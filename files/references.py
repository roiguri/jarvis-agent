"""Which file ids are in use, and by what — computed, never registered per file.

Each place that can hold a file id registers one function returning the ids it
holds right now:

    @references.holder("collections", strength="strong")
    def _collection_files() -> Iterable[str]: ...

Strong holders (collections, reminders, owner-curated memory) keep a file alive.
Weak holders (history, daily logs, heartbeat notes) only mention it: history is
kept forever, so counting it would keep every file forever.

Answers are computed fresh from the holders' own data, so they can't drift. A
holder that fails makes the whole scan fail: nothing may be cleaned up on a
partial answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from files import ids, index, store

STRENGTHS = ("strong", "weak")
SWEEP_GRACE = timedelta(days=3)


@dataclass(frozen=True)
class Holder:
    name: str
    strength: str
    list_ids: Callable[[], Iterable[str]]


_HOLDERS: dict[str, Holder] = {}


class ReferenceScanFailed(RuntimeError):
    pass


def holder(name: str, *, strength: str):
    """Register `fn() -> iterable of ids` as the holder `name`."""
    if strength not in STRENGTHS:
        raise ValueError(f"strength must be one of {STRENGTHS}")

    def register(fn: Callable[[], Iterable[str]]):
        if name in _HOLDERS and _HOLDERS[name].list_ids is not fn:
            raise ValueError(f"holder {name!r} is already registered")
        _HOLDERS[name] = Holder(name, strength, fn)
        return fn
    return register


def holders() -> list[Holder]:
    return list(_HOLDERS.values())


def scan() -> dict[str, list[Holder]]:
    """id → the holders using it. Raises ReferenceScanFailed if any holder fails."""
    used: dict[str, list[Holder]] = {}
    for h in holders():
        try:
            found = {ids.normalize(i) for i in h.list_ids()}
        except Exception as e:
            raise ReferenceScanFailed(f"holder {h.name!r} failed: {e}") from e
        for fid in found - {None}:
            used.setdefault(fid, []).append(h)
    return used


def where_used(file_id: str) -> list[Holder]:
    fid = ids.normalize(file_id)
    return scan().get(fid, []) if fid else []


def sweep_candidates(eligible: Callable[[store.FileRecord], bool],
                     *, grace: timedelta = SWEEP_GRACE) -> list[store.FileRecord]:
    """What a retention policy would remove — a report; nothing is changed.

    A file qualifies when the policy says so, it is available, it is older than
    `grace`, and no strong holder uses it. Raises ReferenceScanFailed rather than
    report on a partial scan."""
    used = scan()
    cutoff = datetime.now(timezone.utc) - grace
    out = []
    for row in index.rows("SELECT id FROM files WHERE state = 'available' ORDER BY created"):
        rec = store.get(row["id"])
        if rec is None or rec.state != "available":
            continue
        if datetime.fromisoformat(rec.created) > cutoff:
            continue
        if any(h.strength == "strong" for h in used.get(rec.id, [])):
            continue
        if eligible(rec):
            out.append(rec)
    return out
