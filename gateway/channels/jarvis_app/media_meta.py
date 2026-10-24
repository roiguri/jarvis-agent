"""Length and size of a voice note or video, read from the file's own headers, so
the app can show a length pill and reserve a video's shape before it downloads.

Two small readers, no decoder: Ogg (Opus or Vorbis) for audio, MP4 / QuickTime
for video. Anything else, or a file they can't read, gives no metadata, and the
upload goes ahead without it.
"""

from __future__ import annotations

import struct


def audio_metadata(payload: bytes) -> dict:
    ms = _ogg_duration_ms(payload)
    return {"duration_ms": ms} if ms else {}


def video_metadata(payload: bytes) -> dict:
    try:
        info = _mp4_info(payload)
    except (struct.error, ValueError, IndexError):
        return {}
    return {k: v for k, v in info.items() if v}


# ---------------------------------------------------------------------------
# Ogg: the last page's granule position counts samples; Opus always at 48 kHz
# (minus its pre-skip), Vorbis at the rate in its first header.
# ---------------------------------------------------------------------------

def _ogg_duration_ms(data: bytes) -> int | None:
    if not data.startswith(b"OggS"):
        return None
    last = data.rfind(b"OggS")
    if last < 0 or last + 14 > len(data):
        return None
    granule = struct.unpack_from("<q", data, last + 6)[0]
    head = data.find(b"OpusHead")
    if 0 <= head < 512 and head + 12 <= len(data):
        samples, rate = granule - struct.unpack_from("<H", data, head + 10)[0], 48000
    else:
        vorbis = data.find(b"\x01vorbis")
        if not 0 <= vorbis < 512 or vorbis + 16 > len(data):
            return None
        samples, rate = granule, struct.unpack_from("<I", data, vorbis + 12)[0]
    if samples <= 0 or rate <= 0:
        return None
    return round(samples * 1000 / rate)


# ---------------------------------------------------------------------------
# MP4 / QuickTime: moov/mvhd holds the length; the video track's tkhd holds its
# size, turned when its matrix rotates it (a phone held upright).
# ---------------------------------------------------------------------------

def _boxes(data: bytes, start: int, end: int):
    """(type, body start, body end) for each box in data[start:end]."""
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack_from(">I4s", data, pos)
        header = 8
        if size == 1:
            size, header = struct.unpack_from(">Q", data, pos + 8)[0], 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _child(data: bytes, start: int, end: int, kind: bytes):
    for k, s, e in _boxes(data, start, end):
        if k == kind:
            return s, e
    return None


def _mp4_info(data: bytes) -> dict:
    moov = _child(data, 0, len(data), b"moov")
    if moov is None:
        return {}
    info: dict = {}
    mvhd = _child(data, *moov, b"mvhd")
    if mvhd:
        s = mvhd[0]
        if data[s] == 1:
            timescale, duration = struct.unpack_from(">IQ", data, s + 20)
        else:
            timescale, duration = struct.unpack_from(">II", data, s + 12)
        if timescale:
            info["duration_ms"] = round(duration * 1000 / timescale)
    for kind, s, e in _boxes(data, *moov):
        if kind != b"trak" or not _is_video(data, s, e):
            continue
        tkhd = _child(data, s, e, b"tkhd")
        if tkhd is None:
            break
        t = tkhd[0]
        matrix_at = t + (52 if data[t] == 1 else 40)
        a, b, _u, c, d = struct.unpack_from(">iiiii", data, matrix_at)
        width, height = (v >> 16 for v in struct.unpack_from(">II", data, matrix_at + 36))
        if a == 0 and d == 0 and b and c:  # turned 90° or 270°
            width, height = height, width
        info["width"], info["height"] = width, height
        break
    return info


def _is_video(data: bytes, start: int, end: int) -> bool:
    mdia = _child(data, start, end, b"mdia")
    hdlr = mdia and _child(data, *mdia, b"hdlr")
    return bool(hdlr) and data[hdlr[0] + 8:hdlr[0] + 12] == b"vide"
