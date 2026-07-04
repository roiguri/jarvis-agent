"""Radarr tools — movie search, library, and management."""

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


def _find_radarr_movie(base_url: str, api_key: str, title: str) -> dict | None:
    """Case-insensitive substring match against existing Radarr library."""
    all_movies = _arr_get(base_url, api_key, "/api/v3/movie")
    title_lower = title.lower()
    return next(
        (m for m in all_movies if title_lower in m.get("title", "").lower()),
        None,
    )


@tool_register(namespace="media/radarr")
@tool
def search_radarr(title: str) -> str:
    """Search Radarr for a movie by title. Checks the local library first for accurate
    on-disk status, then falls back to TMDB lookup for titles not yet in the library.
    Use when the user asks about a movie's availability or download status."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured (missing RADARR_URL or RADARR_API_KEY)."
    try:
        # Check local library first — accurate hasFile / sizeOnDisk
        all_movies = _arr_get(base_url, api_key, "/api/v3/movie")
        title_lower = title.lower()
        local = [m for m in all_movies if title_lower in m.get("title", "").lower()]

        if local:
            lines = []
            for m in local[:5]:
                has_file = "on disk" if m.get("hasFile") else "not on disk"
                monitored = "monitored" if m.get("monitored") else "not monitored"
                status = m.get("status", "unknown")
                size_mb = round(m.get("sizeOnDisk", 0) / 1024 / 1024)
                size_str = f"{size_mb} MB" if size_mb else "—"
                lines.append(
                    f"- {m.get('title')} ({m.get('year', '?')}) [in library]: {status}, "
                    f"{has_file}, {size_str}, {monitored}"
                )
            return "Radarr results:\n" + "\n".join(lines)

        # Not in library — fall back to TMDB lookup
        results = _arr_get(base_url, api_key, "/api/v3/movie/lookup", {"term": title})
        if not results:
            return f"No results found in Radarr for '{title}'."
        lines = []
        for m in results[:3]:
            status = m.get("status", "unknown")
            lines.append(
                f"- {m.get('title')} ({m.get('year', '?')}) [not in library]: {status}"
            )
        return "Radarr results:\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code} — check the API key."


@tool_register(namespace="media/radarr")
@tool
def get_radarr_queue() -> str:
    """Get the current Radarr download queue — movies being downloaded right now.
    Returns title, quality, download progress percentage, status, and time left."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured (missing RADARR_URL or RADARR_API_KEY)."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/queue", {"includeUnknownMovieItems": "false", "pageSize": 50})
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            return "Radarr download queue is empty."
        lines = []
        for item in records:
            movie = (item.get("movie") or {}).get("title", "Unknown")
            quality = item.get("quality", {}).get("quality", {}).get("name", "?")
            status = item.get("status", "?")
            timeleft = item.get("timeleft", "?")
            size = item.get("size", 0)
            sizeleft = item.get("sizeleft", 0)
            pct = round((1 - sizeleft / size) * 100) if size else 0
            lines.append(f"- {movie} [{quality}] | {pct}% | {status} | time left: {timeleft}")
        return f"Radarr queue ({len(lines)} item(s)):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code} — check the API key."


@tool_register(namespace="media/radarr")
@tool
def get_radarr_wanted(limit: int = 20) -> str:
    """List monitored movies in Radarr that are missing and haven't been downloaded.
    Use when the user asks what movies are still missing or haven't been found yet."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/wanted/missing",
                        {"pageSize": limit, "sortKey": "releaseDate", "sortDirection": "descending"})
        records = data.get("records", []) if isinstance(data, dict) else data
        if not records:
            return "No missing monitored movies in Radarr."
        lines = []
        for m in records:
            title = m.get("title", "?")
            year = m.get("year", "?")
            release = (m.get("digitalRelease") or m.get("physicalRelease") or "?")[:10]
            lines.append(f"- {title} ({year}) — released {release}")
        total = data.get("totalRecords", len(lines)) if isinstance(data, dict) else len(lines)
        return f"Radarr missing movies ({total} total, showing {len(lines)}):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def get_radarr_library(status: str | None = None) -> str:
    """List all movies in the Radarr library. Optional filter: 'monitored' (monitored
    with no file), 'available' (file on disk), or 'missing' (monitored + no file).
    Use when the user asks what movies are in the library or wants to browse by status."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        all_movies = _arr_get(base_url, api_key, "/api/v3/movie")
        if status:
            s = status.lower()
            if s == "available":
                all_movies = [m for m in all_movies if m.get("hasFile")]
            elif s in ("missing", "monitored"):
                all_movies = [m for m in all_movies if m.get("monitored") and not m.get("hasFile")]
        if not all_movies:
            filter_str = f" matching '{status}'" if status else ""
            return f"No movies found in Radarr{filter_str}."
        lines = []
        for m in sorted(all_movies, key=lambda x: x.get("title", "")):
            year = m.get("year", "?")
            has_file = "on disk" if m.get("hasFile") else "missing"
            monitored = "monitored" if m.get("monitored") else "unmonitored"
            size_gb = round(m.get("sizeOnDisk", 0) / 1024**3, 1)
            size_str = f" ({size_gb} GB)" if size_gb > 0 else ""
            lines.append(f"- {m.get('title')} ({year}): {has_file}{size_str}, {monitored}")
        filter_str = f" — {status}" if status else ""
        return f"Radarr library{filter_str} ({len(lines)} movies):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


