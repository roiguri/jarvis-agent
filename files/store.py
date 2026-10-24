"""The file store: add, read, change the state of, and collect stored files.

Every file that reaches Jarvis — sent by the owner, obtained by a tool, sent by
Jarvis — goes through add() and is known by its id from then on. Nothing outside
this package reads or writes the disk layout.

Write order keeps the index honest: bytes are written before the record that
points at them, and bytes are never deleted on the spot — collect_unused()
removes them only once no record needs them and they have sat unused past a
grace period. Adding and collecting take one process lock, so a collection can
never delete bytes an add of the same content is about to point at. (One
process owns the store: the service. Scripts only read, apart from queueing jobs.)

States:
  available  bytes and records present
  trash      restorable; bytes kept
  expired    bytes released, records kept (a retention policy freed the disk)
  deleted    the owner's delete: bytes and records go, a minimal row stays
  missing    computed, never stored: the record needs bytes that are gone
             (e.g. an older index restored after a collection)
"""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import time
import unicodedata
from dataclasses import dataclass, field

import filetype

from files import blobs, ids, index

MAX_FILE_BYTES = 100 * 1024 * 1024
# Unused bytes younger than this are never collected.
UNUSED_GRACE_S = 10 * 60
# Free space under the files root below which disk_status() reports low.
FREE_FLOOR_BYTES = 2 * 1024**3
CHUNK_CHARS = 1500
# The job kind add() queues: write the file's record (files/worker.py).
RECORD_JOB = "record"
_FILENAME_MAX = 120

_STATES_WITH_BYTES = ("available", "trash")
_TRANSITIONS = {
    "available": {"trash", "expired", "deleted"},
    "trash": {"available", "expired", "deleted"},
    "expired": {"deleted"},
    "deleted": set(),
}

_BYTES_LOCK = threading.Lock()


class FileTooLarge(ValueError):
    pass


class FileUnavailable(LookupError):
    """The file exists but its bytes can't be read; .state says why."""

    def __init__(self, file_id: str, state: str):
        super().__init__(f"{file_id} is {state}")
        self.file_id = file_id
        self.state = state


@dataclass(frozen=True)
class FileRecord:
    id: str
    hash: str
    kind: str
    mime_type: str
    size: int
    filename: str | None
    caption: str | None
    source: str
    origin: dict = field(default_factory=dict)
    turn_id: str | None = None
    created: str = ""
    retention: str = "kept"
    state: str = "available"
    state_changed: str | None = None
    removed_reason: str | None = None

    def marker(self) -> str:
        """The one-line handle history carries: `[file:… · kind · name · size]`."""
        parts = [self.id, self.kind]
        if self.filename:
            parts.append(self.filename)
        parts.append(_human_size(self.size))
        if self.state != "available":
            parts.append(self.state)
        return "[" + " · ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Adding
# ---------------------------------------------------------------------------

def add(data: bytes, *, source: str, filename: str | None = None, caption: str | None = None,
        origin: dict | None = None, turn_id: str | None = None, mime_hint: str | None = None,
        retention: str = "kept", created: str | None = None) -> FileRecord:
    """Store `data` and return its new record. Every call makes a record; identical
    bytes are stored once. `created` defaults to now (an import passes the original
    arrival time). Raises FileTooLarge, or OSError when the disk refuses."""
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_FILE_BYTES:
        raise FileTooLarge(f"{len(data)} bytes is over the {MAX_FILE_BYTES}-byte limit")
    if retention not in ("kept", "temporary"):
        raise ValueError(f"unknown retention {retention!r}")
    name = clean_filename(filename)
    kind, mime = classify(data, mime_hint, name)
    caption = (caption or "").strip() or None
    with _BYTES_LOCK:
        sha = blobs.put(data)
        now = created or index.now_iso()
        with index.write() as con:
            file_id = _insert(con, sha, kind, mime, len(data), name, caption, source,
                              origin or {}, turn_id, now, retention)
            _write_chunks(con, file_id, "meta", _meta_text(name, caption, source, origin))
            con.execute("INSERT INTO jobs (file_id, kind, next_at, created) VALUES (?, ?, ?, ?)",
                        (file_id, RECORD_JOB, index.now_iso(), index.now_iso()))
    return get(file_id)


