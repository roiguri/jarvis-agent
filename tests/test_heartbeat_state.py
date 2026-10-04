"""The heartbeat gate's parser and due logic: HEARTBEAT.md headers, ``due:``
windows (Israel time), and whether a task is due at a given moment."""

import os
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import heartbeat_state as hs
from timeutils import ISRAEL_TZ
from triggers import store

UTC = timezone.utc


def il(y, m, d, h, mi=0):
    """An Israel-local moment."""
    return datetime(y, m, d, h, mi, tzinfo=ISRAEL_TZ)


def window(spec):
    w = hs.parse_window(spec)
    assert w is not None, f"{spec!r} should parse"
    return w


# --- due: windows ---------------------------------------------------------------

def test_range_window():
    w = window("06:00-22:00")
    assert w.is_open(il(2026, 3, 10, 12))
    assert w.is_open(il(2026, 3, 10, 6)), "start is inclusive"
    assert not w.is_open(il(2026, 3, 10, 22)), "end is exclusive"
    assert not w.is_open(il(2026, 3, 10, 23))


def test_window_reads_israel_time():
    w = window("06:00-22:00")
    # 21:30 UTC is 23:30 in Israel (UTC+2 in March): closed, though 21:30 is inside the range.
    assert not w.is_open(datetime(2026, 3, 10, 21, 30, tzinfo=UTC))


def test_range_wrapping_midnight():
    w = window("22:00-02:00")
    assert w.is_open(il(2026, 3, 10, 23, 30))
    assert w.is_open(il(2026, 3, 11, 1)), "after midnight, anchored to the day before"
    assert not w.is_open(il(2026, 3, 11, 3))
    assert not w.is_open(il(2026, 3, 10, 21, 59))


def test_equal_ends_mean_all_day():
    w = window("08:00-08:00")
    assert all(w.is_open(il(2026, 3, 10, h)) for h in range(24))


@pytest.mark.parametrize("spec", ["20:30±3h", "20:30+-3h", "20:30+/-3h", "20:30 ± 3 h"])
def test_radius_window(spec):
    w = window(spec)
    assert w.is_open(il(2026, 3, 10, 17, 30)), "radius is inclusive"
    assert w.is_open(il(2026, 3, 10, 23, 30))
    assert not w.is_open(il(2026, 3, 10, 23, 31))
    assert not w.is_open(il(2026, 3, 10, 17, 29))


def test_radius_crossing_midnight():
    w = window("23:00±2h")
    assert w.is_open(il(2026, 3, 11, 0, 30))
    assert not w.is_open(il(2026, 3, 11, 1, 1))


def test_weekday_window():
    assert il(2026, 3, 10, 0).weekday() == 1  # a Tuesday
    w = window("Tue,Sat 20:30±3h")
    assert w.is_open(il(2026, 3, 10, 20))
    assert not w.is_open(il(2026, 3, 11, 20)), "Wednesday"
    assert w.is_open(il(2026, 3, 14, 20)), "Saturday"


def test_weekday_window_wrapping_into_next_day():
    w = window("Fri 23:00-01:00")
    assert w.is_open(il(2026, 3, 14, 0, 30)), "Saturday 00:30 belongs to Friday's window"
    assert not w.is_open(il(2026, 3, 15, 0, 30)), "Sunday 00:30 belongs to Saturday, not listed"


@pytest.mark.parametrize("spec", ["25:00-26:00", "noon", "8-10", "Funday 10:00-11:00", "10:00", "10:00±h", ""])
def test_unparseable_windows(spec):
    assert hs.parse_window(spec) is None


# --- task headers ---------------------------------------------------------------

def parse(header):
    tasks = hs.parse_tasks_text(header)
    assert len(tasks) == 1
    return tasks[0]


@pytest.mark.parametrize("cadence, want", [
    ("every 1h", timedelta(hours=1)),
    ("every 24h", timedelta(hours=24)),
    ("every 7d", timedelta(days=7)),
    ("every 2 hours", timedelta(hours=2)),
    ("every 1 hour", timedelta(hours=1)),
    ("every 3 days", timedelta(days=3)),
    ("Every 6H", timedelta(hours=6)),
])
def test_cadence_units(cadence, want):
    assert parse(f"- **t** | {cadence} | notes: `heartbeat/t.md`").cadence == want


def test_missing_cadence_is_none():
    assert parse("- **t** | sometimes | notes: `heartbeat/t.md`").cadence is None


def test_due_field():
    t = parse("- **t** | every 1h | due: 06:00-22:00 | notes: `heartbeat/t.md`")
    assert t.due == "06:00-22:00"
    assert t.window == hs.parse_window("06:00-22:00")


def test_unparseable_due_keeps_the_text_and_no_window():
    t = parse("- **t** | every 1h | due: whenever | notes: `heartbeat/t.md`")
    assert (t.due, t.window) == ("whenever", None)


