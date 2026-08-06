# Travel — target state

**Status:** specification. The current build is Phases 1–3 plus Stage 0; this describes where the
next work lands. Rationale lives in `TRAVEL_PLAN.md`, which also carries the staged route from one
to the other.

**Scope:** one destination per trip. A destination may be a city or a country, and carries the
trip's timezone. A country is **preferred where it applies** — it holds every city's places under
one name, and country names barely vary in spelling where city names do. A city or region is right
when the country spans several timezones, since the destination is what carries one. Cities within it are labels used to group the wishlist. Trips spanning several
destinations are out of scope; §5 states what extending costs.

---

## 1. Data

| Table | One row is |
|---|---|
| `destinations` | somewhere you travel to — "Lisbon" or "Portugal", your choice of granularity |
| `places` | a specific venue: restaurant, hotel, station, viewpoint |
| `trips` | a dated visit to one destination |
| `wishlist` | something you want to do at a destination |
| `itinerary` | something happening at a time, on a trip |

```sql
destinations
  destination_id    INTEGER PK
  name              TEXT NOT NULL UNIQUE COLLATE NOCASE   -- one row per name, ever
  kind              TEXT CHECK(kind IN ('city','region','country'))
  country           TEXT
  timezone          TEXT NOT NULL          -- IANA. Required: the whole time model defaults to it
  lat, lng          REAL
  google_locality   TEXT                   -- what Google called it. Evidence, never identity

places
  place_id          INTEGER PK
  google_place_id   TEXT UNIQUE            -- NULL for a place Google doesn't know
  title             TEXT NOT NULL
  address, maps_url TEXT
  lat, lng          REAL
  category          TEXT                   -- display bucket (below)
  google_type       TEXT                   -- Google's primaryType
  google_type_label TEXT                   -- "Seafood Restaurant"
  google_types      TEXT                   -- the full types array, verbatim JSON
  city, country     TEXT                   -- from addressComponents; may be a ward, may be NULL
  destination_id    INTEGER FK -> destinations NOT NULL   -- set on every save (§"Which destination")

trips
  trip_id           TEXT PK                -- a slug: "portugal_2027"
  title             TEXT                   -- NULL -> the destination's name
  destination_id    INTEGER FK -> destinations NOT NULL
  start_date        DATE                   -- NULL until dated
  end_date          DATE
  status            TEXT CHECK(status IN ('draft','archived'))
  is_current        INTEGER                -- one row only, partial unique index
  notes             TEXT
  CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)

wishlist
  wishlist_id       INTEGER PK             -- shown in list output; how rows are addressed
  destination_id    INTEGER FK -> destinations NOT NULL
  place_id          INTEGER FK -> places   -- NULL for an intention with no place yet
  title             TEXT                   -- overrides the place's name; required without one
  city              TEXT                   -- overrides the place's city; groups the list
  notes             TEXT
  priority          INTEGER DEFAULT 3 CHECK(priority BETWEEN 1 AND 5)
  done_at           DATE                   -- set when you've actually been
  added_at          DATETIME
  UNIQUE(destination_id, place_id)
  UNIQUE(destination_id, title)            -- stops duplicate placeless rows, which the
                                           -- first UNIQUE cannot: NULLs are distinct
  CHECK (place_id IS NOT NULL OR title IS NOT NULL)

itinerary
  entry_id          INTEGER PK
  trip_id           TEXT    FK -> trips
  place_id          INTEGER FK -> places   -- NULL for transit and notes
  item_type         TEXT NOT NULL CHECK(item_type IN ('place','lodging','transit','note'))
  title             TEXT                   -- overrides the place's name; required without one
  start_date        DATE NOT NULL
  end_date          DATE                   -- lodging: last night. transit: arrival date
  start_time        TEXT                   -- "HH:MM" local; NULL = untimed
  end_time          TEXT
  departure_timezone TEXT                  -- transit only; NULL = the trip's
  arrival_timezone  TEXT                   -- transit only; NULL = the trip's
  from_location     TEXT                   -- transit; free text
  to_location       TEXT                   -- transit; free text
  confirmation_code TEXT                   -- reference only; drives no behaviour
  notes             TEXT
  created_at        DATETIME
  CHECK (place_id IS NOT NULL OR title IS NOT NULL)
  CHECK (end_date IS NULL OR end_date >= start_date)
  CHECK (start_time IS NOT NULL OR end_time IS NULL)   -- no end without a start
```

### Which destination a row belongs to

**`places.destination_id` is set on every save and is the mechanism the wishlist depends on.** It is
resolved once, when the place is saved:

1. If the caller named a destination, use it.
2. Otherwise, if the trip in context has one, use that.
3. Otherwise, ask. Google's locality is **not** used to pick a destination — it is a ward as often
   as a city, and guessing here is what silently forks a returning trip's list into an empty one.

