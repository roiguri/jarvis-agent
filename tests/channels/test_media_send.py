"""Channels send every file kind: Telegram as the message type that fits it, the app
as an upload under the file's own name."""
import asyncio

PDF = b"%PDF-1.4\n"
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64


def test_telegram_sends_each_kind_as_its_own_message_type():
    from gateway.channels.telegram.channel import TelegramChannel

    calls = []

    class Bot:
        def __getattr__(self, method):
            async def send(**kw):
                assert kw["write_timeout"] >= 10 and kw["read_timeout"] == kw["write_timeout"]
                calls.append((method, sorted(k for k in kw if k not in ("chat_id", "caption",
                                                                        "parse_mode",
                                                                        "write_timeout",
                                                                        "read_timeout"))))
            return send

    chan = TelegramChannel(owner_id=7)
    chan.attach(Bot())
    for kind, mime in [("image", "image/jpeg"), ("video", "video/mp4"), ("audio", "audio/ogg"),
                       ("audio", "audio/mp3"), ("document", "application/pdf"), ("file", None)]:
        asyncio.run(chan.send_to_owner_media(kind, b"x", "c", filename="a.bin", mime_type=mime))
    assert calls == [("send_photo", ["photo"]), ("send_video", ["filename", "video"]),
                     ("send_voice", ["filename", "voice"]), ("send_audio", ["audio", "filename"]),
                     ("send_document", ["document", "filename"]),
                     ("send_document", ["document", "filename"])]


def test_the_app_uploads_any_file_under_its_own_name():
    from gateway.channels.jarvis_app.channel import _upload_meta

    assert _upload_meta("document", PDF, "lease.pdf", "application/pdf") == ("lease.pdf", "application/pdf")
    assert _upload_meta("video", b"x", None, "video/mp4") == ("file", "video/mp4")
    assert _upload_meta("image", JPEG) == ("image.jpg", "image/jpeg")
    assert _upload_meta("audio", b"x", "v.ogg", "audio/ogg") == ("v.ogg", "audio/ogg")


# --- length and size for the app ---------------------------------------------------

import struct  # noqa: E402


def _box(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(body), kind) + body


def ogg_opus(seconds: float, pre_skip=312) -> bytes:
    first = b"OggS\x00\x02" + struct.pack("<q", 0) + b"\x00" * 13 + b"OpusHead\x01\x01" + \
        struct.pack("<H", pre_skip) + b"\x00" * 8
    last = b"OggS\x00\x04" + struct.pack("<q", int(seconds * 48000) + pre_skip) + b"\x00" * 13
    return first + b"\x00" * 100 + last


def mp4(seconds: float, width: int, height: int, *, upright: bool) -> bytes:
    mvhd = _box(b"mvhd", b"\x00" * 12 + struct.pack(">II", 1000, int(seconds * 1000)) + b"\x00" * 80)
    matrix = (0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000) if upright else \
        (0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd = _box(b"tkhd", b"\x00" * 40 + struct.pack(">9i", *matrix) +
                struct.pack(">II", width << 16, height << 16))
    hdlr = _box(b"hdlr", b"\x00" * 8 + b"vide" + b"\x00" * 12)
    sound = _box(b"trak", _box(b"mdia", _box(b"hdlr", b"\x00" * 8 + b"soun" + b"\x00" * 12)))
    video = _box(b"trak", tkhd + _box(b"mdia", hdlr))
    return _box(b"ftyp", b"isom" + b"\x00" * 4) + _box(b"mdat", b"\x00" * 50) + \
        _box(b"moov", mvhd + sound + video)


def test_a_voice_note_and_a_video_carry_their_length_and_size():
    from gateway.channels.jarvis_app.media_meta import audio_metadata, video_metadata

    assert audio_metadata(ogg_opus(3)) == {"duration_ms": 3000}
    assert video_metadata(mp4(5, 1920, 1080, upright=True)) == \
        {"duration_ms": 5000, "width": 1080, "height": 1920}
    assert video_metadata(mp4(2.5, 1280, 720, upright=False))["width"] == 1280
    assert audio_metadata(b"ID3 not ogg") == {} and video_metadata(b"\x00" * 30) == {}
    assert video_metadata(_box(b"moov", b"\xff" * 20)) == {}, "a broken file gives nothing"


def test_the_app_upload_carries_them():
    from gateway.channels.jarvis_app.channel import JarvisAppChannel

    uploads = []

    class Hub:
        async def upload_attachment(self, payload, **kw):
            uploads.append(kw)
            return "att_" + "0" * 26

        async def send_message(self, body):
            return {"id": 1}

    chan = JarvisAppChannel(Hub(), owner_id="owner")
    asyncio.run(chan.send_to_owner_media("audio", ogg_opus(4), None, mime_type="audio/ogg"))
    asyncio.run(chan.send_to_owner_media("video", mp4(6, 1920, 1080, upright=False), None,
                                         filename="clip.mp4", mime_type="video/mp4"))
    assert uploads[0]["duration_ms"] == 4000
    assert (uploads[1]["duration_ms"], uploads[1]["width"], uploads[1]["height"],
            uploads[1]["filename"]) == (6000, 1920, 1080, "clip.mp4")


def test_a_large_upload_gets_time_and_a_timeout_says_so():
    import httpx
    import pytest

    from gateway.base import media_send_wait_s, media_upload_timeout_s
    from gateway.channels.jarvis_app.client import HubClient, HubUnavailable

    big = 21 * 1024 * 1024
    assert 90 < media_upload_timeout_s(big) < 100 < media_send_wait_s(big)
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        raise httpx.WriteTimeout("", request=request)

    client = HubClient("http://hub", "t")
    client._client = httpx.AsyncClient(base_url="http://hub", transport=httpx.MockTransport(handler))
    with pytest.raises(HubUnavailable, match=r"the upload timed out after 50s \(10.0 MB\)"):
        asyncio.run(client.upload_attachment(b"x" * 10 * 1024 * 1024, filename="v.mp4",
                                             mime_type="video/mp4"))
    assert seen[0]["write"] == 50.0 and seen[0]["connect"] == 10.0


def test_telegram_sends_an_image_it_cant_take_as_a_photo_as_a_document():
    from gateway.channels.telegram.channel import _send_method

    assert _send_method("image", "image/jpeg", 1000)[0] == "send_photo"
    assert _send_method("image", "image/heic", 1000)[0] == "send_document"
    assert _send_method("image", "image/png", 11 * 1024 * 1024)[0] == "send_document"
