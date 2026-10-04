"""Day and week boundaries: which clock each surface reads (Israel for
home-anchored code, the owner's for what travels with them) and where its day
starts — around midnight, across daylight-saving switches, and away from home."""

import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
from hypothesis import given
from hypothesis import strategies as st

import agent
import pending_mirrors
import timeutils
from tests.conftest import LOG_DIR
from tests.fakes import frozen_datetime, frozen_datetime_module
from timeutils import ISRAEL_TZ
from tools.core.history import get_chat_history
from tools.google_health import google_health_tools as health

UTC = timezone.utc
NEW_YORK = "America/New_York"


@pytest.fixture
def owner_home():
    """Starts and ends the test with the owner at home (no away timezone)."""
    timeutils.clear_owner_tz()
    yield
    timeutils.clear_owner_tz()


# --- Sunday-anchored weeks ------------------------------------------------------

@pytest.mark.parametrize("anchor, week", [
    (datetime(2026, 3, 8, 0, 0, tzinfo=ISRAEL_TZ), ("2026-03-08", "2026-03-14")),     # Sunday 00:00
    (datetime(2026, 3, 14, 23, 59, tzinfo=ISRAEL_TZ), ("2026-03-08", "2026-03-14")),  # Saturday 23:59
    # Saturday 22:30 UTC is already Sunday 00:30 in Israel (UTC+2): the next week.
    (datetime(2026, 3, 14, 22, 30, tzinfo=UTC), ("2026-03-15", "2026-03-21")),
    # Israel's summer time starts Friday 2026-03-27; the week holds together across it.
    (datetime(2026, 3, 28, 12, 0, tzinfo=ISRAEL_TZ), ("2026-03-22", "2026-03-28")),
    (datetime(2026, 3, 29, 1, 0, tzinfo=ISRAEL_TZ), ("2026-03-29", "2026-04-04")),
    # ...and ends Sunday 2026-10-25.
    (datetime(2026, 10, 25, 0, 30, tzinfo=ISRAEL_TZ), ("2026-10-25", "2026-10-31")),
    (datetime(2026, 10, 24, 21, 30, tzinfo=UTC), ("2026-10-25", "2026-10-31")),       # 00:30 IDT
])
def test_week_bounds(anchor, week):
    assert timeutils.israel_week_bounds(anchor) == week


@given(st.datetimes(min_value=datetime(2024, 1, 1), max_value=datetime(2030, 1, 1), timezones=st.just(UTC)))
def test_week_bounds_contain_their_anchor(anchor):
    start, end = (date.fromisoformat(d) for d in timeutils.israel_week_bounds(anchor))
    local_day = anchor.astimezone(ISRAEL_TZ).date()
    assert start.weekday() == 6, "starts on a Sunday"
    assert end - start == timedelta(days=6)
    assert start <= local_day <= end


# --- get_chat_history's `since` -------------------------------------------------

def seed_chat(*stamps):
    with open(os.path.join(LOG_DIR, "chat_history.jsonl"), "w", encoding="utf-8") as f:
        for ts in stamps:
            f.write(json.dumps({"ts": ts, "thread_id": "owner", "role": "user", "content": f"at {ts}"}) + "\n")


def history(since):
    return get_chat_history.invoke({"since": since, "limit": 50})


@pytest.mark.parametrize("day, last_of_previous, first_of_day", [
    # Winter: Israel is UTC+2, so the day starts at 22:00 UTC the evening before.
    ("2026-01-15", "2026-01-14T21:59:00+00:00", "2026-01-14T22:00:00+00:00"),
    # Summer: UTC+3, so 21:00 UTC. A fixed +03:00 was right only half the year.
    ("2026-07-15", "2026-07-14T20:59:00+00:00", "2026-07-14T21:00:00+00:00"),
])
def test_since_a_date_starts_at_israel_midnight(day, last_of_previous, first_of_day):
    seed_chat(last_of_previous, first_of_day)
    out = history(day)
    assert f"at {first_of_day}" in out
    assert f"at {last_of_previous}" not in out


def test_since_a_timestamp_cuts_exactly():
    seed_chat("2026-01-15T09:59:59+00:00", "2026-01-15T10:00:00+00:00")
    out = history("2026-01-15T10:00:00Z")
    assert "at 2026-01-15T10:00:00+00:00" in out and "09:59:59" not in out


def test_since_without_an_offset_is_refused():
    seed_chat("2026-01-15T10:00:00+00:00")
    assert history("2026-01-15T10:00:00").startswith("Ambiguous 'since'")


