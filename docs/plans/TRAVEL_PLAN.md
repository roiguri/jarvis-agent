# Travel — trip planning skill, and the app tile derived from it

**Status:** designed, not started.
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

**Phase 3 — app surface (agent side)**
- [ ] `gateway/apps/travel.py` — `AppSpec(ns="travel")`, one `GET tile` entry
- [ ] One import line in `gateway/apps/specs.py`
- [ ] Every blocking call through `asyncio.to_thread` (the queue-split rule)
- [ ] `scripts/ci/check_channel_agnostic.py` stays green

**Phase 4 — app client** *(handoff to `roiguri/jarvis-app`)*
- [ ] Handoff spec written from the shipped tile payload, not from this document
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
trip      trip_id, destination, start_date, end_date, timezone, status,
          today (in the trip's timezone), position (before|during|after)
days      [{ date, day_number, outside_window }]      -- union of scheduled dates
lodging   [ entries with item_type='lodging' ]        -- rendered as a banner, not a slot
itinerary [ entries, grouped by date, sorted by start_time (NULLs last) ]
wishlist  [ rows for this trip, grouped by place.category ]
```

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
