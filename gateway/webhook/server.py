import logging
import re
from fastapi import FastAPI, Request, HTTPException

from gateway.webhook.notifier import MediaNotificationManager

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Arr event routing table
# ---------------------------------------------------------------------------

_ARR_ROUTING: dict[str, str] = {
    "Test":                          "test",
    "Grab":                          "keepalive",
    "Download":                      "import_or_upgrade",
    "DownloadFailed":                "failure",
    "Health":                        "skip",
    "HealthIssue":                   "skip",
    "HealthRestored":                "skip",
    "ManualInteractionRequired":     "system",
    "ApplicationUpdate":             "system",
    # Internal / no user value
    "Rename":                        "skip",
    "MovieAdded":                    "skip",
    "MovieDelete":                   "skip",
    "MovieFileDelete":               "skip",
    "MovieFileDeletedForUpgrade":    "skip",
    "SeriesDelete":                  "skip",
    "EpisodeFileDelete":             "skip",
    "EpisodeFileDeletedForUpgrade":  "skip",
}


# ---------------------------------------------------------------------------
# Arr payload helpers
# ---------------------------------------------------------------------------

def _arr_category(payload: dict) -> str:
    return (
        payload.get("series", {}).get("title")
        or payload.get("movie", {}).get("title")
        or "Unknown"
    )


def _episode_label(ep: dict) -> str:
    return f"S{ep.get('seasonNumber', 0):02d}E{ep.get('episodeNumber', 0):02d}"


def _movie_label(payload: dict) -> str:
    movie = payload.get("movie", {})
    title = movie.get("title", "Unknown")
    year = movie.get("year")
    return f"{title} ({year})" if year else title


def _season_groups(payload: dict) -> dict[str, list[dict]]:
    """
    Group episodes by batch key.
    Returns {"Silo__S02": [ep_dict, ...], ...} for series,
    or {"Movies": []} for Radarr movies.
    """
    category = _arr_category(payload)
    episodes = payload.get("episodes", [])
    if not episodes:
        return {"Movies": []}
    groups: dict[str, list[dict]] = {}
    for ep in episodes:
        key = f"{category}__S{ep.get('seasonNumber', 0):02d}"
        groups.setdefault(key, []).append(ep)
    return groups


# ---------------------------------------------------------------------------
# Jellyfin payload helper
# ---------------------------------------------------------------------------