# ---------------------------------------------------------------------------
# Radarr — action tools
# ---------------------------------------------------------------------------

@tool_register(namespace="media/radarr")
@tool
def add_radarr_movie(title: str, quality_profile: str | None = None) -> str:
    """Add a movie to Radarr for monitoring and downloading. Looks up the movie by
    title from TMDB, then adds it with the specified quality profile (or the most
    commonly used profile in your library if not specified), with a search triggered
    immediately. Use when the user wants to download a movie."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        results = _arr_get(base_url, api_key, "/api/v3/movie/lookup", {"term": title})
        if not results:
            return f"No movie found for '{title}' in TMDB lookup."
        movie = results[0]

        profiles = _arr_get(base_url, api_key, "/api/v3/qualityprofile")
        if not profiles:
            return "No quality profiles configured in Radarr."
        if quality_profile:
            match = next((p for p in profiles if quality_profile.lower() in p["name"].lower()), None)
            if not match:
                available = ", ".join(p["name"] for p in profiles)
                return f"Quality profile '{quality_profile}' not found. Available: {available}"
            profile_id, profile_name = match["id"], match["name"]
        else:
            profile_id, profile_name = _preferred_quality_profile(base_url, api_key, "/api/v3/movie")
        root_folders = _arr_get(base_url, api_key, "/api/v3/rootfolder")
        if not root_folders:
            return "No root folders configured in Radarr."

        payload = {
            "title":            movie["title"],
            "tmdbId":           movie["tmdbId"],
            "year":             movie.get("year", 0),
            "qualityProfileId": profile_id,
            "rootFolderPath":   root_folders[0]["path"],
            "monitored":        True,
            "addOptions":       {"searchForMovie": True},
            "titleSlug":        movie.get("titleSlug", ""),
            "images":           movie.get("images", []),
        }
        result = _arr_post(base_url, api_key, "/api/v3/movie", payload)
        return (
            f"Added '{result.get('title')}' ({result.get('year')}) to Radarr "
            f"(quality: {profile_name}, root: {root_folders[0]['path']})."
        )
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 400:
            return f"Could not add movie — it may already exist in Radarr."
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def set_radarr_monitored(title: str, monitored: bool) -> str:
    """Set the monitored status for an existing movie in Radarr. Use when the user
    wants to start or stop monitoring a movie (monitored=True or False)."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        movie = _find_radarr_movie(base_url, api_key, title)
        if not movie:
            return f"Movie '{title}' not found in Radarr library."
        movie["monitored"] = monitored
        _arr_put(base_url, api_key, f"/api/v3/movie/{movie['id']}", movie)
        state = "monitored" if monitored else "unmonitored"
        return f"'{movie['title']}' is now {state} in Radarr."
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def trigger_radarr_search(title: str) -> str:
    """Trigger Radarr to search all configured indexers for a movie right now.
    Use when the user wants to force a search for a specific movie."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        movie = _find_radarr_movie(base_url, api_key, title)
        if not movie:
            return f"Movie '{title}' not found in Radarr library."
        result = _arr_post(base_url, api_key, "/api/v3/command",
                           {"name": "MoviesSearch", "movieIds": [movie["id"]]})
        return f"Search triggered for '{movie['title']}' (command ID: {result.get('id')})."
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def delete_radarr_movie(title: str) -> str:
    """Remove a movie from Radarr WITHOUT deleting the file on disk. Use when the
    user wants to stop tracking a movie but keep the existing file."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        movie = _find_radarr_movie(base_url, api_key, title)
        if not movie:
            return f"Movie '{title}' not found in Radarr library."
        _arr_delete(base_url, api_key, f"/api/v3/movie/{movie['id']}",
                    {"deleteFiles": "false"})
        return f"'{movie['title']}' removed from Radarr (file kept on disk)."
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr", destructive=True)
@tool
def delete_radarr_movie_with_files(title: str) -> str:
    """Remove a movie from Radarr AND permanently delete the file from disk.
    This is irreversible — sends a Telegram confirmation button before executing.
    Use when the user explicitly wants to free up disk space by deleting a movie."""
    from gateway.factory import get_confirmation
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        movie = _find_radarr_movie(base_url, api_key, title)
        if not movie:
            return f"Movie '{title}' not found in Radarr library."
        movie_id = movie["id"]
        movie_title = movie["title"]

        async def _do_delete() -> str:
            return await asyncio.to_thread(
                _exec_radarr_delete_files, base_url, api_key, movie_id, movie_title
            )

        return get_confirmation().request_confirmation_sync(
            description=f"Delete '{movie_title}' from Radarr AND remove the file from disk",
            action_fn=_do_delete,
            result_ok_text=f"'{movie_title}' deleted with its file.",
            result_cancel_text=f"Deletion of '{movie_title}' cancelled.",
        )
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