def test_since_garbage_is_refused():
    assert history("last tuesday").startswith("Invalid 'since'")


# --- the pending-mirror window ---------------------------------------------------

def test_mirror_window_is_a_rolling_24h_not_the_israel_day(monkeypatch):
    """At 01:00 Israel, a send from 23:00 the evening before is still pending —
    an Israel-day floor would have dropped it unseen."""
    now = datetime(2026, 3, 10, 1, 0, tzinfo=ISRAEL_TZ)
    monkeypatch.setattr(pending_mirrors, "_dt", frozen_datetime_module(now))
    rows = [
        (now - timedelta(hours=25), "older than a day"),
        (now - timedelta(hours=2), "late last night"),
    ]
    with open(os.path.join(LOG_DIR, "notifications.jsonl"), "w", encoding="utf-8") as f:
        for ts, msg in rows:
            f.write(json.dumps({"ts": ts.astimezone(UTC).isoformat(), "event": "reminder", "message": msg}) + "\n")
    block, _ = pending_mirrors.drain_pending()
    assert block and "late last night" in block, "last night's send is still pending"
    assert "older than a day" not in block


# --- the owner's timezone (/tz away mode) ----------------------------------------

def test_owner_is_home_by_default(owner_home):
    assert timeutils.owner_tz_name() is None
    assert timeutils.owner_tz() == ISRAEL_TZ


def test_set_and_clear_owner_tz(owner_home):
    timeutils.set_owner_tz(NEW_YORK)
    assert timeutils.owner_tz_name() == NEW_YORK
    assert timeutils.owner_tz() == ZoneInfo(NEW_YORK)
    timeutils.clear_owner_tz()
    timeutils.clear_owner_tz()  # idempotent
    assert timeutils.owner_tz_name() is None


def test_unknown_zone_refused_before_touching_disk(owner_home):
    with pytest.raises((ZoneInfoNotFoundError, ValueError)):
        timeutils.set_owner_tz("Mars/Olympus")
    assert not os.path.exists(timeutils._owner_tz_path())


@pytest.mark.parametrize("content", ["{not json", '{"timezone": 7}', '{"timezone": "Mars/Olympus"}', "{}"])
def test_unreadable_owner_tz_reads_as_home(owner_home, content):
    os.makedirs(os.path.dirname(timeutils._owner_tz_path()), exist_ok=True)
    with open(timeutils._owner_tz_path(), "w", encoding="utf-8") as f:
        f.write(content)
    assert timeutils.owner_tz_name() is None
    assert timeutils.owner_tz() == ISRAEL_TZ


# 03:00 UTC on 2026-03-10 is 05:00 on the 10th in Israel but 23:00 on the 9th in New York.
AWAY_NOW = datetime(2026, 3, 10, 3, 0, tzinfo=UTC)


def test_health_days_follow_the_owner(owner_home, monkeypatch):
    monkeypatch.setattr(health, "datetime", frozen_datetime(AWAY_NOW))
    assert health._since_local(1) == datetime(2026, 3, 10, tzinfo=ISRAEL_TZ)
    assert health._zone_note() == "", "home output unchanged"
    timeutils.set_owner_tz(NEW_YORK)
    assert health._since_local(1) == datetime(2026, 3, 9, tzinfo=ZoneInfo(NEW_YORK))
    assert health._since_local(3) == datetime(2026, 3, 7, tzinfo=ZoneInfo(NEW_YORK))
    assert health._local("2026-03-10T03:00:00Z").hour == 23
    assert health._zone_note() == f" (times in {NEW_YORK})"


def test_turn_stamp_home_and_away(owner_home):
    assert agent._turn_stamp(AWAY_NOW) == "[Tuesday, 2026-03-10 05:00 Israel time]"
    timeutils.set_owner_tz(NEW_YORK)
    assert agent._turn_stamp(AWAY_NOW) == \
        f"[Tuesday, 2026-03-10 05:00 Israel time | owner local: Monday, 2026-03-09 23:00 {NEW_YORK}]"


def test_home_anchored_surfaces_ignore_away_mode(owner_home, monkeypatch):
    """The prompt's date and the week bounds stay on Israel time while away."""
    timeutils.set_owner_tz(NEW_YORK)
    monkeypatch.setattr(agent, "_dt", frozen_datetime_module(AWAY_NOW))
    assert agent._today_israel() == "2026-03-10"
    assert timeutils.israel_week_bounds(AWAY_NOW) == ("2026-03-08", "2026-03-14")
