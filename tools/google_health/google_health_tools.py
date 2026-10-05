"""Google Health API tools — Roi's Pixel Watch sleep, workouts, daily status.

Read-only. Endpoints, dataType IDs, filter syntax and response shapes were all
verified against live Pixel Watch data. Setup: tools/google_health/SETUP.md.

Verified rules (Google Health API v4):
* Resource:  ``users/me/dataTypes/{kebab-data-type}/dataPoints``  (GET list).
  ``users/me`` alias works; dataType ID is the kebab-case of the DataPoint
  field (``daily-resting-heart-rate``), the filter member is its snake_case.
* Sleep is a session filtered by ``sleep.interval.end_time`` (RFC-3339, UTC)
  — start_time is NOT a supported filter member for sleep.
* Exercise is a session filtered by ``exercise.interval.civil_start_time``
  (ISO ``YYYY-MM-DD`` local civil date).
* Daily-summary types (resting HR, HRV, SpO2, breathing rate) are filtered
  by ``<snake>.date`` (ISO ``YYYY-MM-DD``); ``>=`` and ``<`` combine with AND.
* Activity totals (steps, AZM, calories, sedentary time) come from
  ``dataPoints:dailyRollUp`` over a civil-date range; days without data are
  omitted, not zero. ``total-calories`` rollups cap the range at 14 days.

Auth (Bearer token + refresh) lives in ``tools.google_health._auth``.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.google_health._auth import get_access_token, GoogleHealthNotConfigured

BASE = "https://health.googleapis.com/v4"
USER = "users/me"
from timeutils import owner_tz, owner_tz_name

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


def _list(data_type: str, filter_expr: str = "", page_size: int = 50) -> list[dict]:
    """GET users/me/dataTypes/{data_type}/dataPoints?filter=… → dataPoints[],
    newest first."""
    params = {"pageSize": page_size}
    if filter_expr:
        params["filter"] = filter_expr
    resp = requests.get(
        f"{BASE}/{USER}/dataTypes/{data_type}/dataPoints",
        headers=_auth_header(),
        params=params,
        timeout=15,
    )
    _raise_for_auth(resp)
    resp.raise_for_status()
    return resp.json().get("dataPoints", []) or []


def _since_local(days: int) -> datetime:
    """Owner-local midnight `days-1` days ago (Israel unless /tz set an away
    zone); days=1 → today 00:00. The watch travels with the owner, so its
    data's day boundaries follow the owner — the one surface that must NOT
    stay home-anchored."""
    days = max(1, int(days))
    return datetime.now(owner_tz()).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days - 1)


def _local(ts: str) -> datetime:
    """Parse an API RFC-3339 timestamp to the owner's local time."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(owner_tz())


