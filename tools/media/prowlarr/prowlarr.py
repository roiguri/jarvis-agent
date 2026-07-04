"""Prowlarr tools — indexer search."""

import os

import httpx
from langchain_core.tools import tool

from tools.registry import tool_register
from tools.media._shared import _arr_get, _NETWORK_ERRORS


@tool_register(namespace="media/prowlarr")
@tool
def search_prowlarr(query: str) -> str:
    """Search all configured Prowlarr indexers for NZB/torrent availability of a title.
    Returns indexer name, seeders, size, and age for the top results. Use when the user
    wants to know if content is available to download from configured indexers."""
    base_url = os.getenv("PROWLARR_URL")
    api_key = os.getenv("PROWLARR_API_KEY")
    if not base_url or not api_key:
        return "Prowlarr is not configured (missing PROWLARR_URL or PROWLARR_API_KEY)."
    try:
        results = _arr_get(base_url, api_key, "/api/v1/search", {"query": query, "type": "search", "limit": 10})
        if not results:
            return f"No indexer results found in Prowlarr for '{query}'."
        lines = []
        for r in results[:5]:
            indexer = r.get("indexer", "?")
            name = r.get("title", "?")
            seeders = r.get("seeders", "?")
            size_mb = round(r.get("size", 0) / 1024 / 1024)
            age = r.get("age", "?")
            lines.append(f"- [{indexer}] {name} | {seeders} seeders | {size_mb} MB | {age} days old")
        return f"Prowlarr results for '{query}':\n" + "\n".join(lines)
    except _NETWORK_ERRORS:
        return "Error: Prowlarr is unreachable."
    except httpx.HTTPStatusError as e:
        return f"Error: Prowlarr returned {e.response.status_code} — check the API key."

