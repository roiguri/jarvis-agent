"""Google Health API tools — Roi's Pixel Watch sleep, workouts, biometrics.

Read-only. Endpoints, dataType IDs, filter syntax and response shapes were all
verified against live Pixel Watch 2 data. Setup: tools/google_health/SETUP.md.

Verified rules (Google Health API v4):
* Resource:  ``users/me/dataTypes/{kebab-data-type}/dataPoints``  (GET list).
  ``users/me`` alias works; dataType ID is the kebab-case of the DataPoint
  field (``daily-resting-heart-rate``), the filter member is its snake_case.
* Sleep is a session filtered by ``sleep.interval.end_time`` (RFC-3339, UTC)
  — start_time is NOT a supported filter member for sleep.
* Exercise is a session filtered by ``exercise.interval.civil_start_time``
  (ISO ``YYYY-MM-DD`` local civil date).
* Resting HR / HRV are daily-summary types filtered by ``<snake>.date``
  (ISO ``YYYY-MM-DD``).

Auth (Bearer token + refresh) lives in ``tools.google_health._auth``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.google_health._auth import get_access_token, GoogleHealthNotConfigured

BASE = "https://health.googleapis.com/v4"
USER = "users/me"
ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
_UTC = ZoneInfo("UTC")


def _auth_header() -> dict:
    return {"Authorization": f"Bearer {get_access_token()}"}


def _raise_for_auth(resp: requests.Response) -> None:
    if resp.status_code in (401, 403):
        raise RuntimeError(
            "Google Health authorization expired. Re-mint the refresh token "
            "(see tools/google_health/SETUP.md), update "
            "GOOGLE_HEALTH_REFRESH_TOKEN in /app/secrets/.env, and restart."
        )


def _list(data_type: str, filter_expr: str, page_size: int = 50) -> list[dict]:
    """GET users/me/dataTypes/{data_type}/dataPoints?filter=… → dataPoints[]."""
    resp = requests.get(
        f"{BASE}/{USER}/dataTypes/{data_type}/dataPoints",
        headers=_auth_header(),
        params={"filter": filter_expr, "pageSize": page_size},
        timeout=15,
    )
    _raise_for_auth(resp)
    resp.raise_for_status()
    return resp.json().get("dataPoints", []) or []


def _since_local(days: int) -> datetime:
    """Local (Asia/Jerusalem) midnight `days-1` days ago; days=1 → today 00:00."""
    days = max(1, int(days))
    return datetime.now(ISRAEL_TZ).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days - 1)


def _local(ts: str) -> datetime:
    """Parse an API RFC-3339 timestamp to Asia/Jerusalem local time."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(ISRAEL_TZ)


