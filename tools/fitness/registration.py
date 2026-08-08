"""Booking and cancelling a place in an Arbox class — the skill's only write
path to the gym.

Two properties matter more here than anywhere else in the skill, because a
mistake books or drops a real seat at a real gym:

1. **The model never supplies an identifier.** It names a class the way a person
   would — date, time, and which track — and `_resolve_class` turns that into
   the one matching schedule row from a freshly fetched schedule. The
   `schedule_id` sent to Arbox, and the `schedule_user_id` used to cancel, come
   only from that fetch. A hallucinated id cannot reach the API because no
   argument carries one.
2. **The confirmation describes the class the API will actually act on.** The
   prompt is rendered from the resolved row — coach, hall, occupancy, and the
   cancellation deadline — not from anything the model wrote. Approving it is
   therefore approving a specific seat, not a sentence about one.

Every precondition is checked against a read before the owner is asked, so a
request that Arbox would reject dies without a prompt. The write itself fires
only from `_exec_registration`, after approval, and re-verifies first: up to
five minutes can pass between the prompt and the tap, and a class can fill in
that time.

Late cancellation is computed locally. Each class carries
`disable_cancellation_time` (hours before the start when cancelling closes) and
`enable_late_cancellation`, which is all the deadline needs — so the gym's
`checkLateCancel` endpoint, whose side effects have never been observed, is
never called.
"""

import os
import re
from datetime import datetime, timedelta, timezone

from langchain_core.tools import tool

from tools.fitness._arbox import _arbox_post
from tools.fitness._db import ISRAEL_TZ, _valid_date
from tools.fitness.classes import _fetch_schedule, _sync_registered_classes
from tools.registry import tool_register

# What Arbox says a booking call would *do*, rather than what free/has_spots
# imply — the two disagree on real rows. Only this value is a real booking; any
# other means the seat is not directly bookable right now.
_BOOKABLE = "insertScheduleUser"
_WAITLIST = "insertStandby"

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class _ClassLookupError(Exception):
    """A refusal phrased for the model: what was wrong, and where the fix is a
    different argument, which ones would work."""


def _class_start(cls: dict) -> datetime:
    """The class's start as an aware Israel-time datetime."""
    return datetime.strptime(
        f"{cls['date']} {cls['time'][:5]}", "%Y-%m-%d %H:%M"
    ).replace(tzinfo=ISRAEL_TZ)


def _category_of(cls: dict) -> str:
    return (cls.get("box_categories") or {}).get("name") or ""


def _cancel_deadline(cls: dict) -> tuple[datetime | None, int | None]:
    """When free cancellation closes, and how many hours before the start that
    is. (None, None) if the class does not declare a limit."""
    hours = cls.get("disable_cancellation_time")
    if hours is None:
        return None, None
    return _class_start(cls) - timedelta(hours=int(hours)), int(hours)


def _describe_class(cls: dict) -> str:
    """The identity block — everything that pins down *which* class, so the
    owner is approving a specific seat rather than a description of one."""
    start = _class_start(cls)
    coach = (cls.get("coach") or {}).get("full_name") or "—"
    space = (cls.get("spaces") or {}).get("name") or ""
    location = (cls.get("locations_box") or {}).get("location") or ""
    where = " · ".join(x for x in (location, space) if x)

    lines = [
        f"  {_category_of(cls) or '(uncategorised)'}",
        f"  {start:%A %-d %b}, {cls['time'][:5]}–{(cls.get('end_time') or '')[:5]}",
        f"  Coach: {coach}",
    ]
    if where:
        lines.append(f"  {where}")
    lines.append(
        f"  {cls.get('registered')}/{cls.get('max_users')} taken "
        f"({cls.get('free')} free)"
    )
    return "\n".join(lines)


def _fetch_day(date: str) -> list[dict]:
    """Every class on `date`. Fetched from now so a same-day class is included;
    the window runs a day long to stay clear of any timezone edge, and the
    result is filtered back to the requested date."""
    now = datetime.now(timezone.utc)
    end = datetime.strptime(date, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    ) + timedelta(days=1)
    if end < now:
        return []
    return [c for c in _fetch_schedule(now, end) if c.get("date") == date]