async def _process_jellyfin_payload(payload: dict, notifier: MediaNotificationManager) -> dict:
    logger.info("Jellyfin payload: %s", payload)

    notification_type: str = (
        payload.get("NotificationType")
        or payload.get("notificationType")
        or payload.get("EventType")
        or payload.get("eventType")
        or ""
    )
    normalised = notification_type.strip().lower().replace("_", "").replace("-", "").replace(" ", "")
    logger.info("Jellyfin event: raw=%r normalised=%r", notification_type, normalised)

    if normalised != "itemadded":
        if notification_type:
            await notifier.dispatch_unknown_event(f"Jellyfin:{notification_type}", payload)
            return {"status": "unknown_event_forwarded", "event": notification_type}
        logger.warning("Jellyfin payload with no event type. Keys: %s", list(payload.keys()))
        return {"status": "ignored", "reason": "no event type"}

    item_name: str = payload.get("Name") or payload.get("name") or "Unknown Item"

    # Skip season/show container objects that Jellyfin fires when a season folder is added
    if re.match(r"^season\s+\d+$", item_name, re.IGNORECASE):
        logger.info("Skipping season container: %r", item_name)
        return {"status": "ignored", "reason": "season container"}

    series_name: str  = payload.get("SeriesName") or payload.get("seriesName") or ""
    item_id: str | None   = payload.get("ItemId") or payload.get("itemId")
    series_id: str | None = payload.get("SeriesId") or payload.get("seriesId")
    season_raw            = (payload.get("SeasonNumber")
                            if payload.get("SeasonNumber") is not None
                            else payload.get("seasonNumber"))
    episode_raw           = (payload.get("IndexNumber")
                            if payload.get("IndexNumber") is not None
                            else payload.get("indexNumber"))

    if series_name and season_raw is not None:
        try:
            season_num = int(season_raw)
            key = f"{series_name}__S{season_num:02d}"
        except (ValueError, TypeError):
            season_num = None
            key = series_name  # fallback — still groups by series
        # Prefix episode label when both season and episode numbers are known
        if season_num is not None and episode_raw is not None:
            try:
                item_name = f"S{season_num:02d}E{int(episode_raw):02d} - {item_name}"
            except (ValueError, TypeError):
                pass  # keep plain title if episode number is malformed
        image_id = series_id  # use series poster for episodes
    else:
        # Only buffer as a movie if Radarr has already declared an expected download.
        # Jellyfin also fires ItemAdded for the show container itself (no SeriesName,
        # no SeasonNumber) — those look identical to movies but are never backed by
        # a Radarr event, so they would produce a spurious notification.
        if not notifier.has_pending_download("Movies"):
            logger.info("Skipping Jellyfin item %r — no pending Radarr download for Movies batch", item_name)
            return {"status": "ignored", "reason": "no pending movie download"}
        key = "Movies"
        image_id = item_id    # use movie's own poster

    await notifier.add_ready_item(key, item_name, image_id=image_id)
    return {"status": "buffered", "key": key, "item": item_name, "image_id": image_id}


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_webhook_app(notifier: MediaNotificationManager) -> FastAPI:
    app = FastAPI(title="Jarvis Webhook Receiver", docs_url=None, redoc_url=None)

    @app.post("/webhook/arr")
    async def arr_webhook(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

        event_type: str = payload.get("eventType", "")
        route = _ARR_ROUTING.get(event_type)
        logger.info("Arr webhook: eventType=%r route=%s", event_type, route or "unknown")

        if route == "test":
            return {"status": "ok", "event": "test_acknowledged"}

        if route == "skip":
            return {"status": "skipped", "event": event_type}

        if route == "keepalive":
            # Reset the timer for each season that's being grabbed
            groups = _season_groups(payload)
            for key in groups:
                await notifier.reset_timer(key)
            return {"status": "timer_reset", "keys": list(groups.keys())}

        if route == "import_or_upgrade":
            is_upgrade: bool = payload.get("isUpgrade", False)
            groups = _season_groups(payload)

            if is_upgrade:
                for key, eps in groups.items():
                    if key == "Movies":
                        await notifier.add_upgrade(key, _movie_label(payload))
                    else:
                        for ep in eps:
                            await notifier.add_upgrade(key, _episode_label(ep))
                return {"status": "upgrade_buffered", "keys": list(groups.keys())}
            else:
                # New import: tell each batch how many episodes to expect
                for key, eps in groups.items():
                    count = 1 if key == "Movies" else len(eps)
                    await notifier.record_arr_download(key, count)
                return {"status": "arr_download_recorded", "keys": list(groups.keys())}

        if route == "failure":
            episodes = payload.get("episodes", [])
            if episodes:
                groups = _season_groups(payload)
                for key, eps in groups.items():
                    for ep in eps:
                        await notifier.record_failure(key, _episode_label(ep))
            else:
                await notifier.record_failure("Movies", _movie_label(payload))
            return {"status": "failure_recorded", "event": event_type}

        if route == "system":
            await notifier.dispatch_system_alert(event_type, payload)
            return {"status": "system_alert_dispatched", "event": event_type}

        # Unknown event
        await notifier.dispatch_unknown_event(event_type, payload)
        return {"status": "unknown_event_forwarded", "event": event_type}

    @app.post("/webhook/jellyfin")
    async def jellyfin_webhook(request: Request) -> dict:
        try:
            payload = await request.json()
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
        return await _process_jellyfin_payload(payload, notifier)

    return app
