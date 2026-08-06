# Travel — trip planning skill, and the app tile derived from it

**Status:** SHIPPED — merged to main in #77 (2026-08-06). Archived; kept for the reasoning behind
the decisions, not as a live plan. Multi-destination is deferred and tracked in issue #76.
**Date:** 2026-08-05.
**Goal:** let the owner plan and run a trip through conversation — save places, keep a per-trip
wishlist, build a dated itinerary — and render the result as a tile in the jarvis-app. Jarvis's
tools are the product; the tile is a view of whatever they return.
**Source of truth:** this document. Earlier external drafts of this feature are superseded and
should not be implemented from.

---

## Checklist

**Phase 1 — storage + skill scaffold** — **SHIPPED & VERIFIED on staging 2026-08-05**
- [x] `tools/travel/` package + `SKILL.md` (frontmatter `name`/`description` + rules body)
- [x] `DATA_DIR/travel/travel.sqlite` — four tables, created idempotently at import
- [x] `query_travel_db` — pulled forward from Phase 2; a skill with no tools is invisible to
      `skill_namespaces()`, so without it there is nothing to activate and nothing to verify
- [x] `GOOGLE_PLACES_API_KEY` documented in `.env.example` — commented at this point, since
      nothing read it until place search existed (uncommented in Phase 2)
- [x] `scripts/ci/check_paths.py` and `check_channel_agnostic.py` green; `check_env.sh` reports
      no new drift
- [x] Live check after restart: skill activates in chat and `query_travel_db` returns the schema;
      staging registry went 8 → 9 namespaces, 52 → 53 skill tools

**Phase 2 — the four tools** *(the actual deliverable; one module per table, one commit per tool)*
- [x] `manage_trip` — create / update / set_current / archive / delete / list — verified by
      `scripts/test_travel.py` (31 checks) and live on staging
- [x] Confirmation wired on `manage_trip(action="delete")` only
- [x] `manage_place` — search / save / list / update / delete — verified by
      `scripts/test_travel.py` (63 checks) and live against the real Places API
- [x] `manage_wishlist` — add / remove / list — verified by `scripts/test_travel.py`
      (86 checks) and live on staging, grouped headings included
- [x] `manage_itinerary` — schedule / reschedule / unschedule / remove / list — verified by
      `scripts/test_travel.py` (117 checks) and live on staging
- [x] `SKILL.md` rule: always record a booking's confirmation code — it is the only thing that
      stops a row being re-dated when the trip moves
- [x] `SKILL.md` rule added after live testing: never widen a trip's dates as a side effect of
      adding an item — schedule it outside the window and say so instead
- [x] `GOOGLE_PLACES_API_KEY` uncommented in `.env.example` — place search now calls it
- [x] `category` vocabulary settled (below); no schema migration ships — instances are
      migrated by hand, as staging was

**Phase 3 — app surface (agent side)** — **SHIPPED & VERIFIED**
- [x] `gateway/apps/travel.py` — `AppSpec(ns="travel")`, one `GET tile` entry
- [x] One import line in `gateway/apps/specs.py`
- [x] Every blocking call through `asyncio.to_thread` (the queue-split rule)
- [x] `scripts/ci/check_channel_agnostic.py` green; 138 checks in `scripts/test_travel.py`
- [x] Live: `declared 2 apps to the jarvis-app hub` on every restart, and the tile answers

**Next — one destination per trip** *(target spec: `TRAVEL_TARGET_STATE.md`. Ordered by
dependency; every stage leaves the tools and the tile working)*

- [x] **Stage 0 — the overnight bug** — fixed, plus a second one it uncovered: `reschedule` had no
      time-order check and left `end_date` behind when an item moved. 143 checks; verified live

- [x] **Stage 1 — collect what the rest needs.** `city` + `country` on `places` from
      `addressComponents` (Essentials tier, no SKU change). Locality, then postal_town, then
      administrative_area_level_2, so the most specific answer wins. 146 checks; verified live —
      a real save returned Lisboa/Portugal, a hand-added place stayed NULL without erroring

- [x] **Stage 2 — `destinations`, and a tool for them.** Table with `name UNIQUE COLLATE NOCASE`
      and `timezone NOT NULL`; `manage_destination` (list · create · update · merge).
      `places.destination_id` resolved from the caller, else the trip in context, else by asking —
      never from Google's locality. `trips.destination_id` replaces the free-text destination and
      `timezone` moves off `trips`. Schema is create-only; staging was recreated clean rather than
      migrated. 164 checks; verified live, and the first saved Tokyo place came back with
      `city=Shibuya` under `destination=Tokyo` — the ward case, on the first run

- [x] **Stage 3 — re-anchor the wishlist.** `wishlist.trip_id` became `destination_id`, plus
      `wishlist_id` addressing, `city`, `done_at`, `priority` 1–5, the second UNIQUE that stops
      duplicate placeless rows, and `manage_wishlist(update)` with empty-string-clears. Forced two
      further changes: `manage_trip(delete)` no longer touches the wishlist, and `unschedule` is
      gone — nothing is consumed, so there is nothing to put back. 170 checks; verified live by
      deleting a trip and finding its destination's list intact

- [x] **Stage 4 — the time model.** `arrival_date` as a parameter rather than a derivation;
      `departure_timezone` / `arrival_timezone` on transit; duration computed from both resolved
      instants; the inference limited to a same-zone overnight, and a crossing refused rather than
      guessed. 180 checks; verified live — the model volunteered both zones unprompted and got the
      date-line case right without the refusal firing
