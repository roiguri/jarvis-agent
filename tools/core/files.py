from datetime import datetime

from langchain_core.tools import tool

from files import model_input, store
from timeutils import ISRAEL_TZ
from tools.registry import tool_register

_GONE = {
    "expired": "The original is no longer stored (it expired).",
    "deleted": "The original was deleted.",
    "missing": "The original is missing from storage.",
}


# While the original is stored the file itself is shown, so its records are a preview.
RECORD_PREVIEW_CHARS = 1500


def _about(rec: store.FileRecord) -> str:
    when = datetime.fromisoformat(rec.created).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")
    origin = rec.origin_text()
    lines = [f"Stored {when} (Israel time), source: {rec.source}"
             + (f", {origin}." if origin else ".")]
    if rec.caption:
        lines.append(f"With the message: {rec.caption}")
    return "\n".join(lines)


def _record_previews(rec: store.FileRecord) -> str:
    lines = []
    for derived in store.get_derived(rec.id):
        if rec.kind == "text" and derived["kind"] == "text":
            continue  # the page below is the same text
        content = derived["content"]
        if len(content) > RECORD_PREVIEW_CHARS:
            content = (content[:RECORD_PREVIEW_CHARS]
                       + f" […cut; {len(content):,} characters in all]")
        lines.append(f"Record ({derived['kind']}): {content}")
    return "\n".join(lines)


def _records_text(rec: store.FileRecord) -> str:
    return "\n\n".join(f"Record ({d['kind']}):\n{d['content']}"
                        for d in store.get_derived(rec.id))


@tool_register(namespace="core")
@tool
def open_file(file_id: str, start: int = 0, length: int = model_input.PAGE_CHARS):
    """Open a stored file by its id (the `file:…` in a marker such as
    `[file:k7m2q9xd · image · photo.jpg · 1.2 MB]`). Use it only if necessary: when
    a question needs the file's contents and your earlier reply about it isn't
    enough. You see the file in your next step only; open it again if you need it
    later.

    Images, audio, video and PDFs come back whole, with their written record. A text
    file, or the record of a file that is no longer stored, comes back one page at a
    time: the result says which characters it shows, and if you need more, call
    again with `start` where it stopped. `length` is the page size in characters
    (up to 20,000).
    """
    rec = store.get(file_id)
    if rec is None:
        return f"No file with id {file_id!r}."
    about = _about(rec)
    if rec.state in _GONE:
        head = f"{rec.marker()}\n{about}\n{_GONE[rec.state]}"
        written = _records_text(rec)
        if not written:
            return head
        return (f"{head} What was recorded of it:\n"
                f"{model_input.page(written, rec.id, start, length, tag='file record')}")
    about = "\n".join(p for p in (about, _record_previews(rec)) if p)
    if rec.kind == "text":
        return f"{rec.marker()}\n{about}\n{model_input.text_page(rec, start, length)}"
    # The file's own blocks end with its marker.
    return model_input.ToolFiles(text=about, files=[rec])