def test_paused_only_as_its_own_field():
    assert parse("- **t** | every 1h | paused | notes: `heartbeat/t.md`").paused
    assert parse("- **t** | every 1h | notes: `heartbeat/t.md` | paused").paused
    assert not parse("- **t** | every 1h | notes: `heartbeat/paused-t.md`").paused
    assert not parse("- **t** | every 1h | paused for now | notes: `heartbeat/t.md`").paused


def test_gate_only_as_its_own_field():
    assert parse("- **t** | every 1h | gate: arbox-sync | notes: `heartbeat/t.md`").gate == "arbox-sync"
    assert parse("- **t** | every 1h | notes: `heartbeat/gate: x.md`").gate is None


def test_non_headers_ignored_and_duplicates_keep_the_first():
    tasks = hs.parse_tasks_text(
        "# Heartbeat Tasks\n\nprose line\n"
        "- **a** | every 1h | notes: `heartbeat/a.md`\n  body\n"
        "- plain bullet\n"
        "- **a** | every 7d | notes: `heartbeat/a2.md`\n"
        "- **b** | every 2h | notes: `heartbeat/b.md`\n")
    assert [(t.name, t.cadence) for t in tasks] == [("a", timedelta(hours=1)), ("b", timedelta(hours=2))]


# --- any_due --------------------------------------------------------------------

TASKS = (
    "# Heartbeat Tasks\n\n"
    "- **hourly** | every 1h | notes: `heartbeat/hourly.md`\n  x\n\n"
    "- **three-hourly** | every 3h | notes: `heartbeat/three.md`\n  x\n\n"
    "- **windowed** | every 1h | due: 06:00-08:00 | notes: `heartbeat/windowed.md`\n  x\n\n"
    "- **paused-one** | every 1h | paused | notes: `heartbeat/paused.md`\n  x\n\n"
    "- **no-cadence** | whenever | notes: `heartbeat/none.md`\n  x\n"
)


@pytest.fixture
def hb_file(tmp_path):
    path = tmp_path / "HEARTBEAT.md"
    path.write_text(TASKS, encoding="utf-8")
    return str(path)


@pytest.fixture
def stamps(monkeypatch):
    """Stand-in last_run map: tests fill it; any_due reads it."""
    last_run: dict[str, str] = {}
    monkeypatch.setattr(hs, "load_state", lambda: {"last_run": last_run})
    return last_run


def due_at(now, hb_file):
    return hs.any_due(now, heartbeat_path=hb_file)[1]


# 12:00 Israel (10:00 UTC): outside the 06:00-08:00 window.
NOON = il(2026, 3, 10, 12)


def test_never_stamped_tasks_are_due(hb_file, stamps):
    assert due_at(NOON, hb_file) == ["hourly", "three-hourly", "no-cadence"]


def test_window_closes_out_even_a_never_run_task(hb_file, stamps):
    assert "windowed" in due_at(il(2026, 3, 10, 7), hb_file)
    assert "windowed" not in due_at(NOON, hb_file)


def test_paused_never_due(hb_file, stamps):
    assert "paused-one" not in due_at(il(2026, 3, 10, 7), hb_file)


def test_hourly_cadence_with_grace(hb_file, stamps):
    stamps["hourly"] = (NOON - timedelta(minutes=59, seconds=30)).isoformat()
    assert "hourly" in due_at(NOON, hb_file), "inside the 60s grace"
    stamps["hourly"] = (NOON - timedelta(minutes=58)).isoformat()
    assert "hourly" not in due_at(NOON, hb_file)


def test_lattice_rescues_an_off_lattice_stamp(hb_file, stamps):
    """A stamp that landed late (10:08) would read 2h52m at the 13:00 tick and
    miss it; measured tick-to-tick it is a full 3h."""
    stamps["three-hourly"] = datetime(2026, 3, 10, 10, 8, tzinfo=UTC).isoformat()
    assert "three-hourly" in due_at(datetime(2026, 3, 10, 13, 0, tzinfo=UTC), hb_file)
    assert "three-hourly" not in due_at(datetime(2026, 3, 10, 12, 0, tzinfo=UTC), hb_file)


def test_no_lattice_rescue_for_a_one_tick_cadence(hb_file, stamps):
    """Flooring would call an eight-minute-old stamp an hour old."""
    stamps["hourly"] = datetime(2026, 3, 10, 10, 52, tzinfo=UTC).isoformat()
    assert "hourly" not in due_at(datetime(2026, 3, 10, 11, 0, tzinfo=UTC), hb_file)


def test_unreadable_stamp_is_due(hb_file, stamps):
    stamps["hourly"] = "not a time"
    assert "hourly" in due_at(NOON, hb_file)