def _resolve_class(classes: list[dict], date: str, time: str, category: str | None) -> dict:
    """The single class at `date`/`time` matching `category`.

    Ambiguity is refused rather than guessed: most time slots at this gym hold
    more than one class (a WOD and an OPEN GYM at the same hour is the norm), so
    picking the "obvious" one would book the wrong seat regularly.
    """
    at_slot = [c for c in classes if c.get("date") == date and (c.get("time") or "")[:5] == time]
    if not at_slot:
        times = sorted({(c.get("time") or "")[:5] for c in classes})
        if not times:
            raise _ClassLookupError(
                f"No classes at all on {date}. Check the date, or use "
                f"fetch_weekly_gym_schedule to see what is scheduled."
            )
        raise _ClassLookupError(
            f"No class at {time} on {date}. That day has classes at: {', '.join(times)}."
        )

    if category:
        wanted = category.strip().lower()
        matched = [c for c in at_slot if wanted in _category_of(c).lower()]
        if not matched:
            raise _ClassLookupError(
                f"No '{category}' class at {date} {time}. That slot has: "
                f"{', '.join(_category_of(c) or '(uncategorised)' for c in at_slot)}."
            )
        if len(matched) > 1:
            raise _ClassLookupError(
                f"'{category}' matches more than one class at {date} {time}: "
                f"{', '.join(_category_of(c) for c in matched)}. Pass the full category name."
            )
        return matched[0]

    if len(at_slot) > 1:
        raise _ClassLookupError(
            f"{date} {time} has more than one class: "
            f"{', '.join(_category_of(c) or '(uncategorised)' for c in at_slot)}. "
            f"Pass `category` to say which one."
        )
    return at_slot[0]


def _blocker(action: str, cls: dict, now_il: datetime) -> str | None:
    """Why this action must not be attempted, or None if it may proceed.

    Checked before the owner is prompted, so a request Arbox would reject never
    becomes a button — and checked again after approval, because the class can
    change in between.
    """
    start = _class_start(cls)
    when = f"{cls['date']} {cls['time'][:5]}"

    if (cls.get("status") or "").lower() != "active":
        return f"The {when} class is not active (status: {cls.get('status')!r})."
    if cls.get("past") or start <= now_il:
        return f"The {when} class has already started."

    if action == "register":
        option = cls.get("booking_option")
        if option == _WAITLIST:
            return (
                f"The {when} class is full ({cls.get('registered')}/{cls.get('max_users')}, "
                f"{cls.get('stand_by') or 0} on the waitlist). Booking it would join the "
                f"waitlist rather than reserve a place, which this tool does not do — "
                f"pick another class, or join the waitlist in the gym app."
            )
        if option != _BOOKABLE:
            return (
                f"Arbox does not offer the {when} class for booking right now "
                f"(booking_option: {option!r})."
            )
        horizon = cls.get("enable_registration_time")
        if horizon is not None:
            hours_out = (start - now_il).total_seconds() / 3600
            if hours_out > float(horizon):
                opens = start - timedelta(hours=float(horizon))
                return (
                    f"Registration for the {when} class has not opened yet — it opens "
                    f"{opens:%a %-d %b %H:%M} ({horizon}h before the class)."
                )
        return None

    # cancel
    deadline, hours = _cancel_deadline(cls)
    if deadline is not None and now_il > deadline and not cls.get("enable_late_cancellation"):
        return (
            f"The cancellation window for the {when} class closed at "
            f"{deadline:%a %H:%M} ({hours}h before it starts), and this class does not "
            f"allow late cancellation — Arbox would reject it. Cancelling now needs the "
            f"gym app or a word with the coach."
        )
    return None


def _membership_user_id() -> int:
    raw = os.environ.get("ARBOX_MEMBERSHIP_USER_ID", "").strip()
    if not raw:
        raise RuntimeError(
            "ARBOX_MEMBERSHIP_USER_ID not set in environment — booking a class needs it."
        )
    return int(raw)


def _explain_refusal(err: Exception) -> str:
    """Keep a gym-side rejection from being read as an auth failure.

    A 403 here was reported to the owner as a bare "403 Forbidden", and read
    back as an expired token — sending them to rotate a credential that was
    working. The cause of a 403 is whatever Arbox put in the response body;
    this only rules out the wrong answer.
    """
    msg = str(err)
    if "HTTP 403" not in msg:
        return msg
    return (
        f"{msg}\n\nThe gym rejected the request itself — this is not an auth "
        f"problem, since an expired token returns 401, not 403. The reason, if "
        f"Arbox gave one, is in the response detail above."
    )


def _exec_registration(action: str, schedule_id: int, date: str, time: str) -> str:
    """The write, run on a worker thread only after the owner approves.

    Re-resolves the class first. The prompt can sit for minutes before the tap,
    and a seat can be taken in that window, so the state the confirmation was
    built from is treated as stale by default rather than trusted.
    """
    fresh = [c for c in _fetch_day(date) if int(c["id"]) == schedule_id]
    if not fresh:
        raise RuntimeError(
            f"The {date} {time} class is no longer in the gym's schedule — nothing was changed."
        )
    cls = fresh[0]
    now_il = datetime.now(ISRAEL_TZ)

    if action == "register":
        if cls.get("user_booked") is not None:
            return f"Already registered for the {date} {time} class — nothing to do."
        blocker = _blocker("register", cls, now_il)
        if blocker:
            raise RuntimeError(f"{blocker} Nothing was changed.")
        try:
            _arbox_post(
                "/api/v2/scheduleUser/insert",
                {
                    "schedule_id": schedule_id,
                    "membership_user_id": _membership_user_id(),
                    "extras": {"spot": None},
                },
            )
        except RuntimeError as e:
            raise RuntimeError(_explain_refusal(e)) from e
        done = f"Booked: {_category_of(cls)} on {date} at {time}."
    else:
        booked_id = cls.get("user_booked")
        if booked_id is None:
            return f"Not registered for the {date} {time} class — nothing to do."
        blocker = _blocker("cancel", cls, now_il)
        if blocker:
            raise RuntimeError(f"{blocker} Nothing was changed.")
        deadline, _ = _cancel_deadline(cls)
        try:
            _arbox_post(
                "/api/v2/scheduleUser/delete",
                {
                    "schedule_user_id": int(booked_id),
                    "schedule_id": schedule_id,
                    "late_cancel": bool(deadline is not None and now_il > deadline),
                },
            )
        except RuntimeError as e:
            raise RuntimeError(_explain_refusal(e)) from e
        done = f"Cancelled: {_category_of(cls)} on {date} at {time}."

    # Reconcile through the same path a schedule refresh uses, so the local
    # workouts rows follow from the gym's state rather than from this write.
    try:
        _sync_registered_classes()
    except Exception as e:
        return f"{done} (Local schedule sync failed: {e} — it will self-correct on the next refresh.)"
    return done


