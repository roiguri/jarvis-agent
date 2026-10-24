"""Stored files → the content blocks the model sees.

A model message is a list of content blocks: text, or media carrying a file's
bytes. content_blocks() turns file records into the blocks to append to a
message — each readable file's media block followed by its marker, a text file's
marker followed by its first page, or the marker with an honest note when the
file can't be shown (wrong kind, unsupported format, too large, no longer
stored). The same blocks serve a user message and a tool result (ToolFiles).
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

from files import store

logger = logging.getLogger(__name__)

# Gemini's documented ceiling for inline media. The API also recommends the
# Files API past a ~20 MB request, but that is a recommendation, not a limit —
# a 42 MB video inlines and is read correctly. A backstop, not a policy: every
# channel today caps its own uploads well below this, so it exists to meet a
# future one with an honest note rather than an opaque API failure. It cannot
# see duration, which the API constrains separately (<1 min guideline for
# inline video).
INLINE_MEDIA_MAX_BYTES = 100 * 1024 * 1024

# The formats the model accepts, per kind, transcribed from the API's published
# lists. Which formats of a kind are readable is a model fact, so it lives here in
# one copy. Outside these sets the file gets an honest note instead of an opaque
# API error.
SUPPORTED_MEDIA_MIMES = {
    "image": {"image/png", "image/jpeg", "image/webp", "image/heic", "image/heif"},
    "audio": {"audio/wav", "audio/mp3", "audio/aiff", "audio/aac", "audio/ogg",
              "audio/flac"},
    "video": {"video/mp4", "video/mpeg", "video/mov", "video/avi", "video/x-flv",
              "video/mpg", "video/webm", "video/wmv", "video/3gpp"},
    "document": {"application/pdf"},
}

# Clients and content sniffing name some formats differently from the API (.mov is
# video/quicktime, .mp3 audio/mpeg). Normalize to the documented spelling so the
# check sees a known name and the bytes are sent under one the API expects.
MEDIA_MIME_ALIASES = {
    "video/quicktime": "video/mov",
    "video/x-msvideo": "video/avi",
    "video/x-ms-wmv": "video/wmv",
    "video/3gpp2": "video/3gpp",
    "audio/mpeg": "audio/mp3",
    "audio/mpg": "audio/mp3",
    "audio/x-wav": "audio/wav",
    "audio/x-aiff": "audio/aiff",
    "audio/x-flac": "audio/flac",
    "image/jpg": "image/jpeg",
}


# One page of a text file: what arrives inline, and open_file's default length.
PAGE_CHARS = 8000
MAX_PAGE_CHARS = 20000


@dataclass
class ToolFiles:
    """A tool result that carries stored files: `text` plus each file's blocks.
    Any tool may return one; the files must already be in the store."""
    text: str
    files: list[store.FileRecord] = field(default_factory=list)


def article(word: str) -> str:
    """'a' or 'an' for a word the model will read back to the owner."""
    return "an" if word[:1].lower() in "aeiou" else "a"


def normalize_mime(mime_type: str) -> str:
    """Strip parameters/case from a mime and resolve alternative spellings to the
    names the API publishes. Empty in, empty out."""
    mime = (mime_type or "").split(";")[0].strip().lower()
    return MEDIA_MIME_ALIASES.get(mime, mime)


def content_blocks(records: list[store.FileRecord]) -> list[dict]:
    """The blocks to append to a model message for `records`, in order."""
    out: list[dict] = []
    for rec in records:
        if rec.kind == "text" and rec.state in ("available", "trash"):
            out.append({"type": "text", "text": f"\n{rec.marker()}\n{text_page(rec)}"})
            continue
        media, note = media_block(rec)
        if media is not None:
            out.append(media)
        out.append({"type": "text", "text": f"\n{rec.marker()}" + (f" {note}" if note else "")})
    return out


def text_page(rec: store.FileRecord, start: int = 0, length: int = PAGE_CHARS) -> str:
    """One page of a text file, inside <file content> tags, ending with how to read
    on when there is more."""
    try:
        text = store.read_bytes(rec.id).decode("utf-8", errors="replace")
    except (LookupError, OSError) as e:
        logger.warning("could not read %s: %s", rec.id, e)
        return "— the original could not be read."
    return page(text, rec.id, start, length)


def page(text: str, file_id: str, start: int = 0, length: int = PAGE_CHARS, *,
         tag: str = "file content") -> str:
    """One page of `text` inside <tag> tags, ending with how to read on with
    open_file when there is more."""
    start = max(0, start)
    length = max(1, min(length, MAX_PAGE_CHARS))
    if start >= len(text):
        return f"— it has {len(text):,} characters; nothing from {start:,}."
    end = min(len(text), start + length)
    body = f"<{tag}>\n{text[start:end]}\n</{tag}>"
    if start == 0 and end == len(text):
        return body
    tail = f"Showing characters {start:,}–{end:,} of {len(text):,}."
    if end < len(text):
        tail += f' If you need the rest: open_file("{file_id}", start={end}).'
    return f"{body}\n{tail}"


_BLOCK_TYPE = {"image": "image", "audio": "audio", "video": "video", "document": "file"}


def media_block(rec: store.FileRecord) -> tuple[dict | None, str | None]:
    """(media block, None) for a file the model can read, else (None, note)."""
    if rec.state not in ("available", "trash"):
        return None, "— the original is no longer stored."
    if rec.kind not in SUPPORTED_MEDIA_MIMES:
        what = "this file" if rec.kind == "file" else f"{article(rec.kind)} {rec.kind} file"
        return None, f"— I can't read {what}."
    if rec.size > INLINE_MEDIA_MAX_BYTES:
        logger.info("file too large to inline: %s (%d bytes)", rec.id, rec.size)
        return None, f"— too large for me to read ({rec.size / (1024 * 1024):.0f} MB)."
    mime = normalize_mime(rec.mime_type)
    if mime not in SUPPORTED_MEDIA_MIMES[rec.kind]:
        logger.info("unsupported media format: %s (%s)", mime, rec.id)
        return None, f"— I can't read that format ({mime})."
    try:
        data = store.read_bytes(rec.id)
    except (LookupError, OSError) as e:
        logger.warning("could not read %s: %s", rec.id, e)
        return None, "— the original could not be read."
    return {"type": _BLOCK_TYPE[rec.kind], "base64": base64.b64encode(data).decode("ascii"),
            "mime_type": mime}, None


def is_media_block(block) -> bool:
    """A block carrying a file's bytes, in the shape built here or the older
    ones (image_url data URLs, legacy media blocks) earlier checkpoints may hold."""
    if not isinstance(block, dict):
        return False
    kind = block.get("type")
    if kind in ("image", "audio", "video", "file"):
        return "base64" in block or "data" in block
    if kind == "image_url":
        return (block.get("image_url") or {}).get("url", "").startswith("data:")
    return kind == "media" and "data" in block
