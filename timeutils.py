"""Shared Israel-time helpers — the single home of the local timezone."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def now_israel() -> datetime:
    return datetime.now(ISRAEL_TZ)


def israel_week_bounds(anchor: datetime | None = None) -> tuple[str, str]:
    """The Sunday-anchored week containing `anchor` (aware; default: now),
    as inclusive YYYY-MM-DD strings, computed Israel-local."""
    local = (anchor or now_israel()).astimezone(ISRAEL_TZ)
    start = local - timedelta(days=(local.weekday() + 1) % 7)
    return start.strftime("%Y-%m-%d"), (start + timedelta(days=6)).strftime("%Y-%m-%d")