def _insert(con, sha, kind, mime, size, name, caption, source, origin, turn_id, now, retention):
    for _ in range(5):
        file_id = ids.new_id()
        try:
            con.execute(
                "INSERT INTO files (id, hash, kind, mime_type, size, filename, caption, source,"
                " origin, turn_id, created, retention, state) VALUES"
                " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available')",
                (file_id, sha, kind, mime, size, name, caption, source,
                 json.dumps(origin, ensure_ascii=False), turn_id, now, retention))
            return file_id
        except sqlite3.IntegrityError:
            if not con.execute("SELECT 1 FROM files WHERE id = ?", (file_id,)).fetchone():
                raise
    raise RuntimeError("could not draw an unused file id")


def classify(data: bytes, mime_hint: str | None, filename: str | None) -> tuple[str, str]:
    """(kind, mime type) from the content first; the sender's hint and the filename
    only fill gaps. Kinds: image, audio, video, document (PDF), text, file."""
    hint = (mime_hint or "").split(";")[0].strip().lower()
    by_name = mimetypes.guess_type(filename or "")[0] or ""
    guess = filetype.guess(data)
    if guess:
        mime = guess.mime
        if mime.startswith("video/"):
            # An audio-only mp4/webm (an .m4a voice note) sniffs as video.
            mime = next((m for m in (hint, by_name) if m.startswith("audio/")), mime)
        return _kind_of(mime), mime
    if _looks_like_text(data):
        for m in (hint, by_name):
            if m.startswith("text/") or m in _TEXT_APPLICATION_TYPES:
                return "text", m
        return "text", "text/plain"
    return "file", hint or "application/octet-stream"


_TEXT_APPLICATION_TYPES = {"application/json", "application/xml", "application/javascript",
                           "application/x-sh", "application/x-yaml", "application/toml"}


def _kind_of(mime: str) -> str:
    if mime == "application/pdf":
        return "document"
    for prefix in ("image", "audio", "video"):
        if mime.startswith(prefix + "/"):
            return prefix
    return "file"


def _looks_like_text(data: bytes) -> bool:
    head = data[:8192]
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as e:
        # A multi-byte character cut by the 8 KB window is still text.
        return e.start >= len(head) - 3
    return True


def clean_filename(raw: str | None) -> str | None:
    """A filename safe to put in the prompt: last path part only, no control
    characters, brackets or newlines, length capped."""
    if not raw:
        return None
    name = os.path.basename(raw.replace("\\", "/"))
    name = "".join(" " if unicodedata.category(c).startswith("C") else c for c in name)
    name = name.replace("[", "(").replace("]", ")")
    name = " ".join(name.split())
    if len(name) > _FILENAME_MAX:
        stem, ext = os.path.splitext(name)
        name = stem[: _FILENAME_MAX - len(ext)] + ext if len(ext) < 16 else name[:_FILENAME_MAX]
    return name or None


def _meta_text(name, caption, source, origin) -> str:
    bits = [name or "", caption or "", source]
    bits += [str(v) for v in (origin or {}).values() if isinstance(v, (str, int, float))]
    return "\n".join(b for b in bits if b)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def get(file_id: str) -> FileRecord | None:
    """The record, with `missing` computed; None for an unknown or malformed id."""
    fid = ids.normalize(file_id)
    if fid is None:
        return None
    found = index.rows("SELECT * FROM files WHERE id = ?", (fid,))
    return _record(found[0]) if found else None


def _record(row: dict) -> FileRecord:
    state = row["state"]
    if state in _STATES_WITH_BYTES and not blobs.exists(row["hash"]):
        state = "missing"
    return FileRecord(
        id=row["id"], hash=row["hash"], kind=row["kind"], mime_type=row["mime_type"],
        size=row["size"], filename=row["filename"], caption=row["caption"],
        source=row["source"], origin=json.loads(row["origin"] or "{}"), turn_id=row["turn_id"],
        created=row["created"], retention=row["retention"], state=state,
        state_changed=row["state_changed"], removed_reason=row["removed_reason"])


