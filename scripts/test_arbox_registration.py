#!/usr/bin/env python3
"""Arbox registration test — exercises the tool against a fake gym, no network.

Runs against a THROWAWAY JARVIS_ROOT, created and deleted per run, so it never
touches a real instance's database. That is why the root is set here, before any
project import: config.py binds every state path at import time (same reason
scripts/ci/check_paths.py sets it first).

NOTHING here reaches Arbox. The schedule fetch is replaced with fixtures, and
`_arbox_post` is replaced with a recorder that stores (path, body) instead of
sending it. The recorder is asserted against directly, so the tests can state
what would have gone on the wire — and, more importantly, that nothing goes on
the wire until the owner has approved.

What this CANNOT establish, by construction: whether Arbox accepts these
payloads, whether `extras.spot` is right for a spot-assigned class, or what the
gym does in response. Only a real booking answers those.

Usage:
    python scripts/test_arbox_registration.py         # run everything
    python scripts/test_arbox_registration.py -v      # show each result
"""

import argparse
import os
import shutil
import sys
import tempfile

_SCRATCH = tempfile.mkdtemp(prefix="jarvis-test-arbox-")
os.makedirs(os.path.join(_SCRATCH, "secrets"), exist_ok=True)
os.environ["JARVIS_ROOT"] = _SCRATCH
os.environ["GOOGLE_API_KEY"] = "dummy"
os.environ["ARBOX_MEMBERSHIP_USER_ID"] = "999001"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio  # noqa: E402
from datetime import datetime, timedelta  # noqa: E402

from tools.fitness import registration as R  # noqa: E402

VERBOSE = False
_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        if VERBOSE:
            print(f"  ok    {label}")
    else:
        _failed += 1
        print(f"  FAIL  {label}")
        if detail:
            print(f"        {detail}")


def section(name):
    print(f"\n{name}")


# ---------------------------------------------------------------------------
# The fake gym
# ---------------------------------------------------------------------------

IL_NOW = datetime.now(R.ISRAEL_TZ)
TOMORROW = (IL_NOW + timedelta(days=1)).strftime("%Y-%m-%d")
FAR = (IL_NOW + timedelta(days=6)).strftime("%Y-%m-%d")
YESTERDAY = (IL_NOW - timedelta(days=1)).strftime("%Y-%m-%d")


def make_class(
    cid, date, time, category, *, booking_option="insertScheduleUser",
    user_booked=None, registered=10, max_users=18, free=8, past=0,
    status="active", stand_by=0, cancel_hours=2, late_cancel_enabled=0,
    horizon=72, end_time=None,
):
    """A class row shaped like the ones the real API returns."""
    hh = int(time[:2])
    return {
        "id": cid,
        "date": date,
        "time": time,
        "end_time": end_time or f"{hh + 1:02d}:{time[3:]}",
        "status": status,
        "past": past,
        "free": free,
        "registered": registered,
        "max_users": max_users,
        "stand_by": stand_by,
        "user_booked": user_booked,
        "user_in_standby": None,
        "booking_option": booking_option,
        "disable_cancellation_time": cancel_hours,
        "enable_late_cancellation": late_cancel_enabled,
        "enable_registration_time": horizon,
        "box_categories": {"name": category},
        "coach": {"full_name": "Test Coach"},
        "spaces": {"name": "HALL A"},
        "locations_box": {"location": "Test Branch"},
    }


# Two classes in the same slot — the normal case at this gym, not an edge case.
WOD_TOMORROW = make_class(1001, TOMORROW, "20:00", "WOD NEVE TZEDEK")
OPEN_TOMORROW = make_class(1002, TOMORROW, "20:00", "OPEN GYM")
FULL_WOD = make_class(1003, TOMORROW, "07:00", "WOD NEVE TZEDEK",
                      booking_option="insertStandby", free=0, registered=18, stand_by=3)
BOOKED_WOD = make_class(1004, TOMORROW, "18:00", "WOD NEVE TZEDEK", user_booked=555001)
BOOKED_SOON = make_class(1005, IL_NOW.strftime("%Y-%m-%d"),
                         (IL_NOW + timedelta(minutes=30)).strftime("%H:%M"),
                         "WOD NEVE TZEDEK", user_booked=555002, cancel_hours=8)