- [x] **Stage 4b — retire the date shift.** `_shift_itinerary` deleted: changing a trip's dates
      changes the dates and names what now falls outside, and a booked item and an unbooked one
      behave identically. `confirmation_code` drives nothing and is reference data. 179 checks

- [x] **Stage 5 — the gaps the reviews found.** `manage_itinerary(update)` covering everything
      reschedule does not, including `place_id` and moving an entry to another trip; `schedule`
      accepting `wishlist_id`; empty string clears; `origin`/`destination_loc` renamed to
      `from_location`/`to_location` so one word stops meaning two kinds of thing; the two missing
      itinerary CHECKs; and the same place on the same day reported with the existing entry rather
      than duplicated. 196 checks; verified live

- [x] **Stage 6 — reads.** Day-strip range takes its min over `start_date` alone, so the
      night-before departure it exists for is no longer dropped. An item is placed on every day it
      touches with a `single`/`start`/`continuation`/`end` role, in the tool listing and the tile
      alike — placement lives in one function both import, so they cannot disagree. Arrivals sort by
      when they land, converted to the clock of the day they land in. 206 checks; the handoff is
      rewritten against the shipped payload and is no longer provisional

- [x] Throughout: **no migration code shipped.** The schema is create-only and staging was
      recreated clean at each shape change, which is what made the NOT NULL constraints real

**Phase 4 — app client** *(handoff to `roiguri/jarvis-app`)*
- [x] Handoff spec written from the shipped tile payload — `TRAVEL_APP_HANDOFF.md`, no longer
      provisional
- [ ] `ui/apps/travel/` package + one line in `AppCatalog.kt` (`ic_app_travel.webp` already ships)

**Phase 5 — deep links** *(blocked; see Out of scope)*
- [ ] Hub contract gains a navigation block kind
- [ ] Chat pre-fill affordance exists in the app's chat surface

**Follow-ups** *(not v1, tracked here so they aren't lost)*
- [ ] Places API usage metrics in `observability/` — see Follow-ups below
- [ ] Multi-trip browsing (a `trips` app entry + a client switcher)

---

## Decisions

Settled in a design session against the code. Each is a decision, not a default.

**Entity model — three tables.** A place, wanting to go, and going at a time are three unrelated
facts, so they are three unrelated tables. The alternative — one row per thing, flipping
`wishlist → scheduled` in place — was rejected: under it, scheduling a place *removes it from the
wishlist*, the same place cannot appear on two days, and a multi-night stay needs a bolted-on end
date. None of those are fixable later without a migration. Both of the owner's earlier travel apps
(`roiguri/wanderlust`, `roiguri/wanderlust-travel`) independently chose the split, and the industry
precedent agrees — Wanderlog keeps lodging and flights in lists separate from the day-by-day
itinerary.

**Places float free.** A place is a fact about the world and carries no trip. The trip lives on the
wishlist row and the itinerary row, which are the two places it is actually true. Google's
`place_id` makes dedupe exact, so a place saved for one trip is already there for the next, while
notes stay attached to the trip they were written for.

**Trip resolution is the agent's, explicitly.** Tools take a literal `trip_id` and never guess. An
unknown one is a hard error whose message lists the real trips, so the model self-corrects in one
retry. No normalization function exists anywhere in the skill — fuzzy matching of "the Lisbon
trip" to a row is what the model is for, and it can see the whole list, which no `LIKE` query can.

**One current trip, explicitly pinned.** `trips.is_current`, single-row enforced by a partial
unique index. `create_trip` claims it **only when no trip holds it** — creating a second trip never
steals the pointer. Deriving the current trip from today's date was rejected: a trip must be
presentable when it is not happening, including a dateless draft.

**Dates are stored; `day_number` is derived.** The deciding case is inserting a day — under
day-numbering that renumbers every later row, which is the N-row rewrite day-numbering exists to
avoid. Under dates it is one edit. The case day-numbering wins (shifting a whole trip) involves
re-booking every anchored item anyway, so its advantage is largely illusory. Moving a trip is
therefore a *tool action*, not a schema property: a pure translation shifts unbooked lines by the
delta and reports lines carrying a `confirmation_code` as needing rebooking, because a booked
flight does not move when plans change.

**Scheduling requires a dated trip.** A dateless draft is the "someday" bucket — collect places,
set dates, then schedule. `manage_itinerary(action="schedule")` on an undated trip refuses and says
why, rather than accepting a day that means nothing.

**Items outside the trip window are allowed and flagged.** Real trips start with a red-eye the
night before and end after a late checkout. The payload marks them so the client can render an edge
day; nothing is ever silently hidden or silently stretched.

**Google Places API (New) Text Search, at the Pro tier.** Enabled on the GCP project that already
serves `tools/google_health`. "Places API (New)" is the only real option: the legacy Places API was
frozen in March 2025 and cannot be enabled on new projects.

Lookup is its own tool returning several candidates — a chain name resolves to dozens of branches,
and only a separate step lets the agent or the owner choose. A Places failure is relayed verbatim
(the Arbox precedent) and never blocks saving a place by hand.

**The field mask is the price**, and this is the load-bearing detail. Text Search (New) requires an
`X-Goog-FieldMask` header — omitting it is an error, not a default — and the fields named in it
select the SKU:

| Field mask contains | SKU | Free/month | Beyond |
|---|---|---|---|
| `places.id` only | Essentials (IDs Only) | unlimited | — |
| `displayName`, `formattedAddress`, `location`, `types`, `googleMapsUri` | **Pro** | **5,000** | $32 / 1,000 |
| adds `rating` | Enterprise | 1,000 | $35 / 1,000 |
| adds `reviews` / atmosphere | Enterprise + Atmosphere | 1,000 | $40 / 1,000 |

Every field this design needs is Pro, and `rating` is deliberately **not** requested. 5,000 free
lookups a month against single-user traffic — and only for a place not already in `places`, which
caches them permanently — means this bills nothing in practice, though billing must still be
enabled on the project. The old $200 monthly credit no longer exists; these per-SKU free caps
replaced it.

The risk is therefore not the bill but a **silent tier slip**: adding one field to the mask cuts the
free allowance fivefold and raises the rate, with no error and no warning. So the field mask is a
**hardcoded module constant, never assembled from caller input** — a caller must not be able to
widen it, and adding a field to it is a deliberate edit that changes the pricing tier.

Revisiting `rating` later is not free: it means a second billed lookup for every place already
saved, so wanting it after the fact costs more than asking for it now. Accepted.

**Eleven display categories, with Food and Drink split five ways.** `restaurant · cafe · dessert ·
bar · market · sights · outdoors · shopping · lodging · transit · other`. Google's own 19
documentation headings are the skeleton — they are not exposed by the API, so the mapping is
transcribed — but its Food and Drink bucket is too coarse to be useful: on a trip a cafe and a
restaurant are different errands. Everything finer survives on the row (`google_type`,
`google_type_label`, and the full `google_types` array), so re-cutting these buckets later is a
migration rather than a re-fetch, and "Italian Restaurant" is always recoverable under a heading
that reads `restaurant`.

A category is derived from Google's `primaryType` by an exact table plus suffix rules (`*_restaurant`
covers every cuisine, including ones Google has yet to invent). An unrecognised type leaves the
category **NULL, not `other`** — undecided and miscellaneous are different states, and only the
first is worth revisiting. The model may set or correct a category, but only to one of the eleven.

