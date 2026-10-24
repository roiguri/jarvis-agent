"""Written records of stored files: one entry point, one function per file kind.

make(rec) is the only way a record is made. RECORDERS maps a file kind to the
function that reads it, the record kind it writes and that function's version.
Improving how a kind is read (a local PDF reader, OCR, frames from video) means
replacing its function and bumping its version; nothing else changes, and the
version shows which files still carry the older record.

Records are written in English; quoted speech and text stay in their original
language. side_call() is the one-message model call behind every model-made
record, and behind fetch_url's video record.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import config
from files import model_input, store

logger = logging.getLogger(__name__)

TEXT_RECORD_MAX_CHARS = 1_000_000
# The finish reasons that mean the model was done.
NORMAL_FINISH = {"STOP", "FINISH_REASON_UNSPECIFIED", ""}

_ONLY_SECTIONS = "Output only these sections, with no introduction or closing line."

IMAGE_PROMPT = (
    "Make a faithful record of this image, for someone who cannot see it. Not a summary.\n"
    "Shown: what the image shows: the kind of image (photo, screenshot, receipt, document, "
    "menu, sign, chart…), then people, objects, place and what is happening, concretely. "
    "Describe people; never guess who they are.\n"
    "Text: all visible text, verbatim, in its original language, in reading order. Write "
    "'none' if there is none.\n"
    "Write in English, except the quoted text. " + _ONLY_SECTIONS
)

# The video record, shared with fetch_url's reel reader.
VIDEO_INTRO = "Make a faithful record of this video, for someone who will not watch it. Not a summary.\n"
VIDEO_SAID = (
    "Said: everything spoken, word for word, in the original language. If that would run "
    "past about 600 words, instead give a detailed account of what is said, in order, that "
    "keeps every specific detail.\n"
)
VIDEO_ON_SCREEN = "On-screen text: all text that appears, verbatim, in order.\n"
VIDEO_SHOWN = (
    "Shown: what happens, in order. Include a specific detail (number, amount, name, place, "
    "price, time) only if it is said or written in the video. Never estimate or fill in a "
    "missing one; if you judge something from the picture alone, mark it as a visual guess.\n"
)
VIDEO_PROMPT = (
    VIDEO_INTRO + VIDEO_SAID + VIDEO_ON_SCREEN + VIDEO_SHOWN
    + "If the video has no sound, say so. Do not say who posted it. Output only the three "
    "sections, with no introduction or closing line."
)
VIDEO_VERSION = "video-1"
VIDEO_MAX_TOKENS = 4096

AUDIO_PROMPT = (
    "Make a faithful record of this audio.\n"
    "Summary: one sentence in English on what it is about.\n"
    "Transcript: everything said, word for word, in the original language. Mark where the "
    "speaker changes. Put music and other sounds in [brackets]. If nothing is said, say so.\n"
    + _ONLY_SECTIONS
)

DOCUMENT_PROMPT = (
    "Extract the text of this document.\n"
    "Summary: one sentence in English: what kind of document it is and what it is about.\n"
    "Text: the full text, verbatim, in reading order, in its original language. Keep "
    "headings, list items and table rows as plain lines. Read scanned pages from the image. "
    "Describe pictures and charts in [brackets], in English.\n"
    + _ONLY_SECTIONS
)


@dataclass(frozen=True)
class Record:
    kind: str
    content: str
    producer: str
    version: str


@dataclass(frozen=True)
class Recorder:
    """How one file kind is recorded. `read` returns (content, producer)."""
    record_kind: str
    version: str
    read: Callable[[store.FileRecord], tuple[str, str]]


class RecordFailed(RuntimeError):
    """This attempt made no record; a later one may."""


class NoRecord(Exception):
    """This file can't be recorded, now or later (format, size, no bytes)."""