def read_bytes(file_id: str) -> bytes:
    """The file's bytes. Raises LookupError for an unknown id, FileUnavailable when the
    file is expired, deleted or missing."""
    rec = get(file_id)
    if rec is None:
        raise LookupError(f"no such file: {file_id}")
    if rec.state not in _STATES_WITH_BYTES:
        raise FileUnavailable(rec.id, rec.state)
    try:
        return blobs.read(rec.hash)
    except FileNotFoundError:
        raise FileUnavailable(rec.id, "missing") from None


def sharing_bytes(file_id: str) -> list[FileRecord]:
    """Other records that point at the same bytes and still need them."""
    rec = get(file_id)
    if rec is None:
        return []
    found = index.rows(
        "SELECT * FROM files WHERE hash = ? AND id != ? AND state IN ('available', 'trash')"
        " ORDER BY created", (rec.hash, rec.id))
    return [_record(r) for r in found]


# ---------------------------------------------------------------------------
# Derived content
# ---------------------------------------------------------------------------

def put_derived(file_id: str, kind: str, content: str, *, producer: str, version: str) -> None:
    """Store (or replace) what `producer` made from the file, and index it for search."""
    rec = get(file_id)
    if rec is None:
        raise LookupError(f"no such file: {file_id}")
    if rec.state == "deleted":
        raise FileUnavailable(rec.id, rec.state)
    if kind == "meta":
        raise ValueError("'meta' is reserved")
    with index.write() as con:
        con.execute(
            "INSERT INTO derived (file_id, kind, producer, version, created, content)"
            " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(file_id, kind) DO UPDATE SET"
            " producer = excluded.producer, version = excluded.version,"
            " created = excluded.created, content = excluded.content",
            (rec.id, kind, producer, version, index.now_iso(), content))
        _write_chunks(con, rec.id, kind, content)


def get_derived(file_id: str, kind: str | None = None) -> list[dict]:
    fid = ids.normalize(file_id)
    if fid is None:
        return []
    if kind is None:
        return index.rows("SELECT * FROM derived WHERE file_id = ? ORDER BY kind", (fid,))
    return index.rows("SELECT * FROM derived WHERE file_id = ? AND kind = ?", (fid, kind))


def _write_chunks(con, file_id: str, origin: str, text: str) -> None:
    con.execute("DELETE FROM chunks WHERE file_id = ? AND origin = ?", (file_id, origin))
    for position, piece in chunk(text):
        con.execute("INSERT INTO chunks (file_id, origin, position, text) VALUES (?, ?, ?, ?)",
                    (file_id, origin, position, piece))


