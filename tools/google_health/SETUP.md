# google_health — one-time setup

Read-only access to Roi's Pixel Watch data (sleep, workouts, resting HR, HRV)
via the Google Health API v4. Done once; the service then auto-refreshes
short-lived access tokens from a long-lived refresh token.

## 1. Google Cloud project

1. [console.cloud.google.com](https://console.cloud.google.com) → select (or create) a project.
2. **APIs & Services → Library** → enable **"Google Health API"**.
3. **OAuth consent screen** (a.k.a. *Google Auth Platform*):
   - **Branding**: app name + your support/developer email.
   - **Audience**: User type *External*; **Publishing status → Publish app → In production**.
     Unverified is fine for a single personal user — do **not** submit for
     verification. Consent will show a one-time "Google hasn't verified this
     app" screen → *Advanced → Continue*.
   - **Data Access**: add the three scopes:
     - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
     - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
     - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - **Clients**: create an OAuth client of type **Desktop app** → note the
     **Client ID** and **Client secret**.

## 2. Mint the refresh token (once, on a machine with a browser)

`GOOGLE_HEALTH_CLIENT_ID` / `GOOGLE_HEALTH_CLIENT_SECRET` are copied straight
from the Desktop client. The refresh token is not shown in the console — mint
it with this throwaway snippet (stdlib + `requests`; run it locally, paste the
client id/secret when prompted, complete consent, copy the printed token):

```python
import urllib.parse, webbrowser, requests
from http.server import BaseHTTPRequestHandler, HTTPServer

CID = input("client id: ").strip(); CSEC = input("client secret: ").strip()
SCOPES = ("https://www.googleapis.com/auth/googlehealth.sleep.readonly "
          "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly "
          "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly")
REDIRECT = "http://127.0.0.1:8765/"
url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CID, "redirect_uri": REDIRECT, "response_type": "code",
    "scope": SCOPES, "access_type": "offline", "prompt": "consent"})
print(url); webbrowser.open(url)
code = {}
class H(BaseHTTPRequestHandler):
    def do_GET(s):
        code["c"] = urllib.parse.parse_qs(urllib.parse.urlparse(s.path).query).get("code", [None])[0]
        s.send_response(200); s.end_headers(); s.wfile.write(b"Done, return to terminal.")
    def log_message(s, *a): pass
HTTPServer(("127.0.0.1", 8765), H).handle_request()
tok = requests.post("https://oauth2.googleapis.com/token", data={
    "code": code["c"], "client_id": CID, "client_secret": CSEC,
    "redirect_uri": REDIRECT, "grant_type": "authorization_code"}, timeout=15).json()
print("REFRESH TOKEN:", tok.get("refresh_token") or tok)
```

Headless box (no local browser): copy the printed URL to a browser on any
machine, authorize, then copy the `code=` value from the failed
`127.0.0.1:8765` redirect URL and exchange it manually with the same
`POST https://oauth2.googleapis.com/token` body.

## 3. Configure & verify

Add to `/app/secrets/.env`:

```
GOOGLE_HEALTH_CLIENT_ID=...
GOOGLE_HEALTH_CLIENT_SECRET=...
GOOGLE_HEALTH_REFRESH_TOKEN=...
```

Quick terminal sanity-check before relying on it (uses the production auth path):

```bash
/app/jarvis_code/venv/bin/python3 - <<'PY'
from dotenv import load_dotenv; load_dotenv("/app/secrets/.env")
import sys; sys.path.insert(0, "/app/jarvis_code")
from tools.google_health.google_health_tools import check_workouts, check_biometrics
print(check_workouts.invoke({"since_date": "2026-01-01"}))
print(check_biometrics.invoke({"days": 7}))
PY
```

Then restart the service. If the refresh token is later revoked/expired, the
tools return an actionable message telling Roi to redo step 2 — re-mint, update
`GOOGLE_HEALTH_REFRESH_TOKEN`, restart.