def _exec_radarr_delete_files(base_url: str, api_key: str, movie_id: int, movie_title: str) -> str:
    try:
        _arr_delete(base_url, api_key, f"/api/v3/movie/{movie_id}", {"deleteFiles": "true"})
        return f"'{movie_title}' and its file deleted."
    except _NETWORK_ERRORS:
        return f"Radarr unreachable when deleting '{movie_title}'."
    except httpx.HTTPStatusError as e:
        return f"Delete failed: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def remove_from_radarr_queue(title: str) -> str:
    """Remove a movie from the Radarr download queue, cancelling the in-progress
    download. Does not delete any already-downloaded files. Use when the user wants
    to cancel a stuck or unwanted movie download."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        data = _arr_get(base_url, api_key, "/api/v3/queue",
                        {"includeUnknownMovieItems": "false", "pageSize": 100})
        records = data.get("records", []) if isinstance(data, dict) else data
        title_lower = title.lower()
        matches = [
            r for r in records
            if title_lower in r.get("movie", {}).get("title", "").lower()
        ]
        if not matches:
            return f"No queue items found for '{title}' in Radarr."
        removed = []
        for item in matches:
            _arr_delete(base_url, api_key, f"/api/v3/queue/{item['id']}",
                        {"removeFromClient": "true"})
            removed.append(item.get("movie", {}).get("title", str(item["id"])))
        return f"Removed {len(removed)} item(s) from Radarr queue: {', '.join(removed)}."
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."


@tool_register(namespace="media/radarr")
@tool
def set_radarr_quality_profile(title: str, quality_profile: str) -> str:
    """Change the quality profile for an existing movie in Radarr. Use when the user
    wants to upgrade or change the quality target for a movie already in the library.
    quality_profile should be a profile name like 'HD-1080p', 'Any', 'Ultra-HD', etc."""
    base_url = os.getenv("RADARR_URL")
    api_key = os.getenv("RADARR_API_KEY")
    if not base_url or not api_key:
        return "Radarr is not configured."
    try:
        profiles = _arr_get(base_url, api_key, "/api/v3/qualityprofile")
        match = next((p for p in profiles if quality_profile.lower() in p["name"].lower()), None)
        if not match:
            available = ", ".join(p["name"] for p in profiles)
            return f"Quality profile '{quality_profile}' not found. Available: {available}"
        movie = _find_radarr_movie(base_url, api_key, title)
        if not movie:
            return f"Movie '{title}' not found in Radarr library."
        movie["qualityProfileId"] = match["id"]
        _arr_put(base_url, api_key, f"/api/v3/movie/{movie['id']}", movie)
        return f"'{movie['title']}' quality profile updated to '{match['name']}'."
    except _NETWORK_ERRORS:
        return "Error: Radarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Radarr returned {e.response.status_code}."