def chunk(text: str, size: int = CHUNK_CHARS) -> list[tuple[int, str]]:
    """(character offset, piece) pairs of about `size` chars, cut at whitespace."""
    text = text or ""
    out, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind(" ", start + size // 2, end)
            cut = max(cut, text.rfind("\n", start + size // 2, end))
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append((start, piece))
        start = end
    return out


# ---------------------------------------------------------------------------
# State changes
# ---------------------------------------------------------------------------

def trash(file_id: str) -> FileRecord:
    return _transition(file_id, "trash", reason=None)


def restore(file_id: str) -> FileRecord:
    return _transition(file_id, "available", reason=None)


def expire(file_id: str, reason: str) -> FileRecord:
    """Release the bytes (collected later); the records stay findable."""
    return _transition(file_id, "expired", reason=reason)


def delete(file_id: str, reason: str) -> FileRecord:
    """The owner's delete: bytes (collected later), records, search entries and jobs
    go; a minimal row stays so whatever pointed at the file reads it as removed."""
    return _transition(file_id, "deleted", reason=reason)


def _transition(file_id: str, to: str, *, reason: str | None) -> FileRecord:
    rec = get(file_id)
    if rec is None:
        raise LookupError(f"no such file: {file_id}")
    stored = index.rows("SELECT state FROM files WHERE id = ?", (rec.id,))[0]["state"]
    if to not in _TRANSITIONS[stored]:
        raise ValueError(f"{rec.id} is {stored}; it can't become {to}")
    now = index.now_iso()
    with index.write() as con:
        con.execute("UPDATE files SET state = ?, state_changed = ?, removed_reason = ?"
                    " WHERE id = ?", (to, now, reason, rec.id))
        if to == "deleted":
            con.execute("DELETE FROM derived WHERE file_id = ?", (rec.id,))
            con.execute("DELETE FROM chunks WHERE file_id = ?", (rec.id,))
            con.execute("DELETE FROM jobs WHERE file_id = ?", (rec.id,))
            con.execute("UPDATE files SET filename = NULL, caption = NULL, origin = NULL"
                        " WHERE id = ?", (rec.id,))
    return get(rec.id)


# ---------------------------------------------------------------------------
# Collecting unused bytes
# ---------------------------------------------------------------------------

def collect_unused(grace_s: float = UNUSED_GRACE_S) -> list[str]:
    """Delete bytes no record needs that have been unused for `grace_s`, plus temp
    files a crash left behind. Returns the hashes deleted."""
    removed = []
    with _BYTES_LOCK:
        needed = _needed_hashes()
        cutoff = time.time() - grace_s
        for sha in list(blobs.hashes()):
            if sha in needed:
                continue
            try:
                if blobs.mtime(sha) > cutoff:
                    continue
            except FileNotFoundError:
                continue
            blobs.delete(sha)
            removed.append(sha)
        for leftover in list(blobs.temp_leftovers(max(grace_s, 3600))):
            try:
                os.unlink(leftover)
            except FileNotFoundError:
                pass
    return removed


def _needed_hashes() -> set[str]:
    return {r["hash"] for r in index.rows(
        "SELECT DISTINCT hash FROM files WHERE state IN ('available', 'trash')")}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def disk_status() -> dict:
    """Bytes stored, free space where they live, and whether free space is low."""
    stored = 0
    for sha in blobs.hashes():
        try:
            stored += blobs.size(sha)
        except FileNotFoundError:
            pass
    probe = blobs.root()
    while not os.path.isdir(probe):
        probe = os.path.dirname(probe)
    free = shutil.disk_usage(probe).free
    return {"stored_bytes": stored, "free_bytes": free, "low": free < FREE_FLOOR_BYTES}


def verify(check_hashes: bool = False) -> dict:
    """Read-only consistency report: records whose bytes are gone, bytes whose content
    no longer matches their name (with check_hashes), and bytes no record needs."""
    needed = index.rows("SELECT id, hash FROM files WHERE state IN ('available', 'trash')")
    missing = [r["id"] for r in needed if not blobs.exists(r["hash"])]
    corrupt = []
    if check_hashes:
        for sha in blobs.hashes():
            try:
                if blobs.digest(blobs.read(sha)) != sha:
                    corrupt.append(sha)
            except FileNotFoundError:
                pass
    needed_hashes = {r["hash"] for r in needed}
    unused = [sha for sha in blobs.hashes() if sha not in needed_hashes]
    return {"records": len(index.rows("SELECT id FROM files")), "missing": missing,
            "corrupt": corrupt, "unused": unused, **disk_status()}


# ---------------------------------------------------------------------------
# Legacy cache paths
# ---------------------------------------------------------------------------

def map_legacy_path(path: str, file_id: str) -> None:
    with index.write() as con:
        con.execute("INSERT OR IGNORE INTO legacy_paths (path, file_id) VALUES (?, ?)",
                    (path, file_id))


def by_legacy_path(path: str) -> FileRecord | None:
    """The newest record an old cache path became."""
    found = index.rows(
        "SELECT f.* FROM legacy_paths l JOIN files f ON f.id = l.file_id WHERE l.path = ?"
        " ORDER BY f.created DESC LIMIT 1", (path,))
    return _record(found[0]) if found else None


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024**2:.1f} MB"
