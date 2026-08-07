# Travel — day tags

**Status:** planned, not started.
**Date:** 2026-08-07.
**Goal:** a day-level label on a trip ("beach", "rest day"), freeform, spanning one or more dates,
with an optional description — rendered on the day header, not as a timeline item.

---

## Decision

Reuse the `itinerary` table with a new `item_type = 'tag'`, rather than a new table. A tag is
`title` + `start_date`/`end_date` + `notes` (the description) — every column it needs already
exists. `place_id`, `start_time`, `end_time` are never set for a tag; multi-day span and trip-window
extension come free from generic `end_date` handling and `day_span()` already walking every row's
date range.

The only behavioral change beyond "allow this enum value" is rendering: a tag must NOT appear in a
day's item list (where it'd read as an untimed `note`). It's excluded the same way `lodging` is, and
folded into the day header instead: `Day 3 · 2026-09-10  [beach, rest]`.

Accepted gap: no duplicate/overlap detection for tags (place-on-same-day has a clash check via
`place_id`; tags have none, since two different tags legitimately overlap).

---

## Changes

**`tools/travel/_db.py`**
- Add `'tag'` to the `itinerary` CHECK's `item_type IN (...)` list in the `CREATE TABLE IF NOT
  EXISTS` script (fresh DBs).
- New idempotent migration, same shape as `_migrate_lodging_check`: rebuild `itinerary` if `'tag'`
  isn't yet in the live schema's CHECK text. SQLite can't `ALTER` a CHECK constraint.

**`tools/travel/itinerary.py`**
- `ITEM_TYPES`: add `"tag"`.
- `_schedule()`: route `kind == "tag"` alongside `("transit", "note")` — needs only `title`, skips
  `_resolve_place`. Reject `start_time`/`end_time`/`place_id`/`google_place_id` when `kind ==
  "tag"` (explicit `TravelError`, mirroring the lodging-specific checks already there).
- `_reschedule()`: reject `start_time`/`end_time` args when `entry["item_type"] == "tag"`.
- `place_rows()`: exclude `item_type == "tag"` the same way `lodging` is excluded (line 193).
- New helper, e.g. `day_tags(rows) -> dict[str, list[str]]`: walks each tag row over
  `start_date..end_date` (same day-walking loop as `place_rows`, no roles — a tag is just
  present/absent per day) and collects titles.
- `_itinerary_lines()`: append the joined tag list to each day's `head` line when present.
- `manage_itinerary` docstring: document `item_type='tag'` — day-level label, `title` + optional
  `end_date` + `notes`, no time, no place.

**`tools/travel/SKILL.md`**
- One line: tags are freeform text, no fixed vocabulary, scheduled the same way as any other
  itinerary entry via `manage_itinerary`.

---

## Verification

- Schedule a tag spanning two days; `list` shows it in both days' headers, not in either day's item
  list.
- Attempt `schedule` with `item_type='tag'` and a `start_time` — rejected.
- Attempt `schedule` with `item_type='tag'` and no `title` — rejected (same message shape as
  transit/note today).
- `remove`/`update` on a tag's `entry_id` work unchanged (generic path, no special-casing needed
  there).
