# Handoff — Travel app screen (`roiguri/jarvis-app`)

**For:** the Android client. **Agent side is shipped**: `travel` is declared in the manifest and
`GET tile` answers today.
**Written from the payload that actually ships**, not from a design sketch — sample below is a real
response, trimmed to two populated days plus one empty one.
**Source of truth for the data:** `gateway/apps/travel.py` in `roiguri/jarvis-agent`. If this
document and the payload disagree, the payload is right.

> **PROVISIONAL — not yet handed over.** The multi-city work (destinations, legs, per-day timezone)
> changes this payload, and the flight/clock conventions are not built yet. Hand this over only
> once that lands and this document has been rewritten against the payload that ships then.

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

```json
{
  "trip": {
    "trip_id": "lisbon_2027",
    "destination": "Lisbon, Portugal",
    "start_date": "2027-05-22",
    "end_date": "2027-05-29",
    "timezone": "Europe/Lisbon",
    "status": "draft",
    "is_current": true,
    "notes": "Focus on food.",
    "today": "2026-08-05",
    "position": "before"
  },
  "days": [
    {
      "date": "2027-05-24",
      "day_number": 3,
      "outside_window": false,
      "is_today": false,
      "items": [
        {
          "entry_id": 4,
          "item_type": "transit",
          "title": "Airport metro to city",
          "start_date": "2027-05-24",
          "end_date": null,
          "start_time": "09:00",
          "end_time": null,
          "origin": "Airport",
          "destination_loc": "Baixa",
          "confirmation_code": null,
          "notes": null,
          "place": null
        }
      ]
    },
    { "date": "2027-05-26", "day_number": 5, "outside_window": false,
      "is_today": false, "items": [] }
  ],
  "lodging": [
    {
      "entry_id": 5, "item_type": "lodging", "title": "Hotel do Chiado",
      "start_date": "2027-05-22", "end_date": "2027-05-25",
      "start_time": null, "end_time": null,
      "confirmation_code": "BK-99812", "notes": null,
      "place": { "place_id": 9, "title": "Hotel do Chiado", "address": "...",
                 "maps_url": "https://maps.google.com/?cid=...", "lat": 38.71, "lng": -9.14,
                 "category": "lodging", "type_label": "Hotel" }
    }
  ],
  "wishlist": [
    {
      "category": "restaurant",
      "items": [
        { "place_id": 6, "title": "Cervejaria Ramiro", "address": "...",
          "maps_url": "...", "lat": 38.7, "lng": -9.1,
          "type_label": "Seafood Restaurant", "notes": null, "priority": null }
      ]
    }
  ]
}
```

### Field notes that matter for rendering

- **`trip` may be `null`.** That means no trip is pinned — a legitimate empty state, not an error.
  Draw an empty screen inviting the owner to start a trip in chat.
- **`position`** is `before | during | after | undated`. Use it to choose the initially selected
  day: `is_today` when `during`, otherwise the first day.
- **`today` is computed in the trip's timezone**, not the device's and not the server's. Don't
  recompute it locally — for the first hours of a morning abroad they disagree, which is exactly
  when the screen is being used.
- **`days` is the whole strip.** Every day of the trip is present *including empty ones*; do not
  filter them out or the strip will skip a free Wednesday. Days outside the trip window (a night
  train the evening before, a late checkout after) appear at the edges with
  `outside_window: true` and a `day_number` below 1 — render them as edge chips, and never as
  "Day 0".
- **`lodging` is separate on purpose** and never appears inside `days`. A multi-night stay is not a
  slot in a day. Render it as the sticky banner above the timeline.
- **`place` is `null` for `transit` and `note` items** — they are not places and have no address or
  map link. Use `origin` / `destination_loc` for a transit leg instead.
- **`title` is always populated**, for every item, whether or not it has a place. Render it
  directly; no fallback logic needed.
- **`start_time` may be `null`.** Untimed items are already sorted after timed ones within their
  day. An untimed item is *not* a midnight item — render it without a time badge rather than as
  00:00.
- **`type_label`** is Google's human label ("Seafood Restaurant", "Pastry Shop"). `category` is the
  coarse bucket used for grouping. Show the label on the card and group by the category.
- **`category`** is one of: `restaurant, cafe, dessert, bar, market, sights, outdoors, shopping,
  lodging, transit, other` — plus `unsorted` in the wishlist for places nothing could classify.
  Groups arrive **pre-sorted** in that order, with `unsorted` last; keep the given order.
- **Rows saved before a schema change may have `type_label: null`.** Handle absence, don't assume.

---

## 3. Screen

Two tabs.

**Tab A — Itinerary**
- Trip header: destination, date range, `position` as a status chip.
- Lodging banner beneath it, from `lodging`. Show name, the check-out date (`end_date`), and a
  map action. Only show stays covering the selected day if you prefer; the array is small enough to
  render whole.
- Horizontal date strip from `days` — chip per day, `Day N · date`, edge days visibly distinct.
- Vertical list for the selected day from `days[].items`: time badge (`start_time`–`end_time`, or
  none), item-type badge, title, address or origin→destination, confirmation code when present.

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
