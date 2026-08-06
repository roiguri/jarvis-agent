# Handoff — Travel app screen (`roiguri/jarvis-app`)

**For:** the Android client. **Agent side is shipped**: `travel` is declared in the manifest and
`GET tile` answers today.
**Written from the payload that actually ships**, not from a design sketch — sample below is a real
response, trimmed to two populated days plus one empty one.
**Source of truth for the data:** `gateway/apps/travel.py` in `roiguri/jarvis-agent`. If this
document and the payload disagree, the payload is right.

---

## 1. Wiring

Adding an app is **a folder and one line** (`AppCatalog.kt`'s own words):

```kotlin
"travel" to AppCatalogEntry(
    icon = R.drawable.ic_app_travel,        // already in the tree, unused until now
    tagline = "...",
    footer = "...",
    screen = { args -> TravelApp(args) },
),
```

`ic_app_travel.webp` already ships. A tile is drawn only when the agent declares the namespace
**and** this map has an entry, so nothing appears until both sides exist — that pairing is
deliberate and needs no guard of its own.

**Fetching:** `GET /v1/apps/travel/q/tile`, optionally `?trip_id=<id>`. Omit `trip_id` for the
current trip, which is what the screen should do by default. `AppQueryClient` already speaks this;
`MemoryDto.kt` is the precedent for the DTOs.

**Errors:** `not_found` for a `trip_id` that doesn't exist; `invalid_request` for an undeclared
param. Both are caller mistakes. Anything else is an internal fault and should read as one.

---

## 2. The payload

A real response, trimmed to the populated days plus one empty one.

```json
{
  "trip": {
    "trip_id": "portugal_2027",
    "destination": "Lisbon & Porto",      // the trip's own title, when it has one
    "destination_name": "Portugal",       // the destination it belongs to
    "country": "Portugal",
    "start_date": "2027-05-02", "end_date": "2027-05-05",
    "timezone": "Europe/Lisbon",
    "status": "draft", "is_current": true, "notes": null,
    "today": "2026-08-06", "position": "before"
  },
  "days": [
    { "date": "2027-05-01", "day_number": 0, "outside_window": true, "is_today": false,
      "items": [
        { "entry_id": 1, "item_type": "transit", "title": "Night flight in",
          "start_date": "2027-05-01", "end_date": "2027-05-02",
          "start_time": "22:40", "end_time": "06:10",
          "from_location": "Tel Aviv", "to_location": "Lisbon",
          "confirmation_code": "XY-9988", "notes": null,
          "crosses_midnight": true, "place": null,
          "role": "start" }
      ] },
    { "date": "2027-05-02", "day_number": 1, "outside_window": false, "is_today": false,
      "items": [
        { "entry_id": 1, "role": "end",    "title": "Night flight in", "…": "…" },
        { "entry_id": 3, "role": "single", "title": "Cervejaria Ramiro",
          "start_time": "20:00", "end_time": null,
          "place": { "place_id": 1, "title": "Cervejaria Ramiro",
                     "address": "Av. Alm. Reis 1 H, Lisboa",
                     "maps_url": "https://maps.google.com/?cid=1",
                     "lat": null, "lng": null,
                     "category": "restaurant", "type_label": null } }
      ] },
    { "date": "2027-05-03", "day_number": 2, "outside_window": false,
      "is_today": false, "items": [] }
  ],
  "lodging": [
    { "entry_id": 2, "item_type": "lodging", "title": "Hotel do Chiado",
      "start_date": "2027-05-02", "end_date": "2027-05-05",
      "confirmation_code": "BK-9", "place": null }
  ],
  "wishlist": [
    { "category": "restaurant",
      "items": [ { "wishlist_id": 1, "place_id": 1, "title": "Cervejaria Ramiro",
                   "city": null, "address": "Av. Alm. Reis 1 H, Lisboa",
                   "maps_url": "…", "lat": null, "lng": null,
                   "type_label": null, "notes": "go early", "priority": 2 } ] }
  ]
}
```

### Nullability — every field, so a DTO can be written from this

Anything not listed as nullable is **always present and non-null**. Arrays are always present and
may be empty.

| Always present | Nullable |
|---|---|
| `trip.trip_id` · `destination` · `destination_name` · `timezone` · `status` · `is_current` · `today` · `position` | `trip.country` · `notes` · `start_date` · `end_date` |
| `days[].date` · `outside_window` · `is_today` · `items` | `days[].day_number` |
| item `entry_id` · `item_type` · `title` · `start_date` · `crosses_midnight` · `role` | item `end_date` · `start_time` · `end_time` · `from_location` · `to_location` · `confirmation_code` · `notes` · `place` |
| `place.place_id` · `title` | `place.address` · `maps_url` · `lat` · `lng` · `category` · `type_label` |
| wishlist group `category` · `items`; item `wishlist_id` · `title` · `priority` | wishlist item `place_id` · `city` · `address` · `maps_url` · `lat` · `lng` · `type_label` · `notes` |

Two that look nullable and are not:

- **`trip.destination` is never null.** It is the trip's own title when it has one and the
  destination's name otherwise, and a destination's name cannot be null. The comment in the sample
  says where the value comes from, not that it can be absent.
- **`role` is on `lodging` entries too**, as `"stay"`. Both arrays carry the same item shape, so one
  type reads both. A stay is never placed inside a day, which is what `"stay"` says.

### `position: "undated"` — a real state, and what it looks like

A trip exists before its dates do; that is the "someday bucket" the wishlist is for. The payload:

```json
{ "trip": { "trip_id": "someday", "destination": "Somewhere",
            "start_date": null, "end_date": null,
            "timezone": "Asia/Tokyo", "position": "undated", "…": "…" },
  "days": [], "lodging": [], "wishlist": [ … ] }
```

So: **no date range to draw, no strip, no day to open on.** The itinerary tab has nothing to show
and cannot have — scheduling into an undated trip is refused agent-side.

Suggested rendering, though this is the client's call:

- Header shows the destination with "no dates yet" in place of the range.
- **Open on the wishlist tab**, since it is the only one with content.
- The itinerary tab, if reachable, says the trip needs dates and that they can be set in chat.

The "pick the day to open on" rule in the notes below therefore applies only to `before`, `during`
and `after`.

### Field notes that matter for rendering

- **`trip` may be `null`** — no trip is pinned. A legitimate empty state, not an error.
- **`destination` is what to show**; `destination_name` is the destination it belongs to, which may
  be a country while the trip is titled after its cities.
- **`position`** is `before | during | after | undated`. Use it to pick the day to open on:
  `is_today` when `during`, otherwise the first.
- **`today` is computed in the trip's timezone**, not the device's. Don't recompute it — abroad,
  for the first hours of a morning, they disagree, which is exactly when the screen is read.
- **`role` is the important one.** An item appears on **every day it touches**:
  `single` (begins and ends that day) · `start` (leaves that day) · `continuation` (in progress all
  day) · `end` (arrives that day). Render the time accordingly — `22:40 →` on a start, `→ 06:10` on
  an end, no time badge on a continuation. Items are **already ordered** within a day; keep the
  order given.
- **`days` is the whole strip.** Every day of the trip is present including empty ones, plus any
  day outside the window an item touches, flagged `outside_window` with a `day_number` below 1.
  Render those as edge chips; never as "Day 0".
- **`lodging` never appears inside `days`.** A stay is not a slot. It is the banner.
- **`place` is `null` for `transit` and `note`** — use `from_location`/`to_location` instead.
- **`title` is always populated.** No fallback logic needed.
- **`start_time` may be `null`** — untimed, already sorted after timed items. Render without a time
  badge, not as 00:00.
- **`crosses_midnight`** is explicit because `end_date` means two different things: a span for a
  stay, a rollover for anything in a day.
- **`category`** is one of `restaurant · cafe · dessert · bar · market · sights · outdoors ·
  shopping · lodging · transit · other`, plus `unsorted` in the wishlist. Groups arrive pre-sorted;
  keep the order. `type_label` is Google's finer label and may be `null` on older rows.
- **Done wishlist items are omitted** from the payload entirely.

## 3. Screen

Two tabs.

**Tab A — Itinerary**
- Trip header: destination, date range, `position` as a status chip.
- Lodging banner beneath it, from `lodging`. Show name, the check-out date (`end_date`), and a
  map action. Only show stays covering the selected day if you prefer; the array is small enough to
  render whole.
- Horizontal date strip from `days` — chip per day, `Day N · date`, edge days visibly distinct.
- Vertical list for the selected day from `days[].items`, in the order given: time badge from the
  item's `role` (`22:40 →`, `→ 06:10`, `all day`, or `start–end`), item-type badge, title, address
  or from→to, confirmation code when present.

**Tab B — Wishlist**
- Sections from `wishlist`, in the order given, heading = category.
- Cards: title, `type_label`, address, note.

**Actions on a card**
- **Open in Maps** — `Intent(ACTION_VIEW, Uri.parse(maps_url))`. Fall back to a `geo:lat,lng`
  intent when `maps_url` is null but coordinates exist. Hide the action when neither is present.

---

## 4. Out of scope for this handoff

- **Writes.** The entry is `GET`. Everything is edited by talking to Jarvis in chat.
- **Chat deep-links / "modify in chat" pre-fill.** The hub's block kinds are a closed set
  (`buttons | card | confirmation | form`), so navigation from chat to a tile needs a hub contract
  change first. Not a client task yet.
- **Multi-trip browsing.** v1 shows one trip. `trip_id` is already accepted by the entry, so a
  switcher is additive when a `trips` entry lands.
- **Trips spanning several destinations.** One destination per trip for now; a country destination
  holds every city's places, and the wishlist groups by city.