**Times are local wall-clock text; the trip carries a timezone.** `"20:00"` means 20:00 where the
owner is standing and is never converted. `trips.timezone` is used for exactly one thing: deciding
which date counts as "today" when reading, so the itinerary is right from the first hour of the
morning in a far-east destination. Blank falls back to Asia/Jerusalem, correct for a domestic trip.

**Four item types: `place | lodging | transit | note`.** Each is a distinct rendering — a slot, a
banner spanning days, a leg with two endpoints, a free-floating remark. `reservation` is
deliberately **not** among them: it is not a kind of thing, it is a `confirmation_code`, and as a
field it attaches to a hotel, a flight *and* a dinner, which as a type it could not.

**The wishlist groups by the place's `category`, with no type field of its own.** Wanting to go
somewhere does not change what kind of place it is. A second field would let one place be "food" on
one trip and "sight" on another with nothing to arbitrate, and the tile would group it under two
headings across trips. A per-trip framing ("rainy-day options") is a tag or a note — additive later,
no migration.

**Tools are one per table, with an inline shortcut.** The model's routing question is only ever
"which table am I touching?", the easiest kind to get right. `manage_place` owns corrections, so a
wrong address is fixed once and every line pointing at it follows. Wishlist and itinerary also
accept inline place details and get-or-create, so the common path stays one call and the agent
never invents an id.

**Tools only — no proactive behavior in v1.** A heartbeat task is authored by asking Jarvis
(`manage_heartbeat_task`), not by writing code, so proactivity costs a conversation later rather
than a build phase now. The obligation this creates: the read paths must be good enough for a
heartbeat turn, not just a chat turn.

**Destruction is graduated.** Archive is the default and destroys nothing. `manage_trip(action=
"delete")` is `destructive=True` with a confirmation button and cascades its wishlist and itinerary
rows; places survive, since they float free. Line-level edits are silent — that *is* the editing
loop, and a button on every edit makes the tool unusable. Deleting a place still referenced by a
line is refused, naming the referencing lines.

**Sequencing: Jarvis tools → tile payload → app client → deep links.** The presentation derives
from what the agent can do, so the tile is specified from a shipped payload rather than from
prose.

---

## Phase 1 — storage + skill scaffold

`tools/travel/` follows the `tools/fitness/` shape: `__init__.py` importing the tool module,
`SKILL.md` with frontmatter plus a rules body, `travel_tools.py` holding the tools. The registry
auto-discovers it; nothing else is wired.

`DB_PATH = os.path.join(config.DATA_DIR, "travel", "travel.sqlite")` — derived, never hardcoded.