def test_naive_stamp_read_as_utc(hb_file, stamps):
    stamps["hourly"] = datetime(2026, 3, 10, 9, 30).isoformat()  # 30 min before NOON in UTC
    assert "hourly" not in due_at(NOON, hb_file)


def test_nothing_due(tmp_path, stamps):
    # Without no-cadence, which is always due.
    path = tmp_path / "HEARTBEAT.md"
    path.write_text(TASKS.split("- **no-cadence**")[0], encoding="utf-8")
    for name in ("hourly", "three-hourly"):
        stamps[name] = NOON.isoformat()
    assert hs.any_due(NOON, heartbeat_path=str(path)) == (False, [])


@pytest.mark.parametrize("text", ["", "# Heartbeat Tasks\n\nno tasks here\n"])
def test_no_parseable_tasks_means_run_everything(tmp_path, stamps, text):
    path = tmp_path / "HEARTBEAT.md"
    path.write_text(text, encoding="utf-8")
    assert hs.any_due(NOON, heartbeat_path=str(path)) == (True, None)


def test_missing_file_means_run_everything(tmp_path, stamps):
    assert hs.any_due(NOON, heartbeat_path=str(tmp_path / "absent.md")) == (True, None)


# Properties over arbitrary stamp/now pairs, for the cadences in use.

CADENCES = [timedelta(hours=1), timedelta(hours=3), timedelta(hours=24), timedelta(days=7)]
moments = st.datetimes(min_value=datetime(2025, 1, 1), max_value=datetime(2028, 1, 1),
                       timezones=st.just(UTC))


def single_task(tmp_path, cadence):
    hours = int(cadence.total_seconds() // 3600)
    path = tmp_path / f"HEARTBEAT_{hours}.md"
    path.write_text(f"- **t** | every {hours}h | notes: `heartbeat/t.md`\n", encoding="utf-8")
    return str(path)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=300)
@given(prev=moments, gap=st.timedeltas(min_value=timedelta(0), max_value=timedelta(days=9)),
       cadence=st.sampled_from(CADENCES))
def test_due_exactly_when_the_cadence_allows(tmp_path, stamps, prev, gap, cadence):
    stamps["t"] = prev.isoformat()
    due = due_at(prev + gap, single_task(tmp_path, cadence)) == ["t"]
    threshold = cadence - hs.CADENCE_GRACE
    if gap >= threshold:
        assert due, "a full cadence has passed"
    if due:
        # Rounding both ends to the tick lattice discards less than one tick.
        slack = hs._TICK_INTERVAL if cadence > hs._TICK_INTERVAL else timedelta(0)
        assert gap > threshold - slack, "never due early by a tick or more"


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=200)
@given(now=moments)
def test_closed_window_never_due(tmp_path, stamps, now):
    path = tmp_path / "HEARTBEAT.md"
    path.write_text("- **t** | every 1h | due: 06:00-08:00 | notes: `heartbeat/t.md`\n", encoding="utf-8")
    open_now = hs.parse_window("06:00-08:00").is_open(now)
    assert (due_at(now, str(path)) == ["t"]) == open_now


# --- stamping through the real store --------------------------------------------

@pytest.fixture
def clean_store():
    def clear():
        if os.path.exists(store.STORE_PATH):
            os.remove(store.STORE_PATH)
    clear()
    yield
    clear()


def test_stamp_records_known_tasks_only(hb_file, clean_store):
    when = datetime(2026, 3, 10, 10, 0, tzinfo=UTC)
    assert hs.stamp(["hourly", "nope", " ", ""], when, heartbeat_path=hb_file) == ["hourly"]
    assert hs.load_state()["last_run"] == {"hourly": when.isoformat()}
    assert "hourly" not in hs.any_due(when + timedelta(minutes=30), heartbeat_path=hb_file)[1]
    assert "hourly" in hs.any_due(when + timedelta(hours=1), heartbeat_path=hb_file)[1]


def test_stamp_nothing_valid_writes_nothing(hb_file, clean_store):
    assert hs.stamp(["nope"], heartbeat_path=hb_file) == []
    assert not os.path.exists(store.STORE_PATH)


# --- filtering the prompt's copy ------------------------------------------------

def test_filter_with_unknown_due_list_keeps_all_but_gated():
    text = ("# Heartbeat Tasks\n\n"
            "- **a** | every 1h | notes: `heartbeat/a.md`\n  A body.\n\n"
            "- **g** | every 1h | gate: x | notes: `heartbeat/g.md`\n  G body.\n")
    out = hs.filter_heartbeat_md(text, None)
    assert "A body." in out and "G body." not in out
    assert "not due" not in out, "no notes when the due list is unknown"


def test_filter_ignores_unknown_names():
    text = "- **a** | every 1h | notes: `heartbeat/a.md`\n  A body.\n"
    out = hs.filter_heartbeat_md(text, ["a", "ghost"])
    assert "A body." in out and "ghost" not in out
