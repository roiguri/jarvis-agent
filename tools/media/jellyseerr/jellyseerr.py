"""Jellyseerr tools — request status and submission."""

import os
from urllib.parse import quote as _urlquote

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.media._shared import _arr_get, _arr_post, _NETWORK_ERRORS


def _jellyseerr_search(base_url: str, api_key: str, title: str) -> dict:
    """
    Search Jellyseerr /api/v1/search, manually building the query string to
    avoid httpx re-encoding issues that cause Jellyseerr 400 errors.
    """
    from urllib.parse import urlencode
    qs = urlencode({"query": title, "page": 1}, quote_via=_urlquote)
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{base_url}/api/v1/search?{qs}",
            headers={"X-Api-Key": api_key},
        )
        r.raise_for_status()
        return r.json()


@tool_register(namespace="media/jellyseerr")
@tool
def search_jellyseerr(title: str) -> str:
    """Search Jellyseerr for a movie or TV show. Returns request status (pending,
    approved, available), who requested it, and overall media availability.
    Use when the user asks about request status or whether something has been requested."""
    base_url = os.getenv("JELLYSEERR_URL")
    api_key = os.getenv("JELLYSEERR_API_KEY")
    if not base_url or not api_key:
        return "Jellyseerr is not configured (missing JELLYSEERR_URL or JELLYSEERR_API_KEY)."

    MEDIA_STATUS = {
        1: "unknown", 2: "pending", 3: "processing",
        4: "partially available", 5: "available",
    }

    try:
        data = _jellyseerr_search(base_url, api_key, title)
        results = data.get("results", []) if isinstance(data, dict) else data
        if not results:
            return f"No results found in Jellyseerr for '{title}'."

        lines = []
        for item in results[:3]:
            media_type = item.get("mediaType", "unknown")
            name = item.get("title") or item.get("name", "?")
            year = (item.get("releaseDate") or item.get("firstAirDate") or "")[:4]
            media_info = item.get("mediaInfo") or {}
            media_status = MEDIA_STATUS.get(media_info.get("status"), "not requested")
            requests = media_info.get("requests", [])
            requester = requests[0].get("requestedBy", {}).get("displayName", "—") if requests else "—"
            lines.append(
                f"- {name} ({year}) [{media_type}]: {media_status}"
                + (f", requested by {requester}" if requester != "—" else "")
            )

        return f"Jellyseerr results for '{title}':\n" + "\n".join(lines)

    except _NETWORK_ERRORS:
        return "Error: Jellyseerr is unreachable."
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.json().get("message") or e.response.text[:200]
        except Exception:
            body = e.response.text[:200]
        return f"Error: Jellyseerr returned {e.response.status_code} — {body}"


@tool_register(namespace="media/jellyseerr")
@tool
def get_jellyseerr_requests(status: str | None = None) -> str:
    """List all media requests in Jellyseerr. Optional status filter: 'pending',
    'approved', 'declined', 'available'. Use when the user asks about pending or
    recent requests."""
    base_url = os.getenv("JELLYSEERR_URL")
    api_key = os.getenv("JELLYSEERR_API_KEY")
    if not base_url or not api_key:
        return "Jellyseerr is not configured."
    REQUEST_STATUSES = {1: "pending", 2: "approved", 3: "declined", 4: "failed"}
    try:
        params: dict = {"take": 20, "skip": 0}
        if status:
            params["filter"] = status
        data = _arr_get(base_url, api_key, "/api/v1/request", params)
        results = data.get("results", []) if isinstance(data, dict) else data
        if not results:
            return "No requests found in Jellyseerr."
        lines = []
        for req in results:
            media = req.get("media") or {}
            # Title lives in different fields depending on Jellyseerr version and media type
            name = (
                media.get("title")
                or media.get("name")
                or media.get("originalTitle")
                or media.get("originalName")
                or (f"tmdbId:{media.get('tmdbId')}" if media.get("tmdbId") else None)
                or (f"tvdbId:{media.get('tvdbId')}" if media.get("tvdbId") else None)
                or "?"
            )
            mtype = media.get("mediaType", "?")
            req_status = REQUEST_STATUSES.get(req.get("status"), "unknown")
            requester = (req.get("requestedBy") or {}).get("displayName", "?")
            created = (req.get("createdAt") or "?")[:10]
            lines.append(f"- {name} [{mtype}] | {req_status} | by {requester} on {created}")
        return f"Jellyseerr requests ({len(lines)}):\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Jellyseerr is unreachable."
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.json().get("message") or e.response.text[:200]
        except Exception:
            body = e.response.text[:200]
        return f"Error: Jellyseerr returned {e.response.status_code} — {body}"


@tool_register(namespace="media/jellyseerr")
@tool
def request_media(title: str, media_type: str) -> str:
    """Submit a media request to Jellyseerr. media_type must be 'movie' or 'tv'.
    Searches for the title then submits the request. Use when the user wants to
    request a movie or TV show to be added to the library."""
    base_url = os.getenv("JELLYSEERR_URL")
    api_key = os.getenv("JELLYSEERR_API_KEY")
    if not base_url or not api_key:
        return "Jellyseerr is not configured."
    if media_type not in ("movie", "tv"):
        return "media_type must be 'movie' or 'tv'."
    try:
        data = _jellyseerr_search(base_url, api_key, title)
        results = data.get("results", []) if isinstance(data, dict) else data
        matches = [r for r in results if r.get("mediaType") == media_type]
        if not matches:
            return f"No {media_type} results found for '{title}' in Jellyseerr."
        item = matches[0]
        media_id = item.get("id")
        name = item.get("title") or item.get("name", "?")
        payload: dict = {"mediaType": media_type, "mediaId": media_id}
        if media_type == "tv":
            # Jellyseerr requires explicit season list for TV requests
            seasons = [s["seasonNumber"] for s in item.get("seasons", []) if s.get("seasonNumber", 0) > 0]
            payload["seasons"] = seasons
        result = _arr_post(base_url, api_key, "/api/v1/request", payload)
        return f"Request submitted for '{name}' [{media_type}] (request ID: {result.get('id')})."
    except _NETWORK_ERRORS:
        return "Error: Jellyseerr is unreachable."
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 409:
            return f"'{title}' has already been requested in Jellyseerr."
        body = ""
        try:
            body = e.response.json().get("message") or e.response.text[:200]
        except Exception:
            body = e.response.text[:200]
        return f"Error: Jellyseerr returned {e.response.status_code} — {body}"