```sql
CREATE TABLE trips (
  trip_id     TEXT PRIMARY KEY,           -- agent-authored slug, e.g. "lisbon_spring"
  destination TEXT NOT NULL,
  timezone    TEXT,                       -- IANA name; NULL -> Asia/Jerusalem
  start_date  DATE,                       -- NULL on a draft
  end_date    DATE,
  status      TEXT NOT NULL DEFAULT 'draft'
                CHECK(status IN ('draft', 'archived')),
  is_current  INTEGER NOT NULL DEFAULT 0,
  notes       TEXT,
  created_at  DATETIME DEFAULT (datetime('now'))
);
-- Exactly one current trip, enforced by the database rather than by discipline.
CREATE UNIQUE INDEX one_current_trip ON trips(is_current) WHERE is_current = 1;

CREATE TABLE places (
  place_id        INTEGER PRIMARY KEY AUTOINCREMENT,   -- ours
  google_place_id TEXT UNIQUE,                         -- Google's; NULL when hand-added
  title           TEXT NOT NULL,
  address         TEXT,
  maps_url        TEXT,
  lat             REAL,
  lng             REAL,
  category        TEXT,                                -- closed set, vocabulary open
  google_type     TEXT,                                -- verbatim primary type, kept for later
  created_at      DATETIME DEFAULT (datetime('now'))
);

CREATE TABLE wishlist (
  wishlist_id INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id     TEXT    NOT NULL REFERENCES trips(trip_id),
  place_id    INTEGER NOT NULL REFERENCES places(place_id),
  notes       TEXT,
  priority    INTEGER,
  added_at    DATETIME DEFAULT (datetime('now')),
  UNIQUE(trip_id, place_id)
);

CREATE TABLE itinerary (
  entry_id          INTEGER PRIMARY KEY AUTOINCREMENT,
  trip_id           TEXT    NOT NULL REFERENCES trips(trip_id),
  place_id          INTEGER REFERENCES places(place_id),  -- NULL for transit / note
  item_type         TEXT    NOT NULL
                      CHECK(item_type IN ('place','lodging','transit','note')),
  title             TEXT,                                 -- required when place_id IS NULL
  start_date        DATE    NOT NULL,
  end_date          DATE,                                 -- NULL = single day
  start_time        TEXT,                                 -- "HH:MM", local wall clock
  end_time          TEXT,
  origin            TEXT,                                 -- transit only
  destination_loc   TEXT,                                 -- transit only
  confirmation_code TEXT,                                 -- any type may carry one
  notes             TEXT,
  created_at        DATETIME DEFAULT (datetime('now')),
  -- A row with neither a place nor a title is unrenderable.
  CHECK (place_id IS NOT NULL OR title IS NOT NULL)
);
```

Derived at read time, never stored: `day_number = julianday(start_date) - julianday(trip.start_date) + 1`,
`NULL` when the trip has no dates. Lodging covering a given day is
`? BETWEEN start_date AND COALESCE(end_date, start_date)`.

**Environment.** `GOOGLE_PLACES_API_KEY` was documented commented while nothing read it — the skill
loading on an instance does not mean the key is needed there — and was uncommented in the change
that shipped place search. `check_env.sh` therefore reports it missing on any instance not yet
given one, which is the intended reminder rather than a fault. **Places API (New)** — not the
frozen legacy Places API — must be enabled with billing on the GCP project that already serves
`tools/google_health`, the key restricted to that single API, and a daily quota set: the field mask
selects the billing SKU, so a quota is what turns a mistake into a failed request rather than a
surprise on the bill.

### Secrets migration — required before this ships to prod

This is the first new secret since the app channel, and the deploy path will **not** stop you from
forgetting it.

Each instance reads its own `ROOT/secrets/.env`, so staging's key does not reach prod. Adding it to
prod is a manual step the owner performs on the host — Claude never reads or writes either `.env`.

The trap: `deploy/deploy.sh` runs `check_env.sh` at step 9 as an **advisory warning, not a gate**
(`say "NOTE: check_env reports .env key drift"` — the deploy proceeds regardless). The import smoke
check at step 6 won't catch it either, since the key is read at call time, not at import. So a prod
deploy with the key missing succeeds, starts cleanly, and fails only when the owner first asks
Jarvis to look up a place — as a tool error mid-conversation, which is the worst place to discover it.

Order of operations for the prod deploy:

1. Owner adds `GOOGLE_PLACES_API_KEY` to `/app/secrets/.env` **before** running `deploy.sh`.
2. Owner runs `scripts/check_env.sh` and confirms it reports no drift.
3. Deploy, restart, then verify with one real lookup through Telegram rather than trusting the
   clean start.

The same applies to any later instance. If prod is deliberately **not** getting the travel skill
yet, the key still has to be present or `check_env.sh` will report drift on every subsequent
deploy — in which case follow the app channel's precedent and keep the key commented in
`.env.example` with a note saying why, rather than leaving a known-failing check in place.

---

## Phase 2 — the four tools

Action-dispatch tools, matching the house style (`manage_fitness_plan`, `manage_heartbeat_task`).
Registered `@tool_register(namespace="travel", destructive=...)` above `@tool`.

| Tool | Actions | Notes |
|---|---|---|
| `manage_place` | `search`, `save`, `update`, `delete` | `search` POSTs to `places.googleapis.com/v1/places:searchText` with the constant Pro field mask and returns ~5 candidates with `google_place_id`, address and a disambiguator. `delete` refuses while referenced. |
| `manage_trip` | `create`, `update`, `set_current`, `archive`, `delete`, `list` | `create` claims `is_current` only if unheld. `update` with new dates performs the shift described above. `delete` is `destructive=True` + confirmation. |
| `manage_wishlist` | `add`, `remove`, `list` | `add` takes either a `google_place_id` or inline place details (get-or-create). `list` groups by `category`. |
| `manage_itinerary` | `schedule`, `reschedule`, `unschedule`, `remove`, `list` | `schedule` is also the promote path — a wishlist row stays put. Refuses on an undated trip. |
| `query_travel_db` | read-only `SELECT` | Separate connection in SQLite `mode=ro`, row cap, empty `sql` returns the live schema. Mirrors `query_fitness_db`. |