INACTIVE = make_class(1006, TOMORROW, "12:00", "WOD NEVE TZEDEK", status="cancelled")
BEYOND_HORIZON = make_class(1007, FAR, "20:00", "WOD NEVE TZEDEK")
PAST_CLASS = make_class(1008, YESTERDAY, "20:00", "WOD NEVE TZEDEK", past=1)

SCHEDULE = [WOD_TOMORROW, OPEN_TOMORROW, FULL_WOD, BOOKED_WOD, BOOKED_SOON,
            INACTIVE, BEYOND_HORIZON, PAST_CLASS]

_writes = []          # every (path, body) the tool tried to send
_synced = []          # every reconciliation the tool triggered
_schedule = list(SCHEDULE)


def fake_fetch_schedule(from_dt, to_dt):
    return list(_schedule)


def fake_arbox_post(path, body):
    _writes.append((path, body))
    return {"data": {"ok": True}}


def fake_sync():
    _synced.append(True)
    return "synced"


R._fetch_schedule = fake_fetch_schedule
R._arbox_post = fake_arbox_post
R._sync_registered_classes = fake_sync


# ---------------------------------------------------------------------------
# A confirmation store that records instead of prompting
# ---------------------------------------------------------------------------

class FakeConfirmation:
    """Captures what the owner would have been shown, and lets a test decide
    afterwards whether they tapped Confirm or Cancel."""

    def __init__(self):
        self.description = None
        self.action_fn = None
        self.ok_text = None
        self.cancel_text = None
        self.requests = 0

    def request_confirmation_sync(self, description, action_fn,
                                  result_ok_text="", result_cancel_text=""):
        self.requests += 1
        self.description = description
        self.action_fn = action_fn
        self.ok_text = result_ok_text
        self.cancel_text = result_cancel_text
        return f"Confirmation request sent. Awaiting your approval to: {description}"

    def confirm(self):
        """Run the deferred action, the way the store does on a Confirm tap."""
        return asyncio.run(self.action_fn())


CONF = FakeConfirmation()

import gateway.factory as factory  # noqa: E402

factory.get_confirmation = lambda: CONF


def call(action, date, time, category=None):
    return R.manage_arbox_registration.invoke(
        {"action": action, "date": date, "time": time, "category": category}
    )


def reset():
    _writes.clear()
    _synced.clear()
    global _schedule
    _schedule = list(SCHEDULE)
    CONF.__init__()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_input_validation():
    section("input validation")
    reset()
    check("bad action refused", "action must be" in call("book", TOMORROW, "20:00"))
    check("bad date refused", "YYYY-MM-DD" in call("register", "9/8/2026", "20:00"))
    check("bad time refused", "HH:MM" in call("register", TOMORROW, "8pm"))
    check("25:00 refused", "HH:MM" in call("register", TOMORROW, "25:00"))
    check("no confirmation requested", CONF.requests == 0)
    check("no writes", not _writes, str(_writes))


def test_resolution():
    section("resolving a class from date/time/category")
    reset()

    out = call("register", TOMORROW, "20:00")
    check("ambiguous slot refused", "more than one class" in out, out)
    check("  and lists the candidates", "WOD NEVE TZEDEK" in out and "OPEN GYM" in out, out)

    out = call("register", TOMORROW, "05:00", "WOD")
    check("unknown time refused", "No class at 05:00" in out, out)
    check("  and lists that day's times", "20:00" in out, out)

    out = call("register", YESTERDAY, "20:00", "WOD")
    check("past date resolves to nothing bookable",
          "already started" in out or "No class" in out or "no classes" in out.lower(), out)

    out = call("register", TOMORROW, "20:00", "PILATES")
    check("unknown category refused", "No 'PILATES' class" in out, out)
    check("  and lists what is in the slot", "OPEN GYM" in out, out)

    check("nothing was sent during resolution", not _writes, str(_writes))
    check("owner never prompted during resolution", CONF.requests == 0)