`manage_wishlist(add)` takes the destination from the place. `manage_place(update)` can move a place
to a different destination when it was resolved wrongly.

### Names, cities, and which column wins

`title` on a `wishlist` or `itinerary` row **overrides** the place's name and is required when there
is no place. `wishlist.city` overrides the place's city the same way, for place-backed rows as well
as placeless ones — a Tokyo venue Google filed under `Shibuya` can be grouped under `Tokyo` without
touching the shared place. Reads are `COALESCE(row.title, place.title)` and
`COALESCE(wishlist.city, places.city)`. A row with neither resolvable city groups under `unsorted`.

`destinations.name` is `UNIQUE COLLATE NOCASE`, so a destination cannot fork on spelling.

### Times

Times are **local wall-clock text**, never converted. `09:00` means 09:00 where the item happens,
which for everything except transit is the trip's destination — always known, because
`destinations.timezone` is `NOT NULL`.

**Transit is the exception, because it is the only thing with two ends.** `start_time` is departure
local, `end_time` is arrival local, as a flight schedule prints it. The timezone columns are set on
the flights into and out of the trip; NULL means the trip's timezone at both ends.

**The arrival date is given, not derived.** `manage_itinerary(schedule)` takes `arrival_date`
alongside `date`; the model is reading a ticket and knows when the flight lands. When it is omitted
and both times are present, a **single convenience inference** applies: same day if `end_time` is
later than `start_time` in the same zone, next day otherwise — correct for the ordinary overnight
and for nothing else, which is why anything crossing zones states the date.

This is deliberate. Deriving the arrival date from resolved instants is circular (resolving an
instant needs the date being derived), needs iteration for a two-day crossing, and has no answer
during a DST fold. Asking removes all three.

**Duration** is computed from the two local times and their two zones, and is shown only when both
zones are known.

### Ordering within a day

1. `continuation` appearances first — something already under way
2. `end` appearances, by arrival time
3. `single` and `start`, by `start_time`
4. untimed items last, by `entry_id`

Where an arrival's zone differs from the trip's, its position is computed from the resolved instant,
not the string — otherwise two clocks are being compared. The arrival is labelled with its zone.
There is no manual reordering: give an item a time instead.

### The day strip

Spans **`min(start_date)` across the trip's entries and the trip's `start_date`**, through
**`max(COALESCE(end_date, start_date))` and the trip's `end_date`**. The min must be over
`start_date` alone — `end_date` is never earlier, so including it in the min would drop the very
red-eye-before-the-trip case the rule exists for.

### Derived, never stored

`day_number` · a trip's display name when `title` is NULL · duration · the wishlist's city
groupings · the day-strip range.

### Categories

`restaurant · cafe · dessert · bar · market · sights · outdoors · shopping · lodging · transit ·
other`. Derived from Google's `primaryType`; NULL when unrecognised, which is distinct from `other`.
`item_type` governs how an itinerary row renders and is independent of a place's `category`.

---

## 2. Tools

| Tool | Actions | Addressed by |
|---|---|---|
| `manage_trip` | list · create · update · set_current · archive · delete | `trip_id` |
| `manage_destination` | list · create · update · merge | `destination_id` or exact name |
| `manage_place` | search · save · list · update · delete | `place_id` / `google_place_id` |
| `manage_wishlist` | list · add · update · remove | `wishlist_id` |
| `manage_itinerary` | list · schedule · reschedule · update · remove | `entry_id` |
| `query_travel_db` | read-only SELECT | — |

`manage_destination` exists so the model can **look before it names**: `list` gives the canonical
spelling, `create` requires a `timezone` (Places never returns one), and `merge` folds one
destination's rows into another — the recovery path for a fork, since a rename would collide with
`UNIQUE(name)`.

**Clearing a value.** Every `update` takes flat scalars, so the empty string means *set to NULL* and
an omitted argument means *leave alone*. Without this there is no way to retract a `done_at`, drop a
wrong `confirmation_code`, or remove an `end_time`.

**Behaviour worth stating:**

- `manage_trip(update)` changing dates moves **nothing**; anything now outside the window is
  reported. Dates cannot be cleared once entries exist.
- `manage_trip(archive)` clears `is_current`; an archived trip is never the current one.
- `manage_trip(delete)` confirms with the owner and cascades to that trip's **itinerary rows only**.
  The wishlist belongs to the destination and is untouched.
- `manage_itinerary(schedule)` accepts `wishlist_id` as well as `place_id` — "book the thing on my
  list" is the commonest flow and needs no id the model has not already seen. It never touches the
  wishlist row.
- There is no `unschedule`: nothing was consumed, so there is nothing to put back.
- `manage_itinerary(reschedule)` changes dates and times; `update` changes everything else,
  including `trip_id` (a thing added to the wrong trip) and `place_id` (a note that turns out to
  have a venue).