@tool_register(namespace="fitness", destructive=True, scopes=("user",))
@tool
def manage_arbox_registration(
    action: str,
    date: str,
    time: str,
    category: str | None = None,
) -> str:
    """Book or cancel the user's place in a gym class on Arbox. Needs approval.

    This does not act immediately: it sends the owner a confirmation describing
    the exact class — coach, hall, how full it is, and the cancellation deadline
    — and books or cancels only if they approve. Say that the request was sent
    and wait; do not repeat the call.

    Name the class the way it appears in `fetch_weekly_gym_schedule`. Most time
    slots hold several classes, so `category` is usually required — without it,
    an ambiguous slot is refused rather than guessed.

    Refuses (without asking the owner) if the class is full, already started,
    beyond the registration window, or past its cancellation deadline. A full
    class is not joined as a waitlist.

    Args:
        action: 'register' to book a place, 'cancel' to give one up.
        date: Class date, 'YYYY-MM-DD'.
        time: Class start time, 'HH:MM' (24h, gym local time).
        category: Which track, e.g. 'WOD' or 'OPEN GYM'. Matched loosely against
            the class category; required whenever the slot has more than one class.
    """
    action = (action or "").strip().lower()
    if action not in ("register", "cancel"):
        return "Error: action must be 'register' or 'cancel'."
    if not _valid_date(date):
        return "Error: date must be 'YYYY-MM-DD'."
    time = (time or "").strip()
    if not _TIME_RE.match(time):
        return "Error: time must be 'HH:MM' in 24-hour form, e.g. '20:00'."

    try:
        classes = _fetch_day(date)
        cls = _resolve_class(classes, date, time, category)
    except _ClassLookupError as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        return f"Error looking up the class: {e}"

    now_il = datetime.now(ISRAEL_TZ)
    booked = cls.get("user_booked") is not None

    # Already in the requested state — a no-op worth reporting, not a write.
    if action == "register" and booked:
        return f"Already registered for this class:\n{_describe_class(cls)}"
    if action == "cancel" and not booked:
        return f"Not registered for this class, so there is nothing to cancel:\n{_describe_class(cls)}"

    blocker = _blocker(action, cls, now_il)
    if blocker:
        return blocker

    deadline, limit_h = _cancel_deadline(cls)
    identity = _describe_class(cls)

    if action == "register":
        description = f"Book this class on Arbox?\n\n{identity}"
        if deadline is not None:
            description += (
                f"\n\nFree cancellation until {deadline:%a %H:%M} ({limit_h}h before)."
            )
        # Neutral headline: the store renders "{ok_text}\n{action's return}", and
        # the action is the only thing that knows what actually happened — it may
        # find the seat already taken, or already booked, when it finally runs.
        ok_text = "Arbox request completed."
        cancel_text = "Nothing was booked."
    else:
        description = f"Cancel this Arbox booking?\n\n{identity}"
        if deadline is not None:
            if now_il > deadline:
                description += (
                    f"\n\n⚠ This is a late cancel — the free window closed at "
                    f"{deadline:%a %H:%M} ({limit_h}h before the class)."
                )
            else:
                description += (
                    f"\n\nStill free to cancel — the window closes {deadline:%a %H:%M} "
                    f"({limit_h}h before)."
                )
        waiting = cls.get("stand_by") or 0
        if waiting:
            description += f"\n{waiting} on the waitlist would get the place."
        ok_text = "Arbox request completed."
        cancel_text = "The booking was kept."

    schedule_id = int(cls["id"])

    from gateway.factory import get_confirmation

    async def _do() -> str:
        import asyncio

        return await asyncio.to_thread(_exec_registration, action, schedule_id, date, time)

    try:
        return get_confirmation().request_confirmation_sync(
            description=description,
            action_fn=_do,
            result_ok_text=ok_text,
            result_cancel_text=cancel_text,
        )
    except Exception as e:
        return f"Error requesting approval: {e}"