Every unknown-id error names the valid alternatives. `SKILL.md` carries the rules the tool
docstrings cannot: when to search Places versus save by hand, that a booked line is never moved
silently, and that the wishlist is per-trip while a place is not.

---

## Phase 3 — app surface (agent side)

`gateway/apps/travel.py` registering `AppSpec(ns="travel", name="Travel", entries=(...))`, plus one
import line in `gateway/apps/specs.py`. One entry in v1:

- `AppEntry(id="tile", method="GET", params=("trip_id",), handler=...)` — `trip_id` optional;
  omitted means the current trip.

The handler reads SQLite directly, exactly as `gateway/apps/memory.py` walks `MEMORY_DIR` rather
than calling the memory tools: tool output is English written for a model, and parsing it here
would ship a client that breaks when a docstring is reworded. Every blocking call goes through
`asyncio.to_thread`. Failures the caller could have caused raise `AppNotFound` /
`AppInvalidRequest`; anything else propagates as an internal fault.

Payload sketch — settled during implementation, and the handoff is written from what actually
ships:

```
trip      trip_id, destination, start_date, end_date, timezone, status, is_current,
          notes, today (in the trip's timezone), position (before|during|after|undated)
days      [{ date, day_number, outside_window, is_today, items: [entry, ...] }]
lodging   [ entry, ... ]      -- item_type='lodging', a banner rather than a slot
wishlist  [{ category, items: [place, ...] }]
```

**`days` carries its own items**, rather than the separate `itinerary` array this was first
sketched with. Two arrays keyed by date would make the client join them to draw one screen, and
would admit a state where they disagree. Every day of the trip is present including empty ones — a
strip that skipped an empty Wednesday would be wrong — plus any day outside the window that holds
something, flagged, with a `day_number` below 1 rather than a fabricated one. Untimed items sort
after timed ones within a day: an item with no time is not a midnight item.

`trip: null` with empty arrays is the answer when no trip is pinned. That is a legitimate state, and
a `not_found` there would make an empty screen indistinguishable from a broken one; an explicitly
requested trip that does not exist **is** `AppNotFound`. The connection is opened read-only at the
URI level, so a device cannot write to this database even if a handler were wrong about what it was
doing.

**Why one entry and not several.** Entries serve navigation steps the client makes, not data types.
The memory app splits `list` from `read` because it is an unbounded tree that must be browsed, with
per-file payloads large enough to need a cap and a path-resolution security surface of their own.
Travel is a single screen whose interactions are gestures — swiping days, flicking between tabs —
and every one of those requests would travel through a long-poll hub, which is acceptable for
opening a screen and not for a swipe. The whole trip is bounded by construction (one trip, a couple
of weeks, tens of rows, no file contents), so it is fetched once and every interaction after that is
local.

This splits later if the payload stops being bounded — a wishlist that outgrows one trip, rich place
data (photos, hours), or any write, which is a new entry regardless. Adding an entry is purely
additive, so starting with one forecloses nothing.

A `trips` list entry is deliberately omitted for the same reason: v1 presents one trip. It arrives
with the multi-trip browser.

---

## Phase 4 — app client (handoff)

Lands in `roiguri/jarvis-app`, not this repo. A new `ui/apps/travel/` package (screen, view model,
DTOs) plus one entry in `AppCatalog.kt` — `ic_app_travel.webp` already ships unused. Two tabs:
itinerary (day strip + vertical day view, lodging as a banner above it) and wishlist (grouped by
category). Maps links open via `Intent`.

**Written after Phase 3 is live**, from the payload that actually ships — not from the sketch
above, which is a design aid and will drift.

---

## Multi-city trips, and the times that go with them

**Raised 2026-08-05, after the tools shipped.** Wanted now, not deferred. The schema is an open
decision: nothing below is settled, and the candidates differ enough that building the wrong one is
a migration rather than an edit.

### The problem — three symptoms, one cause

The wishlist is anchored to a **visit** when the thing it describes is a **place in the world**:

1. A second trip to the same city starts with an **empty wishlist**, even though the places
   themselves survive. Place duplication was solved; list persistence was not.
2. A trip covering several cities has **one flat wishlist** with no notion of which city a place is
   in — the list stops being scannable at exactly the size where it matters.
3. **"Which city am I in on day 5?"** is unanswerable, so lodging cannot be reasoned about per city
   and the tile cannot section a long trip by leg.

### What is agreed

- The symptoms are real and worth fixing now.
- `places.city` is worth adding under any of the candidates, so it is not blocked on this decision.
- A destination is addressed by **explicit id**, exactly as a trip is. That decision does not get
  reopened — it just gains a second thing to name.
- **A wishlist entry belongs to the place, not to the visit.** So it anchors at destination level
  and survives into the next trip there. This rules out candidate B.
- **`timezone` moves from the trip to the city.** A trip crossing countries cannot have one, and
  putting it on the city is what makes a multi-country trip work at all.

### Which follows from those

Those two answers decide the third. If timezone lives on the city, then resolving "what is today?"
for a given day *requires* knowing which city that day belongs to — and that is exactly what
inference cannot answer on a day with nothing scheduled, or on a travel day that is genuinely two
cities. A tile would show the wrong day on the one morning nothing is booked. So the day→city
mapping has to be **declared**, and candidate C falls with B.

**Candidate A is therefore the shape**, arrived at from the three answers rather than chosen up
front. What remains is not *whether* but *how carefully*: legs are a second thing to keep correct,
and the known cost is that moving or extending a trip has to move or extend them too — the same
class of problem `manage_trip(update)` already solves for scheduled items, and it should reuse that
reasoning rather than invent a second one.