- `manage_wishlist(add)` on a place already listed reports "already on your list" and updates the
  note rather than raising a constraint error.
- `manage_wishlist(update)` changes priority, notes, title, city, and sets or clears `done_at`.
  `remove` means "changed my mind"; `done_at` means "I went".
- `manage_place(delete)` is refused while any wishlist or itinerary row references the place.
- `manage_destination(update)` affects every trip to it — which is the point, and what the reply
  says.
- Scheduling into a trip with no dates is refused.
- Every unknown id or action is answered with the valid alternatives.
- Place search is capped at 5 distinct queries per turn; a repeated query is served from memory.

**Reads place an item on every day it touches** — in `manage_itinerary(list)` and in the tile — each
appearance carrying a role: `single`, `start`, `continuation`, `end`.

---

## 3. Interactions

**Save something with no trip**
```
manage_place(search, query="Ichiran", near="Tokyo")
manage_destination(list)                          → "Tokyo" already exists
manage_place(save, google_place_id="ChIJ…", destination="Tokyo")
manage_wishlist(add, place_id=12)                 → Tokyo's list; no trip involved
```

**An intention with no place**
```
manage_wishlist(add, destination="Tokyo", title="somewhere with a view",
                city="Shibuya", priority=2)
```

**Plan a trip**
```
manage_destination(create, name="Portugal", kind="country", timezone="Europe/Lisbon")
manage_trip(create, trip_id="portugal_2027", destination="Portugal",
            start_date="2027-05-22", end_date="2027-05-29")
```

**Schedule something on the list** — by the id the listing just showed
```
manage_itinerary(schedule, trip_id=…, wishlist_id=12, date="2027-05-24", start_time="20:00")
```

**The flight out**
```
manage_itinerary(schedule, trip_id=…, item_type="transit", title="Flight to Lisbon",
                 date="2027-05-22", start_time="06:15", end_time="10:15",
                 from_location="Tel Aviv", to_location="Lisbon",
                 departure_timezone="Asia/Jerusalem", confirmation_code="XY-9988")
→ duration 6h
```

**A two-day crossing — the date is stated, not guessed**
```
… date="2027-06-01", start_time="22:30", arrival_date="2027-06-03", end_time="06:15",
  departure_timezone="America/Los_Angeles", arrival_timezone="Australia/Sydney"
```

**Retracting a mistake**
```
manage_wishlist(update, wishlist_id=12, done_at="")     → cleared
```

---

## 4. Rules Jarvis follows (`SKILL.md`)

1. Address a trip by its `trip_id` and a destination by its exact name; never guess either — list
   and pick.
2. A wishlist entry belongs to a place, not to a trip. Saving one needs no trip.
3. Scheduling a place does not remove it from the wishlist, and a place may be scheduled twice.
4. Times are local wall-clock and are never converted.
5. A trip with no dates cannot be scheduled into; collect wishlist places instead.
5b. Never invent a detail that was not given — a date, a time, an address, a confirmation code —
   even when a tool requires it. Stop and ask.
6. Never change a trip's dates as a side effect of adding an item — schedule it outside the window
   and say so.
7. For a flight that crosses timezones, give `arrival_date` and both timezone columns. Only an
   ordinary overnight in one zone may be left to inference.
8. Record a booking's confirmation code when there is one; add it later with `update` if it arrives
   after the item does.
9. Changing a trip's dates does not move what is scheduled — say what now falls outside, and ask
   what should happen to it.
10. When the owner says they've been somewhere on the list, set `done_at` rather than removing it.
11. An empty string clears a field; omitting an argument leaves it alone.

---

## 5. What extending to several destinations would cost

Not in scope; recorded so the boundary is known.

**Free.** `wishlist` and `places` are already anchored to a destination rather than a trip. That
migration is mechanical and it happens in this version.

**Not free.**

- **`itinerary` has no timezone of its own for non-transit rows** — a dinner takes its zone from the
  trip. Under legs it would have to take it from the leg covering its date, so the leg reference is
  not an added convenience, it is the only thing that would give an existing row a zone. And that
  resolution is undefined precisely where this design permits items to exist: outside the trip
  window, and spanning a boundary. **This is a semantic migration, not a data one — it cannot be
  scripted, which makes it the expensive part, not the wishlist re-anchoring.**
- **The day strip and the intra-day ordering become per-leg.** Both are written per-trip today, and
  "local" stops having one meaning when a strip spans two zones.
- **Tool signatures change**: `manage_trip(create, destination=…)`, `manage_wishlist(list,
  trip_id=…)` returning several groups, `manage_itinerary(schedule)` resolving an ambiguous date.
- **`SKILL.md` rules 4 and 7 change**, since both name "the trip's" timezone as a singular fact.
- **New table** `trip_legs`; `trips.destination_id` migrates into a single leg, then drops.