def make(rec: store.FileRecord) -> Record:
    """The record for `rec`. Raises NoRecord when the file can't have one,
    RecordFailed when this attempt failed."""
    recorder = RECORDERS.get(rec.kind)
    if recorder is None:
        raise NoRecord(f"no record is made for {rec.kind} files")
    content, producer = recorder.read(rec)
    return Record(recorder.record_kind, content, producer, recorder.version)


def side_call(parts: list[dict], prompt: str, *, job: str, timeout_s: float,
              max_output_tokens: int, low_resolution: bool = False):
    """One model call on `parts` plus `prompt`, recorded as its own telemetry job.
    Returns (text or None, finish reason, response or None); never raises for a
    failed call — the error is the finish reason."""
    from google.genai.types import MediaResolution
    from langchain_core.messages import HumanMessage
    from langchain_google_genai import ChatGoogleGenerativeAI

    from observability import telemetry

    extra = {"media_resolution": MediaResolution.MEDIA_RESOLUTION_LOW} if low_resolution else {}
    llm = ChatGoogleGenerativeAI(
        model=config.MODEL, temperature=0, max_retries=1, timeout=timeout_s,
        max_output_tokens=max_output_tokens, thinking_level="low", **extra,
    )
    message = HumanMessage(content=[*parts, {"type": "text", "text": prompt}])
    # Its own job row, so a running turn's call count and call indexes stay its own.
    with telemetry.job(job):
        t0 = time.perf_counter()
        try:
            response = llm.invoke([message])
        except Exception as e:
            latency = int((time.perf_counter() - t0) * 1000)
            telemetry.record_llm_call(None, latency_ms=latency, error=f"{type(e).__name__}: {e}")
            return None, f"{type(e).__name__}: {e}", None
        latency = int((time.perf_counter() - t0) * 1000)
        finish = str((response.response_metadata or {}).get("finish_reason") or "")
        telemetry.record_llm_call(response, latency_ms=latency, finish_reason=finish)
    text = response.text.strip() if isinstance(response.text, str) else ""
    return text or None, finish, response


# ---------------------------------------------------------------------------
# One function per file kind
# ---------------------------------------------------------------------------

def _by_model(prompt: str, *, max_output_tokens: int, timeout_s: float,
              low_resolution: bool = False) -> Callable[[store.FileRecord], tuple[str, str]]:
    def read(rec: store.FileRecord) -> tuple[str, str]:
        block, note = model_input.media_block(rec)
        if block is None:
            raise NoRecord(note or "the file can't be read")
        text, finish, _ = side_call([block], prompt, job="file_record", timeout_s=timeout_s,
                                    max_output_tokens=max_output_tokens,
                                    low_resolution=low_resolution)
        if text is None:
            raise RecordFailed(finish or "empty record")
        if finish not in NORMAL_FINISH:
            text += f"\n[The record stops early ({finish}); the rest of the file is missing.]"
        return text, config.MODEL
    return read


def read_text(rec: store.FileRecord) -> tuple[str, str]:
    try:
        text = store.read_bytes(rec.id).decode("utf-8", errors="replace")
    except store.FileUnavailable as e:
        raise NoRecord(str(e)) from None
    if len(text) > TEXT_RECORD_MAX_CHARS:
        text = text[:TEXT_RECORD_MAX_CHARS] + "\n[Cut: only the start of the file is kept here.]"
    return text, "file"


RECORDERS: dict[str, Recorder] = {
    "image": Recorder("description", "image-1",
                      _by_model(IMAGE_PROMPT, max_output_tokens=4096, timeout_s=120)),
    "video": Recorder("description", VIDEO_VERSION,
                      _by_model(VIDEO_PROMPT, max_output_tokens=VIDEO_MAX_TOKENS, timeout_s=180,
                                low_resolution=True)),
    "audio": Recorder("transcript", "audio-1",
                      _by_model(AUDIO_PROMPT, max_output_tokens=16384, timeout_s=180)),
    "document": Recorder("text", "document-1",
                         _by_model(DOCUMENT_PROMPT, max_output_tokens=32768, timeout_s=300)),
    "text": Recorder("text", "text-1", read_text),
}
