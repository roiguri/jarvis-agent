"""Arbox client: the authenticated HTTP surface and the WOD-programming parser.

Private to the skill. Every call to the gym's API goes through `_arbox_post`,
so the auth headers and the expired-session message exist in one place. The
track-scoring helpers turn the gym's multi-track daily programming into the
single track the owner actually follows.
"""

import os

import requests

ARBOX_BASE = "https://apiappv2.arboxapp.com"


# Arbox only allows registering for a class up to this many hours in advance
# (rolling, time-precise). This is the only window over which a complete fetch
# yields an authoritative registered set, which is what makes safely deleting
# dropped-registration "ghost" rows possible. Both the Arbox fetch window and
# the reconciliation window derive from this constant.
ARBOX_REGISTRATION_HORIZON_HOURS = 72


def _arbox_headers() -> dict:
    token = os.environ.get("ARBOX_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("ARBOX_ACCESS_TOKEN not set in environment")
    return {
        "accesstoken": token,
        "whitelabel": os.environ.get("ARBOX_WHITELABEL", ""),
        "version": "11",
        "referername": "app",
        "content-type": "application/json",
    }


def _arbox_post(path: str, body: dict) -> dict:
    resp = requests.post(f"{ARBOX_BASE}{path}", headers=_arbox_headers(), json=body, timeout=15)
    if resp.status_code == 401:
        raise RuntimeError("Arbox session expired. Please update ARBOX_ACCESS_TOKEN in /app/secrets/.env and restart the service.")
    resp.raise_for_status()
    return resp.json()


# Tracks Roi follows: the WOD (and Endurance on Saturday) at his branch. PUMP /
# W.LIFTING / other-branch tracks are dropped from briefings (reachable via
# get_daily_programming). Branch keyword is env-overridable.
ARBOX_WOD_BRANCH = os.environ.get("ARBOX_WOD_BRANCH", "neve tzedek").strip().lower()


_FOLLOWED_TRACKS = ("wod", "endurance")


def _norm_category(name: str) -> str:
    """Case-fold and collapse whitespace for tolerant matching."""
    return " ".join((name or "").split()).lower()


def _score_track(category: str, prefer_category: str | None = None) -> int:
    """Rank a track for Roi's program (0 = not followed, skip). prefer_category
    is the class he booked — an exact-match tie-breaker, not a requirement."""
    cat = _norm_category(category)
    if not cat:
        return 0
    if prefer_category and cat == _norm_category(prefer_category):
        return 100
    followed = any(k in cat for k in _FOLLOWED_TRACKS)
    branch = bool(ARBOX_WOD_BRANCH) and ARBOX_WOD_BRANCH in cat
    if followed and branch:
        return 40  # e.g. WOD/ENDURANCE NEVE TZEDEK
    if branch:
        return 30
    if followed:
        return 20  # WOD at another branch — last resort
    return 0  # PUMP / W.LIFTING / OPEN GYM


def _parse_wod_tracks(date_str: str) -> list[tuple[str, str]]:
    """All posted (category, comment) tracks for a date. [] if none/on error."""
    try:
        data = _arbox_post("/api/v2/logbook/workouts", {"date": date_str})
    except Exception:
        return []
    tracks: list[tuple[str, str]] = []
    for group_list in data.get("data", []):
        for group in group_list:
            for exercise in group:
                comment = (exercise.get("comment") or "").strip()
                if not comment:
                    continue
                category = (exercise.get("box_categories") or {}).get("name", "")
                tracks.append((category, comment))
    return tracks


def _get_session_programming(date_str: str, prefer_category: str | None = None) -> str:
    """Full text of the single track Roi follows for a date. "" if none posted.

    Picks the best-scoring track (see _score_track) instead of concatenating all
    of them; prefer_category is the registered class's category.
    """
    best: tuple[int, str, str] | None = None  # (score, category, comment)
    for category, comment in _parse_wod_tracks(date_str):
        score = _score_track(category, prefer_category)
        if score > 0 and (best is None or score > best[0]):
            best = (score, category, comment)
    if best is None:
        return ""
    _, category, comment = best
    return f"[{category}] {comment}" if category else comment
