"""Shared GitHub REST helpers (non-tool).

All helpers take the token as an argument — no module-level config. Mirrors
the shape of tools/media/_shared.py (httpx.Client, raise_for_status, a
network-error tuple).
"""

import os

import httpx

GITHUB_API = "https://api.github.com"

_NETWORK_ERRORS = (httpx.ConnectError, httpx.TimeoutException)


def _gh_token() -> str | None:
    """The GitHub PAT, or None if unconfigured."""
    return os.getenv("GITHUB_TOKEN")


def _gh_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _gh_get(token: str, path: str, params: dict | None = None) -> list | dict:
    """Shared GET helper (Bearer auth)."""
    with httpx.Client(timeout=10) as client:
        r = client.get(
            f"{GITHUB_API}{path}",
            headers=_gh_headers(token),
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


def _gh_post(token: str, path: str, json_body: dict) -> dict:
    """Shared POST helper."""
    with httpx.Client(timeout=15) as client:
        r = client.post(
            f"{GITHUB_API}{path}",
            headers=_gh_headers(token),
            json=json_body,
        )
        r.raise_for_status()
        return r.json()


def _gh_patch(token: str, path: str, json_body: dict) -> dict:
    """Shared PATCH helper."""
    with httpx.Client(timeout=15) as client:
        r = client.patch(
            f"{GITHUB_API}{path}",
            headers=_gh_headers(token),
            json=json_body,
        )
        r.raise_for_status()
        return r.json()
