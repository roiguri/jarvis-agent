"""Media system tools — combined Sonarr/Radarr health and library overview."""

import os

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.media._shared import _arr_get, _NETWORK_ERRORS


@tool_register(namespace="media/system")
@tool
def get_media_system_health() -> str:
    """Check health, version, and disk space for Sonarr and Radarr. Returns any
    warnings or errors, application versions, and free disk space per path. Use when
    the user asks about system status, health alerts, updates, or disk space."""
    results = []
    for name, url_var, key_var in [
        ("Sonarr", "SONARR_URL", "SONARR_API_KEY"),
        ("Radarr", "RADARR_URL", "RADARR_API_KEY"),
    ]:
        base_url = os.getenv(url_var)
        api_key = os.getenv(key_var)
        if not base_url or not api_key:
            results.append(f"{name}: not configured.")
            continue
        try:
            health = _arr_get(base_url, api_key, "/api/v3/health")
            sys_status = _arr_get(base_url, api_key, "/api/v3/system/status")
            diskspace = _arr_get(base_url, api_key, "/api/v3/diskspace")

            version = sys_status.get("version", "?")
            issues = [f"  [{h['type'].upper()}] {h['message']}" for h in health] if health else []
            disks = []
            for d in diskspace:
                free_gb = round(d.get("freeSpace", 0) / 1024 ** 3, 1)
                total_gb = round(d.get("totalSpace", 0) / 1024 ** 3, 1)
                disks.append(f"  {d.get('path', '?')}: {free_gb}/{total_gb} GB free")

            block = [f"{name} v{version}:"]
            block += issues if issues else ["  Health: OK"]
            block += disks
            results.append("\n".join(block))
        except _NETWORK_ERRORS:
            results.append(f"{name}: unreachable.")
        except httpx.HTTPStatusError as e:
            results.append(f"{name}: HTTP {e.response.status_code}.")

    return "\n\n".join(results)


@tool_register(namespace="media/system")
@tool
def get_library_overview() -> str:
    """Get a high-level count overview of the Sonarr and Radarr libraries — total
    series and movies, broken down by status. Use when the user asks how much content
    is in the library or wants a quick summary of what's available."""
    lines = []

    sonarr_url = os.getenv("SONARR_URL")
    sonarr_key = os.getenv("SONARR_API_KEY")
    if sonarr_url and sonarr_key:
        try:
            all_series = _arr_get(sonarr_url, sonarr_key, "/api/v3/series")
            by_status: dict[str, int] = {}
            for s in all_series:
                st = s.get("status", "unknown")
                by_status[st] = by_status.get(st, 0) + 1
            breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_status.items()))
            lines.append(f"Sonarr: {len(all_series)} series ({breakdown})")
        except (_NETWORK_ERRORS[0], _NETWORK_ERRORS[1]):
            lines.append("Sonarr: unreachable")
        except httpx.HTTPStatusError:
            lines.append("Sonarr: error fetching library")

    radarr_url = os.getenv("RADARR_URL")
    radarr_key = os.getenv("RADARR_API_KEY")
    if radarr_url and radarr_key:
        try:
            all_movies = _arr_get(radarr_url, radarr_key, "/api/v3/movie")
            on_disk = sum(1 for m in all_movies if m.get("hasFile"))
            monitored_missing = sum(1 for m in all_movies if m.get("monitored") and not m.get("hasFile"))
            lines.append(
                f"Radarr: {len(all_movies)} movies "
                f"({on_disk} on disk, {monitored_missing} monitored/missing)"
            )
        except (_NETWORK_ERRORS[0], _NETWORK_ERRORS[1]):
            lines.append("Radarr: unreachable")
        except httpx.HTTPStatusError:
            lines.append("Radarr: error fetching library")

    return "\n".join(lines) if lines else "No media services configured."
