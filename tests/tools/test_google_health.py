"""health_status: which days it asks the API for, and how it reports what comes
back — partial today, days with no data, 7-day baselines, a failed series.
The API is replaced by canned series shaped like live responses."""

from datetime import date, datetime

import pytest

import timeutils
from tests.fakes import frozen_datetime
from timeutils import ISRAEL_TZ
from tools.google_health import google_health_tools as health

NOW = datetime(2026, 10, 5, 22, 0, tzinfo=ISRAEL_TZ)  # Monday evening


def rollup(**by_day):
    return {d: v for d, v in by_day.items()}


def series(field_key, values):
    """{day: value} → a daily-summary map like _daily_range returns."""
    return {d: {field_key: v} for d, v in values.items()}


def canned():
    rhr = {f"2026-09-{d}": 64 for d in range(27, 31)} | {
        "2026-10-02": 65, "2026-10-03": 62, "2026-10-04": 59, "2026-10-05": 58}
    return {
        "steps": rollup(**{"2026-10-03": {"countSum": "1318"}, "2026-10-04": {"countSum": "5567"},
                           "2026-10-05": {"countSum": "7517"}}),
        "azm": {"2026-10-04": {"sumInFatBurnHeartZone": "6", "sumInCardioHeartZone": "0",
                               "sumInPeakHeartZone": "0"}},
        "kcal": {"2026-10-04": {"kcalSum": 2289.4}},
        "sedentary": {"2026-10-04": {"durationSum": "49920s"}},
        "sleep": {"2026-10-04": (429, 403)},
        "rhr": series("beatsPerMinute", {d: str(v) for d, v in rhr.items()}),
        "hrv": series("averageHeartRateVariabilityMilliseconds", {"2026-10-04": 71.9}),
        "spo2": {"2026-10-04": {"averagePercentage": 96.7, "lowerBoundPercentage": 94.8,
                                "upperBoundPercentage": 98.4}},
        "breathing": series("breathsPerMinute", {"2026-10-04": 15.8}),
        "sync": datetime(2026, 10, 5, 21, 45, tzinfo=ISRAEL_TZ),
    }


@pytest.fixture
def api(monkeypatch):
    """Pins the clock at NOW and records the [start, end) each call fetched."""
    timeutils.clear_owner_tz()
    monkeypatch.setattr(health, "datetime", frozen_datetime(NOW))
    calls, data = [], canned()

    def fake_fetch(start, end):
        calls.append((start, end))
        return data

    monkeypatch.setattr(health, "_fetch_status", fake_fetch)
    return calls, data


def status(**kwargs) -> str:
    return health.health_status.invoke(kwargs)


def test_one_full_day(api):
    calls, _ = api
    out = status(day="2026-10-04")
    assert calls == [(date(2026, 10, 4), date(2026, 10, 5))]
    assert "Health — Sun 2026-10-04" in out
    assert "5,567 steps · AZM 6 (fat-burn 6) · 2,289 kcal · sedentary 13h52m" in out
    assert "sleep 6h43m, eff 94%" in out
    # 6 days with data before 10-04 average 63.8; counting 10-04 itself would give 63.1.
    assert "RHR 59 (7d 64)" in out, "baseline is the 7 days before, not including the day"
    assert "SpO2 96.7% (94.8–98.4)" in out
    assert "(so far)" not in out


def test_today_is_partial_and_shows_sync(api):
    out = status()
    assert "Activity (so far): 7,517 steps" in out
    assert "watch last synced 2026-10-05 21:45" in out


def test_old_day_omits_sync(api):
    assert "synced" not in status(day="2026-09-20")


def test_day_without_data_says_so(api):
    out = status(day="2026-10-01")
    assert "Activity: no data" in out
    assert "RHR —" in out and "HRV —" in out
    assert "Overnight" not in out


def test_rows_and_average(api):
    calls, _ = api
    out = status(days=3)
    assert calls == [(date(2026, 10, 3), date(2026, 10, 6))]
    assert "Sat 10-03: 1,318 steps · AZM — · sleep — · RHR 62 · HRV —" in out
    assert "Mon 10-05: 7,517 steps" in out
    assert "Avg steps: 3,442 (today excluded)" in out


def test_days_capped_at_api_limit(api):
    calls, _ = api
    status(day="2026-10-04", days=40)
    assert calls == [(date(2026, 9, 21), date(2026, 10, 5))]


def test_failed_series_still_reports_the_rest(api):
    _, data = api
    data["steps"] = None
    out = status(day="2026-10-04")
    assert "Request failed for: steps" in out
    assert "RHR 59" in out


@pytest.mark.parametrize("day, err", [("04-10-2026", "YYYY-MM-DD"), ("2026-10-06", "future")])
def test_rejects_bad_day(api, day, err):
    calls, _ = api
    assert err in status(day=day)
    assert calls == []