### Candidates considered

**A. Destinations + legs — SELECTED (see above).** A `destinations` table (name, country, timezone, coordinates) and
`trip_legs` dating each destination within a trip. The wishlist anchors to `destination_id`; the
itinerary stays on `trip_id` and derives its leg from the date. Solves all three symptoms. Largest
change, and it moves `timezone` from trip to destination — which is arguably where it always
belonged, since a trip crossing timezones cannot have one.

**B. City on the row, no new tables — ruled out.** `places.city`, and the wishlist groups by it. Smallest
change and immediately useful. Does **not** solve symptom 1: a trip still owns its own list, so the
next visit still starts empty.

**C. Destinations without legs — ruled out.** Anchor the wishlist to a destination, but leave the itinerary
trip-only and infer which city a day is in from what is scheduled that day. Avoids inventing a leg
the owner never declares; the cost is that a day with nothing scheduled belongs to no city.

### Still to settle, inside candidate A

- **Trip-specific notes.** A wishlist entry belongs to the place, but "go early, we have the kids
  this time" belongs to one visit. Either notes live on the entry and accumulate across trips
  (simplest, slightly lossy), or an entry carries an optional per-trip note. Not decided.
- **What keeps legs correct when a trip moves.** Extending or shifting a trip has to extend or
  shift its legs. `manage_trip(update)` already reasons about this for scheduled items; legs should
  reuse that reasoning rather than grow a second one.
- **Whether a day may belong to no leg**, and what the tile shows if so — a gap between two legs is
  possible to express and probably a mistake worth flagging rather than refusing.
- **Migrating what exists.** `trips.destination` is free text today and `trips.timezone` is shipped;
  both need a one-time manual migration into `destinations`, as with every earlier schema change.


### Times that cross midnight or a timezone

**Raised 2026-08-05**, while reviewing how the timezone decision holds up for a multi-country trip.
Probing the shipped tools turned up one outright bug and two representation gaps.

### What is broken today

**An overnight transit is refused.** Scheduling a flight at `22:00 → 06:00` fails:

```
Error: end_time 06:00 is before start_time 22:00 on the same day.
```

The validation is too strict for the one item type where an overnight is the *normal* case, and the
message does not hint that `end_date` is the way through — so the likely outcome is a flight
recorded with no arrival time at all. This is broken independently of multi-city: a domestic night
bus hits it too.

**With `end_date` set it renders as `22:00-06:00`**, as though both were the same clock. A
Lisbon→Tokyo flight departs 22:00 WEST and lands 06:00 JST — about fourteen hours, displayed as
though it travelled backwards. Nothing in the payload says the arrival is in another zone, or even
on another day.

**`end_date` carries two meanings.** For lodging it is a *span* — the tile lifts those out as a
banner. For transit it would be an *arrival date*, a single moment. One column, two meanings,
distinguished only by `item_type`.

### The convention

`start_time` is departure wall-clock at the origin. **`end_time` is arrival wall-clock at the
destination.** When the arrival falls on the next day, `end_date` carries it, and the tool infers
+1 rather than refusing. The client renders `22:00 → 06:00 ⁺¹`.

This is how every boarding pass prints it, which is the argument: the ambiguity is resolved by a
convention travellers already read fluently, rather than by machinery. Nothing is converted, no
offsets are stored, and the wall-clock decision holds unchanged.

Rejected: a timezone column per time — every non-transit row would carry two NULLs to serve one
item type. Rejected: UTC instants for transit only — inconsistent with everything else, and it
re-introduces exactly the conversion bugs wall-clock storage exists to avoid.

### How "today" resolves once timezone lives on the city

Today is one fact about where the **owner** is, not a property of a day. So: find the leg whose
date range contains now, and compute today in that leg's timezone. Before the trip, fall back to
the first leg; after it, the last; with no legs at all, Israel.

The honest gap: on a travel day this is ambiguous for a few hours around midnight, because two legs
disagree about the date. Any answer there is defensible and it is not worth being clever about — but
the payload should carry **each day's own timezone** alongside the resolved `today`, so the client
has the facts rather than only the verdict. The phone, after all, genuinely knows where it is.

### Flights, and what the destination clock shows

The convention above is not ours to invent: **flight times are always local to their own airport** —
departure in origin time, arrival in destination time. Every schedule, boarding pass and airline
site works this way, and TripIt follows it. Adopting anything else would mean the owner reading our
itinerary against a boarding pass and finding they disagree.

**The destination clock follows the selected day.** While planning, the header shows the current
time where that day happens — tapping Day 5 (Porto) makes it read Porto. It reuses the date strip
the owner is already tapping, needs no extra control, and stays correct on a multi-city trip. Only
`timezone` is needed for it, which the payload already carries.

> Show a **clock**, never an offset. "14:32 in Lisbon" is always true; "+2h" is true today and wrong
> for the trip dates, because the offset drifts with DST — a May trip planned in December differs
> from what a delta computed now would claim. Stored times are unaffected either way, since they are
> wall-clock; this is purely about what not to render.

A quieter benefit: seeing "14:32 in Lisbon" beside an `09:00` breakfast makes the wall-clock
convention *visible*. It shows what the timezone decision otherwise has to explain.

**A flight is the one item that must not inherit the day's clock**, because it has two. Its card
carries both ends itself:

