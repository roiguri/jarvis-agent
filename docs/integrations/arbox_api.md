# Arbox API Documentation

Captured via mitmproxy from the Arbox gym app. Base URL: `https://apiappv2.arboxapp.com`

## Authentication

All requests require these headers:

| Header | Value |
|--------|-------|
| `accesstoken` | JWT token (long-lived; stored in `ARBOX_ACCESS_TOKEN` env var) |
| `whitelabel` | `<GYM_WHITELABEL>` (the gym's app brand identifier) |
| `version` | `11` |
| `referername` | `app` |
| `content-type` | `application/json` |

On 401: access token has expired. Update `ARBOX_ACCESS_TOKEN` in `/app/secrets/.env` and restart.

## Constants (stored in .env)

| Variable | Value | Notes |
|----------|-------|-------|
| `ARBOX_BOX_ID` | `<box_id>` | Gym's internal ID |
| `ARBOX_LOCATIONS_BOX_ID` | `<locations_box_id>` | Location-specific ID |
| `ARBOX_MEMBERSHIP_USER_ID` | `<membership_user_id>` | Required for class registration |

Discoverable via `/api/v2/user/profile` and `/api/v2/boxes/<box_id>/memberships/1/false`.

## MFA Login Flow (for token renewal)

1. **Request SMS code**: `POST /api/v2/mfa`
   ```json
   { "type": "sms", "value": "PHONE_NUMBER" }
   ```
   Returns an `id` (MFA session ID).

2. **Submit code**: `POST /api/v2/user/mfa/login`
   ```json
   { "type": "sms", "value": "PHONE_NUMBER", "code": "1234", "id": MFA_ID }
   ```
   Returns `data.token` (new accesstoken) and `data.refreshToken`.

---

## Endpoints

### 1. Get Class Schedule

Fetches all classes in a date range for the gym location.

- **Method**: `POST`
- **URL**: `/api/v2/schedule/betweenDates`
- **Request Body**:
  ```json
  {
      "from": "2026-05-09T00:00:00.000Z",
      "to": "2026-05-15T23:59:59.000Z",
      "locations_box_id": "<locations_box_id>",
      "boxes_id": "<box_id>"
  }
  ```
- **Key response fields** (per class object):
  - `id` — the `schedule_id` (globally unique, use as `arbox_class_id`)
  - `date` — `"YYYY-MM-DD"`
  - `time` / `end_time` — `"HH:MM"` in Asia/Jerusalem timezone
  - `status` — `"active"` if class is running
  - `past` — `1` if class has already happened, `0` if future
  - `user_booked` — `null` if not registered; equals the `schedule_user_id` (int) if registered
  - `user_in_standby` — non-null if on waitlist
  - `workout_id` — usually `null`; coaches may link WODs but often don't
  - `box_categories.name` — class type: `"WOD"`, `"W.LIFTING"`, etc.
  - `booked_users[].membership_user_fk` — compare against `ARBOX_MEMBERSHIP_USER_ID` to find own entry
  - `booked_users[].checked_in` — `1` if user actually attended (use for attendance sync)
- **Notes**: Does NOT include WOD text. Call `logbook/workouts` separately for WOD description.

---

### 2. Get WOD Programming for a Date

Returns the workout program (WOD) for a given date. This is what shows in the app as the class workout.

- **Method**: `POST`
- **URL**: `/api/v2/logbook/workouts`
- **Request Body**:
  ```json
  { "date": "2026-05-08" }
  ```
- **Key response fields** (data is a nested array `data[][][]`):
  - `comment` — full WOD text (e.g., "16 rounds for time with a partner...\n7 hang power snatch")
  - `box_categories.name` — category: `"WOD"`, `"W.LIFTING"`, `"Strength"`, etc.
  - `box_sections.name` — section: `"Metcon"`, `"Skill"`, etc.
  - `name` — the date string `"YYYY-MM-DD"`
- **Notes**: Returns WOD programming, NOT attendance history. Empty `{"data": []}` means no WOD posted for that date yet (coaches post WODs ~5 days ahead).

**Flatten the nested arrays**: `data` is `[sections][groups][exercises]`. Collect all `comment` fields.

---

### 3. Get Attendance History (Dates User Attended)

Returns a list of dates when the user actually attended the gym.

- **Method**: `POST`
- **URL**: `/api/v2/schedule/weekly`
- **Request Body**:
  ```json
  {
      "from": "2026-05-01T00:00:00.000Z",
      "to": "2026-05-31T23:59:59.999Z",
      "locations_box_id": "<locations_box_id>"
  }
  ```
- **Response**: Array of date strings: `["2026-04-06", "2026-04-09", "2026-05-07"]`
- **Notes**: These are attendance dates (checked-in classes only). Use to sync `workouts` table: any workout on a date in this list → mark `status='completed'`.

---

### 4. Register for a Class

- **Method**: `POST`
- **URL**: `/api/v2/scheduleUser/insert`
- **Request Body**:
  ```json
  {
      "schedule_id": "<schedule_id>",
      "membership_user_id": "<membership_user_id>",
      "extras": { "spot": null }
  }
  ```
- **Response**: Full class object with `user_booked` set to the new `schedule_user_id`.

---

### 5. Cancel a Class Registration

- **Method**: `POST`
- **URL**: `/api/v2/scheduleUser/delete`
- **Request Body**:
  ```json
  {
      "schedule_user_id": "<schedule_user_id>",
      "schedule_id": "<schedule_id>",
      "late_cancel": false
  }
  ```

### 6. Check Late Cancel Status

- **Method**: `POST`
- **URL**: `/api/v2/scheduleUser/checkLateCancel`
- **Request Body**: `{ "schedule_id": "<schedule_id>" }`

### 7. Get User Profile

- **Method**: `GET`
- **URL**: `/api/v2/user/profile`
- Returns user details including `refreshToken` and confirmed `boxes`, `activeLocationsBox` IDs.

### 8. Get Memberships

- **Method**: `GET`
- **URL**: `/api/v2/boxes/<box_id>/memberships/1/false`
- Returns membership records; confirms the `membership_user_id`.

---

## Tool → Endpoint Mapping

| Tool | Endpoint(s) |
|------|------------|
| `fetch_upcoming_arbox_classes` | `betweenDates` + `logbook/workouts` |
| `fetch_weekly_gym_schedule` | `betweenDates` + `logbook/workouts` for each date |
| `sync_arbox_attendance` | `schedule/weekly` (attendance dates) |