def test_register_preconditions():
    section("register preconditions (refused without prompting the owner)")
    reset()

    out = call("register", TOMORROW, "07:00", "WOD")
    check("full class refused", "full" in out, out)
    check("  names the waitlist count", "3 on the waitlist" in out, out)
    check("  does NOT join the waitlist", not _writes, str(_writes))

    out = call("register", FAR, "20:00", "WOD")
    check("beyond registration horizon refused", "has not opened yet" in out, out)

    out = call("register", TOMORROW, "12:00", "WOD")
    check("inactive class refused", "not active" in out, out)

    out = call("register", TOMORROW, "18:00", "WOD")
    check("already registered is a no-op, not a write", "Already registered" in out, out)

    check("no confirmation was ever requested", CONF.requests == 0)
    check("no writes at all", not _writes, str(_writes))


def test_cancel_preconditions():
    section("cancel preconditions")
    reset()

    out = call("cancel", TOMORROW, "20:00", "WOD")
    check("cancelling an unbooked class is a no-op", "nothing to cancel" in out, out)
    check("  no prompt", CONF.requests == 0)

    # BOOKED_SOON starts in 30 min with an 8h cancellation limit -> window closed.
    out = call("cancel", BOOKED_SOON["date"], BOOKED_SOON["time"], "WOD")
    check("past the cancellation deadline is refused", "window for" in out and "closed" in out, out)
    check("  explains late cancellation is not allowed", "late cancellation" in out, out)
    check("  no prompt", CONF.requests == 0)
    check("no writes", not _writes, str(_writes))


def test_register_happy_path():
    section("register: the confirmation gate")
    reset()

    out = call("register", TOMORROW, "20:00", "WOD NEVE TZEDEK")
    check("owner was prompted once", CONF.requests == 1)
    check("tool reports it is waiting", "Awaiting your approval" in out, out)
    check("NOTHING sent before approval", not _writes, str(_writes))

    d = CONF.description
    check("prompt names the category", "WOD NEVE TZEDEK" in d, d)
    check("prompt names the time", "20:00" in d, d)
    check("prompt names the coach", "Test Coach" in d, d)
    check("prompt names the hall", "HALL A" in d, d)
    check("prompt shows occupancy", "10/18" in d, d)
    check("prompt shows the cancellation deadline", "Free cancellation until" in d, d)

    result = CONF.confirm()
    check("exactly one write after approval", len(_writes) == 1, str(_writes))
    path, body = _writes[0]
    check("  correct endpoint", path == "/api/v2/scheduleUser/insert", path)
    check("  schedule_id from the fetched row", body["schedule_id"] == 1001, str(body))
    check("  membership_user_id from env", body["membership_user_id"] == 999001, str(body))
    check("  extras.spot is null", body["extras"] == {"spot": None}, str(body))
    check("  payload has exactly 3 keys", set(body) == {"schedule_id", "membership_user_id", "extras"}, str(body))
    check("local rows reconciled after the write", _synced == [True], str(_synced))
    check("result names what happened", "Booked" in result, result)


def test_register_declined():
    section("register: owner declines")
    reset()
    call("register", TOMORROW, "20:00", "WOD NEVE TZEDEK")
    check("owner was prompted", CONF.requests == 1)
    # The owner taps Cancel: the store simply never calls action_fn.
    check("no write without the tap", not _writes, str(_writes))
    check("no reconciliation either", not _synced, str(_synced))
    check("decline text is set", CONF.cancel_text == "Nothing was booked.", CONF.cancel_text)


def test_cancel_happy_path():
    section("cancel: the confirmation gate")
    reset()

    out = call("cancel", TOMORROW, "18:00", "WOD NEVE TZEDEK")
    check("owner was prompted once", CONF.requests == 1)
    check("nothing sent before approval", not _writes, str(_writes))

    d = CONF.description
    check("prompt says it is a cancellation", "Cancel this Arbox booking?" in d, d)
    check("prompt says the window is still open", "Still free to cancel" in d, d)

    CONF.confirm()
    check("exactly one write after approval", len(_writes) == 1, str(_writes))
    path, body = _writes[0]
    check("  correct endpoint", path == "/api/v2/scheduleUser/delete", path)
    check("  schedule_user_id comes from user_booked", body["schedule_user_id"] == 555001, str(body))
    check("  schedule_id from the fetched row", body["schedule_id"] == 1004, str(body))
    check("  late_cancel is False inside the window", body["late_cancel"] is False, str(body))
    check("  payload has exactly 3 keys",
          set(body) == {"schedule_user_id", "schedule_id", "late_cancel"}, str(body))


