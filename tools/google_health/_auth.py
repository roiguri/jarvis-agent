"""Google Health API OAuth2 — refresh-token → cached access token.

The codebase's first OAuth2 integration (Arbox uses a static token). The
one-time consent that mints the refresh token is done out-of-band (see
``tools/google_health/SETUP.md``); this module only keeps a valid access
token at request time.

Client credentials + the long-lived refresh token come from env vars in
``/app/secrets/.env`` (same convention as ``ARBOX_ACCESS_TOKEN``):

    GOOGLE_HEALTH_CLIENT_ID
    GOOGLE_HEALTH_CLIENT_SECRET
    GOOGLE_HEALTH_REFRESH_TOKEN

``google-auth`` (already a dependency) handles the refresh-token exchange and
expiry tracking; the resulting access token is cached process-wide and only
re-minted when expired.
"""

from __future__ import annotations

import os
import threading

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError

TOKEN_URI = "https://oauth2.googleapis.com/token"

# Read-only scopes for sleep, workouts/activity, and biometrics (heart rate).
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]

# Actionable message relayed verbatim to Roi (mirrors the Arbox 401 contract).
_REAUTH_MSG = (
    "Google Health authorization expired or was revoked. Re-mint the refresh "
    "token (see tools/google_health/SETUP.md), update "
    "GOOGLE_HEALTH_REFRESH_TOKEN in /app/secrets/.env, and restart the service."
)


class GoogleHealthNotConfigured(RuntimeError):
    """Required env vars are missing — surfaced to the model as a plain string."""


_creds: Credentials | None = None
_lock = threading.Lock()


def _build_credentials() -> Credentials:
    client_id = os.environ.get("GOOGLE_HEALTH_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_HEALTH_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_HEALTH_REFRESH_TOKEN", "")
    if not (client_id and client_secret and refresh_token):
        raise GoogleHealthNotConfigured(
            "Google Health is not configured (missing GOOGLE_HEALTH_CLIENT_ID, "
            "GOOGLE_HEALTH_CLIENT_SECRET, or GOOGLE_HEALTH_REFRESH_TOKEN)."
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri=TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )


def get_access_token() -> str:
    """A valid Bearer access token, refreshed only when expired.

    Raises GoogleHealthNotConfigured if env vars are missing, or RuntimeError
    with an actionable re-auth message if the refresh token is revoked.
    """
    global _creds
    with _lock:
        if _creds is None:
            _creds = _build_credentials()
        if not _creds.valid:
            try:
                _creds.refresh(Request())
            except RefreshError as e:
                _creds = None
                raise RuntimeError(_REAUTH_MSG) from e
        return _creds.token
