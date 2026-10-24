"""The family cookbook's Recipe API: settings, one HTTP call, and its errors.

The API acts as the token's owner (the cookbook repo's docs/architecture/recipe-api.md).
Settings come from the instance's environment: COOKBOOK_API_URL (the function's base
URL) and COOKBOOK_API_TOKEN. The token is only ever sent in the Authorization header;
it never reaches a log, an error or a tool result.
"""

from __future__ import annotations

import os
import time

import httpx

TIMEOUT_S = 30.0
_CATEGORIES_TTL_S = 3600

# Set by tests: an httpx transport standing in for the API.
_transport: httpx.BaseTransport | None = None
_categories: tuple[float, dict[str, str]] | None = None


class CookbookError(Exception):
    """A failed call, phrased for the model."""


# Each API error code as one plain sentence (the API docs' error table).
_MESSAGES = {
    "unauthorized": "The cookbook token isn't valid any more. Tell the owner; it needs a new "
                    "token from the cookbook's dashboard.",
    "forbidden": "The cookbook refused: the token lacks access for this, or the owner's "
                 "cookbook account isn't approved. Tell the owner.",
    "not-found": "No such recipe, or not one the owner can see.",
    "rate-limited": "The cookbook's daily request limit is reached; it resets at midnight UTC.",
    "unavailable": "The cookbook is busy; try again in a moment.",
    "internal": "The cookbook had an error; try again later.",
    "payload-too-large": "The request is too large for the cookbook (30 MB with files).",
    "conflict": "That recipe id belongs to another recipe.",
}


def configured() -> bool:
    return bool(os.getenv("COOKBOOK_API_URL") and os.getenv("COOKBOOK_API_TOKEN"))


def request(method: str, path: str, *, params: dict | None = None, json: dict | None = None,
            data: dict | None = None, files: list | None = None) -> tuple[int, dict]:
    """(status, JSON body) for a successful call; raises CookbookError otherwise."""
    base = os.getenv("COOKBOOK_API_URL", "").rstrip("/")
    token = os.getenv("COOKBOOK_API_TOKEN", "")
    if not base or not token:
        raise CookbookError("The cookbook isn't set up here (COOKBOOK_API_URL and "
                            "COOKBOOK_API_TOKEN are needed). Tell the owner.")
    try:
        with httpx.Client(base_url=base, timeout=TIMEOUT_S, transport=_transport) as client:
            r = client.request(method, f"/v1{path}", params=_clean(params), json=json, data=data,
                               files=files, headers={"Authorization": f"Bearer {token}"})
    except httpx.TimeoutException:
        raise CookbookError("The cookbook didn't answer in time; try again.") from None
    except httpx.HTTPError as e:
        raise CookbookError(f"Couldn't reach the cookbook ({type(e).__name__}).") from None
    try:
        body = r.json()
    except ValueError:
        body = None
    if r.status_code >= 400:
        raise CookbookError(_error_text(r.status_code, body))
    if not isinstance(body, dict):
        raise CookbookError("The cookbook sent an answer that isn't JSON.")
    return r.status_code, body


def categories() -> dict[str, str]:
    """The valid category ids → their Hebrew labels, cached for an hour."""
    global _categories
    if _categories and time.monotonic() - _categories[0] < _CATEGORIES_TTL_S:
        return _categories[1]
    _, body = request("GET", "/categories")
    found = {c["id"]: c.get("label") or c["id"] for c in body.get("categories") or []
             if isinstance(c, dict) and c.get("id")}
    _categories = (time.monotonic(), found)
    return found


def _clean(params: dict | None) -> dict | None:
    return {k: v for k, v in (params or {}).items() if v not in (None, "")} or None


def _error_text(status: int, body) -> str:
    """The API's error as a sentence; platform errors (not JSON) by status."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        if status == 413:
            return _MESSAGES["payload-too-large"]
        return f"The cookbook refused the request (HTTP {status})."
    code = error.get("code") or ""
    details = [d for d in error.get("details") or [] if isinstance(d, dict)]
    if details:
        fields = "; ".join(f"{d.get('field')}: {d.get('message')}" for d in details)
        return f"The cookbook refused the recipe ({code}). Fix these fields: {fields}."
    return _MESSAGES.get(code) or f"The cookbook refused the request ({code}: {error.get('message')})."
