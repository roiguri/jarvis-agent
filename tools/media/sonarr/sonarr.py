"""Sonarr tools — TV series search, library, and management."""

import asyncio
import os

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.media._shared import (
    _arr_get,
    _arr_post,
    _arr_put,
    _arr_delete,
    _NETWORK_ERRORS,
    _preferred_quality_profile,
)

def _find_sonarr_series(base_url: str, api_key: str, title: str) -> dict | None:
    """Case-insensitive substring match against existing Sonarr library."""
    all_series = _arr_get(base_url, api_key, "/api/v3/series")
    title_lower = title.lower()
    return next(
        (s for s in all_series if title_lower in s.get("title", "").lower()),
        None,
    )

@tool_register(namespace="media/sonarr")
@tool
def search_sonarr(title: str) -> str:
    """Search Sonarr for a TV show by title. Checks the local library first for accurate
    on-disk status, then falls back to TVDB lookup for titles not yet in the library.
    Use when the user asks about a TV series availability or download status."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured (missing SONARR_URL or SONARR_API_KEY)."
    try:
        # Check local library first — accurate hasFile / episode counts
        all_series = _arr_get(base_url, api_key, "/api/v3/series")
        title_lower = title.lower()
        local = [s for s in all_series if title_lower in s.get("title", "").lower()]

        if local:
            lines = []
            for s in local[:3]:
                stats = s.get("statistics", {})
                on_disk = stats.get("episodeFileCount", 0)
                total = stats.get("totalEpisodeCount", "?")
                seasons = stats.get("seasonCount", "?")
                monitored = "monitored" if s.get("monitored") else "not monitored"
                status = s.get("status", "unknown")
                lines.append(
                    f"- {s.get('title')} ({s.get('year', '?')}) [in library]: {status}, "
                    f"{seasons} season(s), {on_disk}/{total} episodes on disk, {monitored}"
                )
            return "Sonarr results:\n" + "\n".join(lines)

        # Not in library — fall back to TVDB lookup
        results = _arr_get(base_url, api_key, "/api/v3/series/lookup", {"term": title})
        if not results:
            return f"No results found in Sonarr for '{title}'."
        lines = []
        for s in results[:3]:
            status = s.get("status", "unknown")
            lines.append(
                f"- {s.get('title')} ({s.get('year', '?')}) [not in library]: {status}"
            )
        return "Sonarr results:\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code} — check the API key."


@tool_register(namespace="media/sonarr")
@tool
def get_sonarr_queue() -> str:
    """Get the current Sonarr download queue — episodes being downloaded right now.
    Returns series title, episode, quality, status, and time left for each item."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured (missing SONARR_URL or SONARR_API_KEY)."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/queue", {"includeUnknownSeriesItems": "false", "pageSize": 50})
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            return "Sonarr download queue is empty."
        lines = []
        for item in records:
            series = (item.get("series") or {}).get("title", "Unknown")
            episode = (item.get("episode") or {}).get("title", "")
            quality = item.get("quality", {}).get("quality", {}).get("name", "?")
            status = item.get("status", "?")
            timeleft = item.get("timeleft", "?")
            lines.append(f"- {series} — {episode} [{quality}] | {status} | time left: {timeleft}")
        return f"Sonarr queue ({len(lines)} item(s)):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code} — check the API key."