def test_waitlist_shown_on_cancel():
    section("cancel: waitlist is surfaced")
    reset()
    global _schedule
    _schedule = [make_class(1004, TOMORROW, "18:00", "WOD NEVE TZEDEK",
                            user_booked=555001, stand_by=2)]
    call("cancel", TOMORROW, "18:00", "WOD")
    check("prompt says someone would take the place",
          "2 on the waitlist would get the place" in CONF.description, CONF.description)


def test_revalidation_between_prompt_and_tap():
    section("re-verification: the world changes before the owner taps")
    global _schedule

    # (a) the class fills up in the meantime
    reset()
    call("register", TOMORROW, "20:00", "WOD NEVE TZEDEK")
    check("prompted", CONF.requests == 1)
    _schedule = [make_class(1001, TOMORROW, "20:00", "WOD NEVE TZEDEK",
                            booking_option="insertStandby", free=0, registered=18, stand_by=1)]
    try:
        CONF.confirm()
        check("filled class raises instead of booking", False, "no exception")
    except Exception as e:
        check("filled class raises instead of booking", "full" in str(e), str(e))
    check("  and sends nothing", not _writes, str(_writes))

    # (b) the class vanishes from the schedule
    reset()
    call("register", TOMORROW, "20:00", "WOD NEVE TZEDEK")
    _schedule = []
    try:
        CONF.confirm()
        check("vanished class raises", False, "no exception")
    except Exception as e:
        check("vanished class raises", "no longer in the gym's schedule" in str(e), str(e))
    check("  and sends nothing", not _writes, str(_writes))

    # (c) it got booked elsewhere in the meantime
    reset()
    call("register", TOMORROW, "20:00", "WOD NEVE TZEDEK")
    _schedule = [make_class(1001, TOMORROW, "20:00", "WOD NEVE TZEDEK", user_booked=777)]
    result = CONF.confirm()
    check("already-booked is reported, not re-booked", "Already registered" in result, result)
    check("  and sends nothing", not _writes, str(_writes))

    # (d) cancelled elsewhere in the meantime
    reset()
    call("cancel", TOMORROW, "18:00", "WOD NEVE TZEDEK")
    _schedule = [make_class(1004, TOMORROW, "18:00", "WOD NEVE TZEDEK", user_booked=None)]
    result = CONF.confirm()
    check("already-cancelled is reported, not re-cancelled", "Not registered" in result, result)
    check("  and sends nothing", not _writes, str(_writes))


def test_scope_and_registration():
    section("registry metadata")
    from tools import registry
    entry = registry._REGISTRY.get("manage_arbox_registration")
    check("tool is registered", entry is not None)
    if entry:
        check("namespace is fitness", entry.namespace == "fitness", entry.namespace)
        check("marked destructive", entry.destructive is True)
        check("bound only on user turns", entry.scopes == ("user",), str(entry.scopes))

    # The real gate: what the runtime would actually bind for a turn.
    user_tools = {t.name for t in registry.get_tools("user", {"fitness"})}
    hb_tools = {t.name for t in registry.get_tools("heartbeat", {"fitness"})}
    check("bound on a user turn with fitness active",
          "manage_arbox_registration" in user_tools)
    check("NOT bound on a heartbeat turn",
          "manage_arbox_registration" not in hb_tools)
    check("  while read tools still are on heartbeat",
          "fetch_upcoming_arbox_classes" in hb_tools)
    check("not bound when fitness is inactive",
          "manage_arbox_registration" not in {t.name for t in registry.get_tools("user", set())})
    check("find() refuses it on a heartbeat turn",
          registry.find("manage_arbox_registration", "heartbeat", {"fitness"}) is None)


def main():
    global VERBOSE
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    VERBOSE = ap.parse_args().verbose

    try:
        test_input_validation()
        test_resolution()
        test_register_preconditions()
        test_cancel_preconditions()
        test_register_happy_path()
        test_register_declined()
        test_cancel_happy_path()
        test_waitlist_shown_on_cancel()
        test_revalidation_between_prompt_and_tap()
        test_scope_and_registration()

        print(f"\n{_passed} passed, {_failed} failed")
        return 1 if _failed else 0
    finally:
        shutil.rmtree(_SCRATCH, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
