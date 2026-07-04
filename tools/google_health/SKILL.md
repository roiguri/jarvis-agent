---
name: google_health
description: Pixel Watch sleep, workouts, resting heart rate & HRV (Google Health API)
---
- This is Roi's Pixel Watch health data via the Google Health API. The backend injects the OAuth Bearer token automatically — never ask Roi for credentials.
- How he slept / last night's sleep / sleep stages → `check_sleep` (nights=1 = last night; pass more nights for a trend). The API does NOT expose Fitbit's 0–100 sleep score; if Roi asks for "sleep score", report the **sleep efficiency** the tool returns (asleep / in-bed) and briefly say the actual score isn't published over the API.
- Did Roi work out today / recently → `check_workouts` (defaults to today, Asia/Jerusalem). Pass `since_date` / `until_date` (both YYYY-MM-DD, inclusive) for a range; pass them equal for a single day. Sessions tagged `(manual)` have MET-estimated kcal/HR, not measured — don't over-interpret them.
- Resting heart rate or HRV → `check_biometrics` (days=1 = today).
- All three are read-only. Report the numbers plainly; if a tool returns "no data returned", the watch likely hasn't synced yet — say so rather than guessing.
- If any tool reports that Google Health authorization expired, relay that message to Roi verbatim and do not retry (he must re-run the consent script and update the env file).
