"""Finding stored files, rendered for the model.

Searches what is indexed for each file: its name, the message it came with, its
source, and its records. The index is trigram-tokenized, so a word matches
inside a longer one; all words must match first, then any word. With no words,
the newest files. One entry per file, with its best-matching snippet. Deleted
files have nothing indexed, so they never match; expired ones match by their
record and say so in their marker.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from episodes.search import parse_bound
from files import index, store
from timeutils import ISRAEL_TZ

KINDS = ("image", "video", "audio", "document", "text", "file")
MAX_LIMIT = 30
_OUTPUT_CAP = 8000
_SNIPPET_CHARS = 240
_ORIGIN_LABEL = {"meta": "name or message", "description": "description",
                 "transcript": "transcript", "text": "text"}


def match_expr(query: str, any_term: bool) -> str | None:
    """An FTS expression for the query's words; the trigram index can't match a
    word under 3 characters, so those are dropped (find() falls back to a
    substring match when that leaves none)."""
    terms = [t for t in re.findall(r"\w+", query or "") if len(t) >= 3]
    if not terms:
        return None
    return (" OR " if any_term else " AND ").join(f'"{t}"' for t in terms)


def find(query: str = "", kind: str = "", source: str = "", since: str | None = None,
         until: str | None = None, limit: int = 10) -> str:
    kind = (kind or "").strip().lower()
    if kind and kind not in KINDS:
        return f"Unknown kind {kind!r}. Use one of: {', '.join(KINDS)}."
    since_dt, err = parse_bound(since)
    if err:
        return err
    until_dt, err = parse_bound(until, end=True)
    if err:
        return err
    limit = max(1, min(int(limit or 10), MAX_LIMIT))

    where, params = ["f.state != 'deleted'"], []
    if kind:
        where.append("f.kind = ?"), params.append(kind)
    if (source or "").strip():
        where.append("f.source = ?"), params.append(source.strip().lower())
    if since_dt:
        where.append("f.created >= ?"), params.append(since_dt.astimezone(timezone.utc).isoformat())
    if until_dt:
        where.append("f.created < ?"), params.append(until_dt.astimezone(timezone.utc).isoformat())
    filters = " AND ".join(where)

    words = [t for t in re.findall(r"\w+", query or "") if len(t) >= 3]
    short = [] if words else re.findall(r"\w+", query or "")

    best: dict[str, dict] = {}
    if short:
        # Too short for the index: a plain substring match, every word.
        likes = " AND ".join("c.text LIKE ?" for _ in short)
        for hit in index.rows(
                "SELECT c.file_id, c.origin, c.position, c.text FROM chunks c"
                f" JOIN files f ON f.id = c.file_id WHERE {likes} AND {filters}"
                " ORDER BY f.created DESC LIMIT 300", (*[f"%{w}%" for w in short], *params)):
            if hit["file_id"] not in best and len(best) < limit:
                best[hit["file_id"]] = hit
        words = short
    elif not words:
        for row in index.rows(f"SELECT f.id AS file_id FROM files f WHERE {filters}"
                              " ORDER BY f.created DESC LIMIT ?", (*params, limit)):
            best[row["file_id"]] = {}
    else:
        for any_term in (False, True):
            hits = index.rows(
                "SELECT c.file_id, c.origin, c.position, c.text FROM chunks_fts"
                " JOIN chunks c ON c.id = chunks_fts.rowid JOIN files f ON f.id = c.file_id"
                f" WHERE chunks_fts MATCH ? AND {filters}"
                " ORDER BY bm25(chunks_fts), f.created DESC LIMIT 300",
                (match_expr(query, any_term), *params))
            for hit in hits:
                if hit["file_id"] not in best and len(best) < limit:
                    best[hit["file_id"]] = hit
            if best:
                break
    if not best:
        return "No files found." + (" Try other words or a wider date range." if words else "")

    out, used = [], 0
    for shown, (file_id, hit) in enumerate(best.items()):
        rec = store.get(file_id)
        if rec is None:
            continue
        entry = _entry(rec, hit, words)
        if used + len(entry) > _OUTPUT_CAP and out:
            out.append(f"({len(best) - shown} more files not shown; narrow the search.)")
            break
        out.append(entry)
        used += len(entry)
    return "\n\n".join(out)


def _entry(rec: store.FileRecord, hit: dict, words: list[str]) -> str:
    when = datetime.fromisoformat(rec.created).astimezone(ISRAEL_TZ).strftime("%Y-%m-%d %H:%M")
    origin = rec.origin_text()
    lines = [f"{rec.marker()} — {when}, {rec.source}" + (f", {origin}" if origin else "")]
    if rec.caption:
        lines.append(f"  With the message: {_flat(rec.caption, 200)}")
    if hit and hit["origin"] != "meta":
        at, snippet = _snippet(hit["text"], words)
        lines.append(f"  Match in its {_ORIGIN_LABEL.get(hit['origin'], hit['origin'])}: {snippet}")
        if rec.kind == "text" and hit["origin"] == "text" and rec.state in ("available", "trash"):
            start = max(0, hit["position"] + at - 200)
            lines.append(f'  Read there: open_file("{rec.id}", start={start})')
    else:
        derived = store.get_derived(rec.id)
        if derived:
            lines.append(f"  Its {derived[0]['kind']}: {_flat(derived[0]['content'], _SNIPPET_CHARS // 2)}")
    return "\n".join(lines)


def _snippet(text: str, words: list[str]) -> tuple[int, str]:
    """(offset of the first matching word, a snippet around it)."""
    low = text.lower()
    found = [i for i in (low.find(w.lower()) for w in words) if i >= 0]
    at = min(found) if found else 0
    start = max(0, at - _SNIPPET_CHARS // 3)
    piece = _flat(text[start:start + _SNIPPET_CHARS], _SNIPPET_CHARS)
    return at, ("…" if start else "") + piece + ("…" if start + _SNIPPET_CHARS < len(text) else "")


def _flat(text: str, cap: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= cap else text[: cap - 1] + "…"