def _hm(td: timedelta) -> str:
    mins = max(0, int(td.total_seconds() // 60))
    return f"{mins // 60}h{mins % 60:02d}m"


@tool_register(namespace="google_health")
@tool
def check_sleep(nights: int = 1) -> str:
    """Roi's Pixel Watch sleep for the last `nights` night(s); nights=1 = last
    night. Reports bedtime, wake time, total asleep, sleep efficiency and the
    stage breakdown. The Google Health API does NOT expose Fitbit's 0-100
    sleep score — sleep efficiency (asleep / in-bed) is the closest proxy."""
    cutoff = _since_local(nights).astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        points = _list("sleep", f'sleep.interval.end_time >= "{cutoff}"')
    except GoogleHealthNotConfigured as e:
        return str(e)
    except requests.RequestException as e:
        return f"Google Health sleep request failed: {e}"
    if not points:
        return "No sleep recorded for that period — the watch may not have synced yet."

    lines = []
    for p in points:
        s = p.get("sleep", {})
        iv = s.get("interval", {})
        if not iv.get("startTime") or not iv.get("endTime"):
            continue
        start, end = _local(iv["startTime"]), _local(iv["endTime"])
        summary = s.get("summary") or {}

        # Prefer the server-computed summary (canonical, handles out-of-bed
        # segments). Fall back to summing client-side from stages[].
        if summary:
            in_bed_m = int(summary.get("minutesInSleepPeriod") or 0)
            asleep_m = int(summary.get("minutesAsleep") or 0)
            latency_m = int(summary.get("minutesToFallAsleep") or 0)
            waso_m = int(summary.get("minutesAfterWakeUp") or 0)
            stage_min = {
                ss.get("type", "?"): int(ss.get("minutes") or 0)
                for ss in summary.get("stagesSummary", [])
            }
        else:
            stage_min = {}
            for st in s.get("stages", []):
                if st.get("startTime") and st.get("endTime"):
                    m = int((_local(st["endTime"]) - _local(st["startTime"])).total_seconds() // 60)
                    stage_min[st.get("type", "?")] = stage_min.get(st.get("type", "?"), 0) + m
            in_bed_m = int((end - start).total_seconds() // 60)
            asleep_m = sum(v for k, v in stage_min.items() if k != "AWAKE") or in_bed_m
            latency_m = waso_m = 0

        efficiency = f"{round(100 * asleep_m / in_bed_m)}%" if in_bed_m else "—"
        breakdown = ", ".join(
            f"{k.title()} {_hm(timedelta(minutes=v))}"
            for k, v in sorted(stage_min.items())
        )
        extras = []
        if latency_m: extras.append(f"latency {latency_m}m")
        if waso_m: extras.append(f"awake after wake {waso_m}m")

        lines.append(
            f"Night ending {end:%Y-%m-%d}: {start:%H:%M}→{end:%H:%M} "
            f"({_hm(timedelta(minutes=in_bed_m))} in bed, "
            f"{_hm(timedelta(minutes=asleep_m))} asleep · {efficiency} efficiency)"
            + (f" — {breakdown}" if breakdown else "")
            + (f". {'; '.join(extras)}." if extras else "")
        )
    return "Sleep:\n" + "\n".join(lines)


def _pace(sec_per_m: float) -> str:
    sec_per_km = int(round(sec_per_m * 1000))
    return f"{sec_per_km // 60}:{sec_per_km % 60:02d} min/km"


def _secs(v) -> int:
    return int(str(v or "0s").rstrip("s") or 0)


def _format_workout(p: dict) -> str:
    ex = p.get("exercise", {})
    iv = ex.get("interval", {})
    src = p.get("dataSource", {})
    name = ex.get("displayName") or ex.get("exerciseType", "Workout")
    when = _local(iv["startTime"]).strftime("%Y-%m-%d %H:%M") if iv.get("startTime") else "?"
    dur = _hm(timedelta(seconds=_secs(ex.get("activeDuration"))))
    manual = " (manual)" if src.get("recordingMethod") == "MANUAL" else ""

    m = ex.get("metricsSummary", {}) or {}
    distance_mm = int(m.get("distanceMillimeters") or 0)
    steps = m.get("steps")
    pace_spm = m.get("averagePaceSecondsPerMeter")
    elev_mm = int(m.get("elevationGainMillimeters") or 0)

    movement = []
    if distance_mm:
        movement.append(f"{distance_mm / 1_000_000:.2f} km")
    if pace_spm:
        movement.append(_pace(float(pace_spm)))
    if steps:
        movement.append(f"{steps} steps")
    if elev_mm >= 5000:
        movement.append(f"elev +{round(elev_mm / 1000)}m")

    energy = []
    if m.get("caloriesKcal") is not None:
        energy.append(f"{m['caloriesKcal']} kcal")
    if m.get("averageHeartRateBeatsPerMinute"):
        energy.append(f"avg HR {m['averageHeartRateBeatsPerMinute']}")

    zones = m.get("heartRateZoneDurations", {}) or {}
    zone_parts = [
        f"{label} {_secs(zones.get(key)) // 60}m"
        for key, label in (("lightTime", "light"), ("moderateTime", "moderate"),
                           ("vigorousTime", "vigorous"), ("peakTime", "peak"))
        if _secs(zones.get(key)) >= 60
    ]

    split_paces = []
    for sp in ex.get("splits") or []:
        if sp.get("splitType") != "DISTANCE":
            continue
        sm = sp.get("metricsSummary") or {}
        if int(sm.get("distanceMillimeters") or 0) < 500_000:
            continue  # skip sub-500m tail splits
        if sm.get("averagePaceSecondsPerMeter"):
            split_paces.append(_pace(float(sm["averagePaceSecondsPerMeter"])).replace(" min/km", ""))

    lines = [f"{when} — {name}, {dur}{manual}"]
    if movement:
        lines.append("  " + ", ".join(movement))
    if energy:
        lines.append("  " + ", ".join(energy))
    if zone_parts:
        lines.append("  HR zones: " + ", ".join(zone_parts))
    if split_paces:
        lines.append("  Splits (min/km): " + ", ".join(split_paces))
    return "\n".join(lines)


@tool_register(namespace="google_health")
@tool
def check_workouts(since_date: str = "", until_date: str = "") -> str:
    """Logged Pixel Watch workout/exercise sessions in a date range
    (Asia/Jerusalem). `since_date`/`until_date` are inclusive YYYY-MM-DD;
    `since_date=""` defaults to today, `until_date=""` means no upper bound.
    For a single day, pass `since_date == until_date`. Reports per session:
    name, time, duration, distance/pace/steps/elevation when available,
    calories, avg HR, HR-zone minutes, and per-km splits for GPS sessions.
    Manually-logged sessions are tagged `(manual)` — their kcal/HR are
    MET-estimated, not measured."""
    today = datetime.now(ISRAEL_TZ).strftime("%Y-%m-%d")
    if since_date:
        try:
            datetime.strptime(since_date, "%Y-%m-%d")
        except ValueError:
            return "since_date must be YYYY-MM-DD."
    else:
        since_date = today
    if until_date:
        try:
            datetime.strptime(until_date, "%Y-%m-%d")
        except ValueError:
            return "until_date must be YYYY-MM-DD."
        if until_date < since_date:
            return "until_date must be on or after since_date."

    # Health API filter supports AND, but only `>=` and `<` for date fields
    # (no `<=`, no `=`); express inclusive `until_date` as `< until+1 day`.
    filt = f'exercise.interval.civil_start_time >= "{since_date}"'
    if until_date:
        upper = (datetime.strptime(until_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        filt += f' AND exercise.interval.civil_start_time < "{upper}"'
    try:
        points = _list("exercise", filt)
    except GoogleHealthNotConfigured as e:
        return str(e)
    except requests.RequestException as e:
        return f"Google Health workouts request failed: {e}"

    if since_date == until_date:
        range_desc = f"on {since_date}"
    elif until_date:
        range_desc = f"from {since_date} to {until_date}"
    else:
        range_desc = f"since {since_date}"

    if not points:
        return f"No workouts {range_desc}."
    return f"Workouts {range_desc}:\n\n" + "\n\n".join(_format_workout(p) for p in points)


def _daily(data_type: str, filter_member: str, days: int) -> list[dict]:
    civil = _since_local(days).strftime("%Y-%m-%d")
    return _list(data_type, f'{filter_member}.date >= "{civil}"')


@tool_register(namespace="google_health")
@tool
def check_biometrics(days: int = 1) -> str:
    """Roi's daily resting heart rate and heart-rate variability (HRV) from the
    Pixel Watch for the last `days` day(s); days=1 = today. Use for resting
    heart rate or HRV / recovery questions."""
    try:
        rhr = _daily("daily-resting-heart-rate", "daily_resting_heart_rate", days)
        hrv = _daily("daily-heart-rate-variability", "daily_heart_rate_variability", days)
    except GoogleHealthNotConfigured as e:
        return str(e)
    except requests.RequestException as e:
        return f"Google Health biometrics request failed: {e}"

    def _d(o: dict) -> str:
        d = o.get("date", {})
        return f"{d.get('year'):04d}-{d.get('month'):02d}-{d.get('day'):02d}"

    rhr_l = [
        f"{_d(p['dailyRestingHeartRate'])}: {p['dailyRestingHeartRate'].get('beatsPerMinute')} bpm"
        for p in rhr if p.get("dailyRestingHeartRate")
    ]
    hrv_l = [
        f"{_d(p['dailyHeartRateVariability'])}: "
        f"{p['dailyHeartRateVariability'].get('averageHeartRateVariabilityMilliseconds')} ms"
        for p in hrv if p.get("dailyHeartRateVariability")
    ]
    if not rhr_l and not hrv_l:
        return "No biometric data for that period — the watch may not have synced yet."
    return (
        "Resting heart rate:\n" + ("\n".join(rhr_l) or "  (none)")
        + "\n\nHRV (nightly avg):\n" + ("\n".join(hrv_l) or "  (none)")
    )