def _zone_note() -> str:
    """' (times in <zone>)' while the owner is away; empty at home, so home
    output stays byte-identical."""
    name = owner_tz_name()
    return f" (times in {name})" if name else ""


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
    return "Sleep" + _zone_note() + ":\n" + "\n".join(lines)


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
    (owner-local dates — Israel unless traveling). `since_date`/`until_date`
    are inclusive YYYY-MM-DD;
    `since_date=""` defaults to today, `until_date=""` means no upper bound.
    For a single day, pass `since_date == until_date`. Reports per session:
    name, time, duration, distance/pace/steps/elevation when available,
    calories, avg HR, HR-zone minutes, and per-km splits for GPS sessions.
    Manually-logged sessions are tagged `(manual)` — their kcal/HR are
    MET-estimated, not measured."""
    today = datetime.now(owner_tz()).strftime("%Y-%m-%d")
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
    return f"Workouts {range_desc}{_zone_note()}:\n\n" + "\n\n".join(_format_workout(p) for p in points)


_STATUS_MAX_DAYS = 14  # the API caps total-calories rollups at 14 days
_BASELINE_DAYS = 7


def _iso(d: dict) -> str:
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def _civil(d: date) -> dict:
    return {"date": {"year": d.year, "month": d.month, "day": d.day}}


def _rollup(data_type: str, start: date, end: date) -> dict[str, dict]:
    """Daily totals for [start, end), keyed by civil date. Days without data
    are absent, never zero."""
    resp = requests.post(
        f"{BASE}/{USER}/dataTypes/{data_type}/dataPoints:dailyRollUp",
        headers=_auth_header(),
        json={"range": {"start": _civil(start), "end": _civil(end)}, "windowSizeDays": 1},
        timeout=15,
    )
    _raise_for_auth(resp)
    resp.raise_for_status()
    out = {}
    for p in resp.json().get("rollupDataPoints", []) or []:
        value = next((v for k, v in p.items() if not k.startswith("civil")), None)
        if value is not None:
            out[_iso(p["civilStartTime"]["date"])] = value
    return out


def _daily_range(data_type: str, field: str, start: date, end: date) -> dict[str, dict]:
    """A once-a-day summary type for [start, end), keyed by its date."""
    member = re.sub(r"(?<!^)(?=[A-Z])", "_", field).lower()
    points = _list(
        data_type, f'{member}.date >= "{start}" AND {member}.date < "{end}"'
    )
    return {_iso(p[field]["date"]): p[field] for p in points if p.get(field)}


def _sleep_minutes(s: dict) -> tuple[int, int]:
    """(in bed, asleep) minutes for one sleep session."""
    summary = s.get("summary") or {}
    if summary:
        return int(summary.get("minutesInSleepPeriod") or 0), int(summary.get("minutesAsleep") or 0)
    iv = s.get("interval", {})
    in_bed = int((_local(iv["endTime"]) - _local(iv["startTime"])).total_seconds() // 60)
    asleep = sum(
        int((_local(st["endTime"]) - _local(st["startTime"])).total_seconds() // 60)
        for st in s.get("stages", [])
        if st.get("type") != "AWAKE" and st.get("startTime") and st.get("endTime")
    )
    return in_bed, asleep or in_bed


def _sleep_by_wake_day(start: date, end: date) -> dict[str, tuple[int, int]]:
    """The main sleep (longest) of each night, keyed by the day it ended on."""
    def utc(d: date) -> str:
        midnight = datetime(d.year, d.month, d.day, tzinfo=owner_tz())
        return midnight.astimezone(_UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    out: dict[str, tuple[int, int]] = {}
    for p in _list(
        "sleep",
        f'sleep.interval.end_time >= "{utc(start)}" AND sleep.interval.end_time < "{utc(end)}"',
    ):
        s = p.get("sleep", {})
        iv = s.get("interval", {})
        if not iv.get("startTime") or not iv.get("endTime"):
            continue
        day = _local(iv["endTime"]).date().isoformat()
        mins = _sleep_minutes(s)
        if mins[1] > out.get(day, (0, 0))[1]:
            out[day] = mins
    return out


def _last_sync() -> datetime | None:
    """When the newest heart-rate sample was taken — a proxy for the watch's
    last sync (the pairedDevices endpoint needs a scope we don't hold)."""
    points = _list("heart-rate", page_size=1)
    if not points:
        return None
    return _local(points[0]["heartRate"]["sampleTime"]["physicalTime"])


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _avg(values) -> float | None:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else None


def _baseline(series: dict[str, float], day: str) -> float | None:
    """Mean of the `_BASELINE_DAYS` days before `day` that have a value."""
    d = date.fromisoformat(day)
    return _avg(series.get((d - timedelta(days=i)).isoformat()) for i in range(1, _BASELINE_DAYS + 1))


def _azm(v: dict | None) -> tuple[int, str] | None:
    if not v:
        return None
    parts = [
        (label, int(v.get(key) or 0))
        for key, label in (("sumInFatBurnHeartZone", "fat-burn"),
                           ("sumInCardioHeartZone", "cardio"),
                           ("sumInPeakHeartZone", "peak"))
    ]
    total = sum(m for _, m in parts)
    detail = ", ".join(f"{label} {m}" for label, m in parts if m)
    return total, detail


def _fetch_status(start: date, end: date) -> dict:
    """Every series the status needs, fetched in parallel. A series whose
    request fails comes back as None so the rest still report; an auth
    failure raises. Keep it to 10 requests: past that the API holds the extra
    ones back for ~5s."""
    base_start = start - timedelta(days=_BASELINE_DAYS)
    jobs = {
        "steps": lambda: _rollup("steps", start, end),
        "azm": lambda: _rollup("active-zone-minutes", start, end),
        "kcal": lambda: _rollup("total-calories", start, end),
        "sedentary": lambda: _rollup("sedentary-period", start, end),
        "sleep": lambda: _sleep_by_wake_day(start, end),
        "rhr": lambda: _daily_range("daily-resting-heart-rate", "dailyRestingHeartRate", base_start, end),
        "hrv": lambda: _daily_range("daily-heart-rate-variability", "dailyHeartRateVariability", base_start, end),
        "spo2": lambda: _daily_range("daily-oxygen-saturation", "dailyOxygenSaturation", start, end),
        "breathing": lambda: _daily_range("daily-respiratory-rate", "dailyRespiratoryRate", base_start, end),
        "sync": _last_sync,
    }
    get_access_token()  # refresh once up front, not racing in every worker
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        futures = {name: pool.submit(fn) for name, fn in jobs.items()}
    out = {}
    for name, fut in futures.items():
        try:
            out[name] = fut.result()
        except requests.RequestException:
            out[name] = None
    return out


def _series(data: dict | None, key: str) -> dict[str, float]:
    return {d: _num(v.get(key)) for d, v in (data or {}).items()}


def _vs(value: float | None, base: float | None, fmt: str) -> str:
    if value is None:
        return "—"
    shown = fmt.format(value)
    return f"{shown} (7d {fmt.format(base)})" if base is not None else shown


def _format_day(day: str, data: dict, partial: bool) -> list[str]:
    steps = (data["steps"] or {}).get(day)
    azm = _azm((data["azm"] or {}).get(day))
    kcal = (data["kcal"] or {}).get(day)
    sed = (data["sedentary"] or {}).get(day)

    activity = []
    if steps:
        activity.append(f"{int(_num(steps['countSum']) or 0):,} steps")
    if azm:
        activity.append(f"AZM {azm[0]}" + (f" ({azm[1]})" if azm[1] else ""))
    if kcal:
        activity.append(f"{round(_num(kcal['kcalSum']) or 0):,} kcal")
    if sed:
        activity.append(f"sedentary {_hm(timedelta(seconds=_secs(sed.get('durationSum'))))}")

    rhr = _series(data["rhr"], "beatsPerMinute")
    hrv = _series(data["hrv"], "averageHeartRateVariabilityMilliseconds")
    sleep = (data["sleep"] or {}).get(day)
    recovery = []
    if sleep:
        in_bed, asleep = sleep
        eff = f", eff {round(100 * asleep / in_bed)}%" if in_bed else ""
        recovery.append(f"sleep {_hm(timedelta(minutes=asleep))}{eff}")
    recovery.append("RHR " + _vs(rhr.get(day), _baseline(rhr, day), "{:.0f}"))
    recovery.append("HRV " + _vs(hrv.get(day), _baseline(hrv, day), "{:.0f} ms"))

    breathing = _series(data["breathing"], "breathsPerMinute")
    spo2 = (data["spo2"] or {}).get(day)
    overnight = []
    if spo2 and spo2.get("averagePercentage") is not None:
        lo, hi = spo2.get("lowerBoundPercentage"), spo2.get("upperBoundPercentage")
        rng = f" ({lo:g}–{hi:g})" if lo is not None and hi is not None else ""
        overnight.append(f"SpO2 {spo2['averagePercentage']:g}%{rng}")
    if breathing.get(day) is not None:
        overnight.append("breathing " + _vs(breathing[day], _baseline(breathing, day), "{:.1f}/min"))

    lines = [
        "Activity" + (" (so far)" if partial else "") + ": "
        + (" · ".join(activity) or "no data"),
        "Recovery: " + " · ".join(recovery),
    ]
    if overnight:
        lines.append("Overnight: " + " · ".join(overnight))
    return lines


def _format_row(day: str, data: dict) -> str:
    steps = (data["steps"] or {}).get(day)
    azm = _azm((data["azm"] or {}).get(day))
    sleep = (data["sleep"] or {}).get(day)
    rhr = _series(data["rhr"], "beatsPerMinute").get(day)
    hrv = _series(data["hrv"], "averageHeartRateVariabilityMilliseconds").get(day)
    cells = [
        f"{int(_num(steps['countSum']) or 0):,} steps" if steps else "— steps",
        f"AZM {azm[0]}" if azm else "AZM —",
        f"sleep {_hm(timedelta(minutes=sleep[1]))}" if sleep else "sleep —",
        f"RHR {rhr:.0f}" if rhr is not None else "RHR —",
        f"HRV {hrv:.0f} ms" if hrv is not None else "HRV —",
    ]
    return f"{date.fromisoformat(day):%a %m-%d}: " + " · ".join(cells)


@tool_register(namespace="google_health")
@tool
def health_status(day: str = "", days: int = 1) -> str:
    """Roi's Pixel Watch daily health snapshot. Use for "how am I doing",
    steps / activity on a day ("yesterday's steps"), resting heart rate, HRV,
    recovery, or a week's trend.

    Args:
        day: YYYY-MM-DD, the owner-local day to report (or the last day of
            the range); "" = today, which is partial.
        days: 1 = that one day in full; 2-14 = one compact row per day ending
            on `day`.

    One day reports activity (steps, active zone minutes, calories burned,
    sedentary time), recovery (sleep that ended that morning; resting HR and
    HRV against the previous 7 days' average) and overnight vitals (SpO2,
    breathing rate). Days without data say so — the watch may not have synced.
    For sleep stages use check_sleep; for individual sessions check_workouts."""
    today = datetime.now(owner_tz()).date()
    if day:
        try:
            last = date.fromisoformat(day)
        except ValueError:
            return "day must be YYYY-MM-DD."
    else:
        last = today
    if last > today:
        return "day can't be in the future."
    days = max(1, min(int(days), _STATUS_MAX_DAYS))
    first = last - timedelta(days=days - 1)

    try:
        data = _fetch_status(first, last + timedelta(days=1))
    except GoogleHealthNotConfigured as e:
        return str(e)

    sync = data["sync"]
    sync_note = f" · watch last synced {sync:%Y-%m-%d %H:%M}" if sync and last >= today - timedelta(days=1) else ""
    failed = [k for k in ("steps", "azm", "sleep", "rhr", "hrv") if data[k] is None]
    fail_note = f"\n(Request failed for: {', '.join(failed)}.)" if failed else ""

    if days == 1:
        header = f"Health — {last:%a %Y-%m-%d}{sync_note}{_zone_note()}"
        return "\n".join([header, *_format_day(last.isoformat(), data, partial=last == today)]) + fail_note

    header = f"Health — {first:%Y-%m-%d} to {last:%Y-%m-%d}{sync_note}{_zone_note()}"
    span = [(first + timedelta(days=i)).isoformat() for i in range(days)]
    lines = [header, *(_format_row(d, data) for d in span)]
    # Today's partial count would drag the average down.
    full_days = span[:-1] if last == today else span
    steps = _series(data["steps"], "countSum")
    avg = _avg(steps.get(d) for d in full_days)
    if avg is not None:
        lines.append(f"Avg steps: {avg:,.0f}" + (" (today excluded)" if last == today else ""))
    return "\n".join(lines) + fail_note
