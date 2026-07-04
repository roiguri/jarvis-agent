"""Shared Sonarr/Radarr/Prowlarr/Jellyseerr helpers (non-tool).

Only helpers used by more than one media submodule live here. Single-consumer
helpers live in their owning module. All take base_url/api_key as arguments —
no module-level config.
"""

import httpx


def _arr_get(base_url: str, api_key: str, path: str, params: dict | None = None) -> list | dict:
    """Shared GET helper for Sonarr/Radarr/Prowlarr/Jellyseerr (X-Api-Key auth)."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{base_url}{path}",
            headers={"X-Api-Key": api_key},
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


def _arr_post(base_url: str, api_key: str, path: str, json_body: dict) -> dict:
    """Shared POST helper."""
    with httpx.Client(timeout=15) as client:
        r = client.post(
            f"{base_url}{path}",
            headers={"X-Api-Key": api_key},
            json=json_body,
        )
        r.raise_for_status()
        return r.json()


def _arr_put(base_url: str, api_key: str, path: str, json_body: dict) -> dict:
    """Shared PUT helper."""
    with httpx.Client(timeout=15) as client:
        r = client.put(
            f"{base_url}{path}",
            headers={"X-Api-Key": api_key},
            json=json_body,
        )
        r.raise_for_status()
        return r.json()


def _arr_delete(base_url: str, api_key: str, path: str, params: dict | None = None) -> None:
    """Shared DELETE helper."""
    with httpx.Client(timeout=15) as client:
        r = client.delete(
            f"{base_url}{path}",
            headers={"X-Api-Key": api_key},
            params=params or {},
        )
        r.raise_for_status()


_NETWORK_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


def _preferred_quality_profile(base_url: str, api_key: str, library_path: str) -> tuple[int, str]:
    """
    Returns (qualityProfileId, profileName) for the most commonly used profile
    in the existing library. Falls back to the first configured profile.
    """
    profiles = _arr_get(base_url, api_key, "/api/v3/qualityprofile")
    if not profiles:
        raise ValueError("No quality profiles configured.")
    try:
        existing = _arr_get(base_url, api_key, library_path)
        if existing:
            counts: dict[int, int] = {}
            for item in existing:
                pid = item.get("qualityProfileId")
                if pid:
                    counts[pid] = counts.get(pid, 0) + 1
            if counts:
                best_id = max(counts, key=counts.get)
                match = next((p for p in profiles if p["id"] == best_id), None)
                if match:
                    return match["id"], match["name"]
    except Exception:
        pass
    return profiles[0]["id"], profiles[0]["name"]
