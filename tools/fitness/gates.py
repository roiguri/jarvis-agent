"""The Arbox registration gate: the crossfit task's hourly check, in code.

The check only reads Arbox and compares the registered classes with the last
committed set. The handler syncs the DB and keeps two keyed wakes per class
(a briefing before it, a check-in when it ends); a dropped class cancels them.
Any change — new, moved or dropped — also wakes Jarvis at once with the whole
change, to talk about the week against quota. The first run only records the
starting set, so it schedules per-class wakes but no change wake. What each
wake says is the task's prose in HEARTBEAT.md, not this module.

Arbox has been seen to answer with an empty set by mistake, which would read
as "every class dropped". So an empty fetch while future classes are known is
believed only when the next tick sees it too.
"""

import hashlib
from datetime import datetime, timedelta, timezone

from tools.fitness._db import ISRAEL_TZ
from tools.fitness.classes import _apply_registered, _fetch_registered
from triggers.gates import Cancel, GateResult, Upsert, gate, gate_handler
from triggers.model import Turn

GATE = "arbox_registrations"
BRIEFING_LEAD = timedelta(hours=2)
# Arbox gives only a start time; classes run an hour.
CLASS_LENGTH = timedelta(minutes=60)


def _start(c: dict) -> datetime:
    """The class's start as a UTC instant, so lead times are exact across DST."""
    local = datetime.strptime(f"{c['date']} {c['time']}", "%Y-%m-%d %H:%M").replace(tzinfo=ISRAEL_TZ)
    return local.astimezone(timezone.utc)


def _label(c: dict) -> str:
    return f"{_start(c).astimezone(ISRAEL_TZ).strftime('%a %d.%m %H:%M')} {c['category']}"


@gate(GATE)
def check(state: dict | None) -> GateResult:
    known = (state or {}).get("classes", {})
    registered = _fetch_registered()
    now = datetime.now(timezone.utc)
    current = {
        str(c["id"]): {
            "date": c["date"],
            "time": c["time"],
            "category": (c.get("box_categories") or {}).get("name", "WOD"),
        }
        for c in registered
    }
    # Only classes still ahead matter; one that has started needs no wakes.
    current = {i: c for i, c in current.items() if _start(c) > now}
    if not current and any(_start(c) > now for c in known.values()) and not (state or {}).get("empty_once"):
        return GateResult(fire=False, state={**(state or {}), "empty_once": True},
                          message="empty fetch; waiting for the next tick to confirm")
    added = [i for i in current if i not in known]
    moved = [i for i in current if i in known and known[i] != current[i]]
    # A known class missing from the fetch was dropped only if it hasn't
    # started — a class that already happened just left the window.
    dropped = {i: c for i, c in known.items() if i not in current and _start(c) > now}
    parts = [f"{verb}: {', '.join(_label(cs[i]) for i in ids)}" for verb, ids, cs in (
        ("new", added, current), ("moved", moved, current), ("dropped", list(dropped), known)) if ids]
    return GateResult(
        fire=bool(added or moved or dropped),
        state={"classes": current},
        message="; ".join(parts),
        data={"registered": registered, "current": current,
              "changed": added + moved, "dropped": dropped, "first_run": state is None},
    )


@gate_handler(GATE)
def apply(result: GateResult) -> list:
    _apply_registered(result.data["registered"])
    now = datetime.now(timezone.utc)
    current = result.data["current"]
    followups = []
    for i in result.data["changed"]:
        c, start = current[i], _start(current[i])
        followups.append(Upsert(f"arbox:{i}:brief", max(start - BRIEFING_LEAD, now),
                                Turn(f"Pre-class briefing: {_label(c)}.")))
        followups.append(Upsert(f"arbox:{i}:checkin", start + CLASS_LENGTH,
                                Turn(f"End-of-class check-in: {_label(c)}.")))
    for i in result.data["dropped"]:
        followups.append(Cancel(f"arbox:{i}:"))
    if not result.data["first_run"]:
        # Keyed by the change itself: a retried tick re-applies the same wake
        # instead of adding a second one.
        digest = hashlib.sha1(result.message.encode()).hexdigest()[:10]
        followups.append(Upsert(f"arbox:change:{digest}", now,
                                Turn(f"Workout schedule changed — {result.message}.")))
    return followups