```
Day 3 · Mon 12 May                    14:32 in Lisbon
─────────────────────────────────────────────────────
  22:00   LIS  Lisbon
     │    14h 05m
  06:05⁺¹ NRT  Tokyo
```

Duration is worth showing: it is the one figure that is unambiguous across timezones and that two
wall-clock times alone do not reveal. Everything needed is already stored — `start_time`,
`end_time`, `origin`, `destination_loc`, `end_date`.

**Which resolves the travel-day question: a leg starts on the date you arrive.** Lisbon owns days
1–3 including the evening of the flight out; Tokyo owns day 4 onward. The flight sits on its
departure date, matching both the schema and TripIt. The header clock on day 3 therefore says
Lisbon, which is where nearly all of that day is spent — shrinking the ambiguity flagged above from
"a whole day is two cities" to "the hours actually in the air", which no app solves and none needs
to.

This also condemns the current text rendering, which should change with this work:
`[1] 22:00-06:00 LIS->NRT` reads as an eight-hour flight travelling backwards.

### The target schema

Where the five stages end up. Two new tables, three columns onto `places`, one column swapped on
`wishlist`, two columns off `trips`. `itinerary` is untouched.

```sql
destinations                          -- NEW
  destination_id   INTEGER PK
  name             TEXT NOT NULL      -- "Lisbon", "Greece"
  kind             TEXT               -- city | region | country
  country          TEXT
  timezone         TEXT               -- expected on a city; a country has none
  lat, lng         REAL
  google_locality  TEXT UNIQUE        -- the canonical key get-or-create resolves on
  created_at

trip_legs                             -- NEW (exactly one per trip from Stage 2; many from Stage 4)
  leg_id           INTEGER PK
  trip_id          TEXT    FK -> trips
  destination_id   INTEGER FK -> destinations
  start_date       DATE               -- a leg starts on the date you arrive
  end_date         DATE

trips                                 -- destination + timezone REMOVED
  trip_id          TEXT PK
  title            TEXT               -- optional; derived from the legs when absent
  start_date, end_date   DATE
  status           draft | archived
  is_current       INTEGER            -- partial unique index, unchanged
  notes            TEXT
  created_at

places                                -- + city, country, destination_id
  place_id, google_place_id, title, address, maps_url, lat, lng,
  category, google_type, google_type_label, google_types,
  city, country,                      -- Stage 1: raw from Google
  destination_id   INTEGER FK NULL,   -- Stage 2: the resolved row
  created_at

wishlist                              -- trip_id -> destination_id
  wishlist_id      INTEGER PK
  destination_id   INTEGER FK -> destinations
  place_id         INTEGER FK -> places
  notes, priority, added_at
  UNIQUE(destination_id, place_id)

itinerary                             -- UNCHANGED
  entry_id, trip_id, place_id, item_type, title,
  start_date, end_date, start_time, end_time,
  origin, destination_loc, confirmation_code, notes, created_at
```

**`trip_legs` arrives in Stage 2, not Stage 4.** The alternative was a `trips.destination_id` column
added in Stage 2 and deleted in Stage 4 once legs subsumed it — a migration that undoes a migration.
Introducing the table early with a one-leg-per-trip invariant costs a little more in Stage 2 and
makes Stage 4 mostly a matter of relaxing that invariant.

**`itinerary` gets no `leg_id`.** A day's city is derived by matching `start_date` against the legs,
for the same reason `day_number` is derived: a stored reference can disagree with the dates, a
derived one cannot. The cost is that a day belonging to no leg resolves to nothing — worth flagging
to the owner rather than preventing, since a gap between legs is expressible and probably a mistake.

**`places.city` and `places.destination_id` coexist**, exactly as `google_type` sits under
`category`: the raw value Google returned, and the row it resolved to. Keeping the raw one is what
makes re-resolution possible later without paying for the lookup again.

**`destinations.timezone` is nullable**, because "Greece" has no single one. So "what time is it
there" has no answer for a country-level destination. That is correct rather than a gap, but the
tile has to render it rather than assume a timezone exists.

### The upgrade, staged

Ordered by dependency, each step commit-sized and testable — the rhythm Phase 2 used. No migration
code ships with any of them; each one's existing rows are migrated by hand, as staging has been
throughout.

**Stage 0 — the overnight bug.** Independent of everything below and shippable any time. Infer +1
day for a transit whose arrival precedes its departure, instead of refusing it. Adopt the arrival
convention in the docstring and `SKILL.md`, and carry a date-rollover marker so the tile can render
`22:00 → 06:00 ⁺¹`.

**Stage 1 — `places.city` / `places.country`.** Add `addressComponents` to the field mask
(Essentials, so the SKU does not move) and store Google's own locality and country. Changes no
behaviour: it only starts collecting the key Stage 2 resolves on.

**Stage 2 — `destinations`.** `destination_id, name, country, timezone, lat, lng`, a nullable
`places.destination_id`, and a `manage_destination` tool. `trips.destination` — free text today —
becomes `trips.destination_id`, still one per trip, and `timezone` moves from trip to destination.
Behaviour is unchanged; the destination is simply a row rather than a string.

> Get-or-create on a destination is keyed on **Google's own locality string**, not on free text the
> model typed. That is not the string matching the trip-resolution decision rejected — it is a
> canonical value Google returns identically every time, the same property that makes
> `google_place_id` trustworthy for place dedupe. A place Google could not localise leaves
> `destination_id` NULL and the agent may set it explicitly.

