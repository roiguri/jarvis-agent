"""Shared Israel-time helpers — the single home of the local timezone."""

import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def now_israel() -> datetime:
    return datetime.now(ISRAEL_TZ)


def israel_week_bounds(anchor: datetime | None = None) -> tuple[str, str]:
    """The Sunday-anchored week containing `anchor` (aware; default: now),
    as inclusive YYYY-MM-DD strings, computed Israel-local."""
    local = (anchor or now_israel()).astimezone(ISRAEL_TZ)
    start = local - timedelta(days=(local.weekday() + 1) % 7)
    return start.strftime("%Y-%m-%d"), (start + timedelta(days=6)).strftime("%Y-%m-%d")


# --- Owner-location awareness (travel mode) --------------------------------
#
# The owner's current timezone is code-owned state in DATA_DIR/agent/, set only
# by the /tz slash command — never by the model. File absent = owner is home,
# and every consumer takes exactly the same code path it always did; the file
# existing is the single switch that changes anything.

_OWNER_TZ_PATH = None  # resolved lazily so importing timeutils never needs config


def _owner_tz_path() -> str:
    global _OWNER_TZ_PATH
    if _OWNER_TZ_PATH is None:
        import config
        _OWNER_TZ_PATH = os.path.join(config.DATA_DIR, "agent", "owner_tz.json")
    return _OWNER_TZ_PATH


def owner_tz_name() -> str | None:
    """The IANA name of the owner's away timezone, or None when home.

    Any unreadable/invalid file reads as home: failing toward Israel keeps the
    home behavior the default rather than an error state.
    """
    try:
        with open(_owner_tz_path(), encoding="utf-8") as f:
            name = json.load(f).get("timezone")
        if not isinstance(name, str):
            return None
        ZoneInfo(name)
        return name
    except (OSError, ValueError, KeyError, ZoneInfoNotFoundError):
        return None


def owner_tz() -> ZoneInfo:
    """The owner's current timezone — ISRAEL_TZ unless /tz set an away zone."""
    name = owner_tz_name()
    return ZoneInfo(name) if name else ISRAEL_TZ


def set_owner_tz(name: str) -> None:
    """Record the owner's away timezone. Raises ZoneInfoNotFoundError (or
    ValueError) on a name the tz database doesn't know."""
    ZoneInfo(name)  # validate before touching disk
    path = _owner_tz_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"timezone": name}, f)
    os.replace(tmp, path)


def clear_owner_tz() -> None:
    """Back to home: remove the away file (idempotent)."""
    try:
        os.remove(_owner_tz_path())
    except FileNotFoundError:
        pass