@tool_register(namespace="media/sonarr")
@tool
def get_sonarr_wanted(limit: int = 20) -> str:
    """List monitored TV episodes in Sonarr that are missing and haven't been downloaded.
    Returns series title, episode code, and air date. Use when the user asks what shows
    are still missing or what hasn't been found yet."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/wanted/missing",
                        {"pageSize": limit, "sortKey": "airDateUtc", "sortDirection": "descending"})
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            return "No missing monitored episodes in Sonarr."
        lines = []
        for ep in records:
            series = ep.get("series", {}).get("title", "?")
            s_num = ep.get("seasonNumber", 0)
            e_num = ep.get("episodeNumber", 0)
            title = ep.get("title", "?")
            air = (ep.get("airDateUtc") or "?")[:10]
            lines.append(f"- {series} S{s_num:02d}E{e_num:02d} '{title}' (aired {air})")
        total = data.get("totalRecords", len(lines)) if isinstance(data, dict) else len(lines)
        return f"Sonarr missing episodes ({total} total, showing {len(lines)}):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def get_sonarr_library(status: str | None = None) -> str:
    """List all TV series in the Sonarr library. Optional status filter: 'continuing',
    'ended', or 'upcoming'. Returns title, year, season count, and episode file count.
    Use when the user asks what shows are in the library or wants to browse by status."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        all_series = _arr_get(base_url, api_key, "/api/v3/series")
        if status:
            all_series = [s for s in all_series if s.get("status", "").lower() == status.lower()]
        if not all_series:
            filter_str = f" with status '{status}'" if status else ""
            return f"No series found in Sonarr{filter_str}."
        # Group by status for a cleaner output
        by_status: dict[str, list[str]] = {}
        for s in sorted(all_series, key=lambda x: x.get("title", "")):
            st = s.get("status", "unknown")
            stats = s.get("statistics", {})
            on_disk = stats.get("episodeFileCount", 0)
            total = stats.get("totalEpisodeCount", "?")
            seasons = stats.get("seasonCount", "?")
            year = s.get("year", "?")
            line = f"- {s.get('title')} ({year}): {seasons} season(s), {on_disk}/{total} episodes on disk"
            by_status.setdefault(st, []).append(line)
        if status:
            # Single group — flat list
            lines = by_status.get(status.lower(), [])
            return f"Sonarr library — {status} ({len(lines)}):\n" + "\n".join(lines)
        parts = []
        for st, lines in sorted(by_status.items()):
            parts.append(f"{st.capitalize()} ({len(lines)}):\n" + "\n".join(lines))
        total_count = len(all_series)
        return f"Sonarr library ({total_count} series):\n\n" + "\n\n".join(parts)
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


# ---------------------------------------------------------------------------
# Sonarr — action tools
# ---------------------------------------------------------------------------

@tool_register(namespace="media/sonarr")
@tool
def add_sonarr_series(title: str, quality_profile: str | None = None) -> str:
    """Add a TV series to Sonarr for monitoring and downloading. Looks up the series
    by title from TVDB, then adds it with the specified quality profile (or the most
    commonly used profile in your library if not specified), with all seasons monitored
    and a search triggered immediately. Use when the user wants to start tracking a new show."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        results = _arr_get(base_url, api_key, "/api/v3/series/lookup", {"term": title})
        if not results:
            return f"No series found for '{title}' in TVDB lookup."
        series = results[0]

        profiles = _arr_get(base_url, api_key, "/api/v3/qualityprofile")
        if not profiles:
            return "No quality profiles configured in Sonarr."
        if quality_profile:
            match = next((p for p in profiles if quality_profile.lower() in p["name"].lower()), None)
            if not match:
                available = ", ".join(p["name"] for p in profiles)
                return f"Quality profile '{quality_profile}' not found. Available: {available}"
            profile_id, profile_name = match["id"], match["name"]
        else:
            profile_id, profile_name = _preferred_quality_profile(base_url, api_key, "/api/v3/series")
        root_folders = _arr_get(base_url, api_key, "/api/v3/rootfolder")
        if not root_folders:
            return "No root folders configured in Sonarr."

        payload = {
            "title":            series["title"],
            "tvdbId":           series["tvdbId"],
            "qualityProfileId": profile_id,
            "rootFolderPath":   root_folders[0]["path"],
            "monitored":        True,
            "addOptions":       {"monitor": "all", "searchForMissingEpisodes": True},
            "seasons":          series.get("seasons", []),
            "titleSlug":        series.get("titleSlug", ""),
            "images":           series.get("images", []),
            "year":             series.get("year", 0),
        }
        result = _arr_post(base_url, api_key, "/api/v3/series", payload)
        return (
            f"Added '{result.get('title')}' ({result.get('year')}) to Sonarr "
            f"(quality: {profile_name}, root: {root_folders[0]['path']})."
        )
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return f"Could not add series — it may already exist in Sonarr."
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def set_sonarr_monitored(title: str, monitored: bool) -> str:
    """Set the monitored status for an existing TV series in Sonarr. Use when the
    user wants to start or stop monitoring a show (monitored=True or False)."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        series = _find_sonarr_series(base_url, api_key, title)
        if not series:
            return f"Series '{title}' not found in Sonarr library."
        series["monitored"] = monitored
        _arr_put(base_url, api_key, f"/api/v3/series/{series['id']}", series)
        state = "monitored" if monitored else "unmonitored"
        return f"'{series['title']}' is now {state} in Sonarr."
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def trigger_sonarr_search(title: str) -> str:
    """Trigger Sonarr to search all configured indexers for missing episodes of a TV
    series right now. Use when the user wants to force a search for a specific show."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        series = _find_sonarr_series(base_url, api_key, title)
        if not series:
            return f"Series '{title}' not found in Sonarr library."
        result = _arr_post(base_url, api_key, "/api/v3/command",
                           {"name": "SeriesSearch", "seriesId": series["id"]})
        return f"Search triggered for '{series['title']}' (command ID: {result.get('id')})."
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def delete_sonarr_series(title: str) -> str:
    """Remove a TV series from Sonarr WITHOUT deleting files on disk. Use when the
    user wants to stop tracking a show but keep existing episode files."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        series = _find_sonarr_series(base_url, api_key, title)
        if not series:
            return f"Series '{title}' not found in Sonarr library."
        _arr_delete(base_url, api_key, f"/api/v3/series/{series['id']}",
                    {"deleteFiles": "false"})
        return f"'{series['title']}' removed from Sonarr (files kept on disk)."
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr", destructive=True)
@tool
def delete_sonarr_series_with_files(title: str) -> str:
    """Remove a TV series from Sonarr AND permanently delete all episode files from disk.
    This is irreversible — sends a Telegram confirmation button before executing.
    Use when the user explicitly wants to free up disk space by removing a show entirely."""
    from gateway.factory import get_confirmation
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        series = _find_sonarr_series(base_url, api_key, title)
        if not series:
            return f"Series '{title}' not found in Sonarr library."
        series_id = series["id"]
        series_title = series["title"]

        async def _do_delete() -> str:
            return await asyncio.to_thread(
                _exec_sonarr_delete_files, base_url, api_key, series_id, series_title
            )

        return get_confirmation().request_confirmation_sync(
            description=f"Delete '{series_title}' from Sonarr AND remove all files from disk",
            action_fn=_do_delete,
            result_ok_text=f"'{series_title}' deleted with all files.",
            result_cancel_text=f"Deletion of '{series_title}' cancelled.",
        )
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