**Stage 3 — re-anchor the wishlist.** `wishlist.trip_id` becomes `destination_id`, so a list belongs
to the place and survives the next visit. This is the headline fix and it lands here, not last:
resolving `trip → destination` needs only Stage 2, never legs. `manage_wishlist` still takes a
`trip_id` for convenience and resolves it through the trip's destination.

**Stage 4 — `trip_legs`.** `leg_id, trip_id, destination_id, start_date, end_date` generalises one
destination into many, and a trip becomes a sequence of dated stays. **A leg starts on the date you
arrive**, so the evening you fly out still belongs to the city you are leaving. `manage_trip`'s date
shift has to move legs with the trip — reusing the reasoning already there for scheduled items
rather than growing a second one. `manage_wishlist`'s resolution widens from "the trip's
destination" to "the trip's legs' destinations", with a refusal that names them when several fit.

**Stage 5 — the tile.** Days carry their leg and their own `timezone`, so the strip can section a
long trip by city and `today` resolves per day. Wishlist groups by city, then category. Lodging is
per leg. Ends with an addendum to `TRAVEL_APP_HANDOFF.md` covering the destination clock and the
flight card, written from the payload that ships.

**Two constraints across all of it.** Stages 1–2 are additive and leave every current behaviour
working. Stage 3 is the only one that changes an existing table's meaning, and by then destinations
exist to migrate onto. And **every stage leaves the tile answering**: it joins `wishlist` on
`trip_id` and reads `trip.timezone`, so stages 2–4 each repair it as they go — Stage 5 is about new
capability, not about fixing what earlier stages broke.

---

## Out of scope, with reasons

- **Chat→tile deep-linking.** Needs a new block kind in the hub contract; the current set
  (`buttons | card | confirmation | form`) is closed and every kind is interactive. A `jarvis-app`
  change, not a Jarvis one.
- **"Modify in chat" pre-fill.** No affordance exists in the app's chat surface to open it with
  text already entered.
- **Multi-trip browsing.** v1 presents one trip; the `is_current` pin becomes its default selection.
- **Reminders integration.** Would put trip-derived state in a second store that can drift when a
  line is rescheduled.
- **Budget, expenses, photos, location tracking.** Present in the owner's earlier designs; nothing
  here needs them.

---

## Open questions

- **Undecided items on a day.** Scheduling something with no time already works — `start_time` is
  nullable and untimed items sort after timed ones. What is missing is *tentative*: "day 3, either
  the castle or the museum" renders as two commitments and a day that looks fuller than it is. The
  small fix is a nullable `status` on `itinerary` (`planned` | `option`) so the tile can group
  options separately. Leaving them on the wishlist instead fails the requirement, since the point
  is that they are attached to a particular day.
- **Whether `manage_trip(action="update")` shifts lines automatically or proposes and confirms.**
  Leaning automatic for unbooked lines, since the booked ones are reported rather than moved.

---

## Follow-ups

### Places API usage metrics

Places is the first **metered, per-request** external dependency in the system. Nothing currently
counts it, and the existing cost surface cannot absorb it as-is.

**What this is actually for.** At the Pro tier's 5,000 free monthly lookups, single-user traffic will
not approach a bill, so metering is not cost control — it is the detector for a **tier slip**. A
field added to the mask silently moves the SKU, cutting the free allowance fivefold with no error
anywhere; a request count plus the mask in use is what makes that visible before a bill does. Size
the work to that purpose: a counter and the active mask are worth more here than a precise
dollar figure.

Where it doesn't fit today:

- `observability/usage.py` rolls up `turns.jsonl` and prices **tokens** through `MODEL_PRICES`.
  `usd_cost` therefore means "LLM cost" everywhere it appears, including `/usage`. Places bills per
  request by SKU, so it has no place in that table and must not be silently folded into the same
  number — a blended figure would make both halves unreadable.
- `tool_calls.jsonl` cannot be used to infer the count. `record_tool_call` stores tool name,
  namespace and `args_size`, but **not** the arguments — so `manage_place` calls cannot be filtered
  down to `action="search"`, and save/update/delete never touch the API. One tool call is also not
  one request: a retry or a follow-up fetch is billed again.

So the count has to be recorded **at the HTTP client**, where a request is a request. Open design
points for whoever picks this up:

- Whether this becomes a travel-local counter or a general "external API call" record in
  `observability/` — the latter is more work now and pays off the moment a second metered API
  arrives, which on current trajectory it will.
- Whether `/usage` grows a separate non-LLM line, or external spend gets its own command. Keeping
  `usd_cost` LLM-only is the safer default; the alternative silently changes the meaning of a number
  the owner already reads.
- A per-SKU price table would repeat `MODEL_PRICES`'s known failure mode — transcribed constants
  that corrupt a rollup silently when stale. If it's added, it needs the same treatment: a comment
  naming the source page and the verification date.

Not blocking v1. Volume is likely to be a handful of requests per trip, which is exactly the
condition under which nobody notices a mistake until the bill.

---

## Verification

Per repo norms, Claude cannot restart the service — each phase ends with the owner restarting and
checking. Phase 2 is exercised through Telegram: create a trip, save a place by search, wishlist it,
schedule it, confirm it is still on the wishlist, schedule it a second time on another day, add a
multi-night stay, then delete the trip and confirm the button appears and the places survive. Phase
3 is exercised from the app once the hub has re-declared the manifest — and note that a correct app
surface answers nothing until the process is bounced, with no agent-side log to show for it.