def _exec_sonarr_delete_files(base_url: str, api_key: str, series_id: int, series_title: str) -> str:
    try:
        _arr_delete(base_url, api_key, f"/api/v3/series/{series_id}", {"deleteFiles": "true"})
        return f"'{series_title}' and all episode files deleted."
    except _NETWORK_ERRORS:
        return f"Sonarr unreachable when deleting '{series_title}'."
    except httpx.HTTPStatusError as e:
        return f"Delete failed: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def remove_from_sonarr_queue(title: str) -> str:
    """Remove a TV series from the Sonarr download queue, cancelling the in-progress
    download. Does not delete any already-downloaded files. Use when the user wants
    to cancel a stuck or unwanted TV download."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/queue",
                        {"includeUnknownSeriesItems": "false", "pageSize": 100})
        records = data.get("records", []) if isinstance(data, dict) else data
        title_lower = title.lower()
        matches = [
            r for r in records
            if title_lower in r.get("series", {}).get("title", "").lower()
        ]
        if not matches:
            return f"No queue items found for '{title}' in Sonarr."
        removed = []
        for item in matches:
            _arr_delete(base_url, api_key, f"/api/v3/queue/{item['id']}",
                        {"removeFromClient": "true"})
            ep = item.get("episode", {}).get("title", "") or str(item["id"])
            removed.append(ep)
        return f"Removed {len(removed)} item(s) from Sonarr queue: {', '.join(removed)}."
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


@tool_register(namespace="media/sonarr")
@tool
def set_sonarr_quality_profile(title: str, quality_profile: str) -> str:
    """Change the quality profile for an existing TV series in Sonarr. Use when the
    user wants to upgrade or change the quality target for a show already in the library.
    quality_profile should be a profile name like 'HD-1080p', 'Any', 'Ultra-HD', etc."""
    base_url = os.getenv("SONARR_URL")
    api_key = os.getenv("SONARR_API_KEY")
    if not base_url or not api_key:
        return "Sonarr is not configured."
    try:
        profiles = _arr_get(base_url, api_key, "/api/v3/qualityprofile")
        match = next((p for p in profiles if quality_profile.lower() in p["name"].lower()), None)
        if not match:
            available = ", ".join(p["name"] for p in profiles)
            return f"Quality profile '{quality_profile}' not found. Available: {available}"
        series = _find_sonarr_series(base_url, api_key, title)
        if not series:
            return f"Series '{title}' not found in Sonarr library."
        series["qualityProfileId"] = match["id"]
        _arr_put(base_url, api_key, f"/api/v3/series/{series['id']}", series)
        return f"'{series['title']}' quality profile updated to '{match['name']}'."
    except _NETWORK_ERRORS:
        return "Error: Sonarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Sonarr returned {e.response.status_code}."


