# Fitness — the app tile for `roiguri/jarvis-app`

**Status:** Phase 1 (agent-side app surface) IMPLEMENTED & VERIFIED against real production data,
on branch `feat/fitness-app` — not yet merged to `main`. Archived here since the design/handoff work
for this phase is done; the handoff section below is live for the Android client work even though
this plan itself is no longer being actively edited. Phase 2 (app client) is tracked in
`roiguri/jarvis-app` from that handoff.
**Date:** 2026-08-06.
**Goal:** a read-only fitness screen in the jarvis-app hub: per-plan progress at a glance, upcoming
classes, and history you can browse and search — with zero new storage. Everything it needs already
exists in `fitness.sqlite` (`tools/fitness/fitness_tools.py`); this plan is entirely
`gateway/apps/fitness.py` plus one import line, following the `travel`/`memory` app precedent.
**Source of truth:** this document.

---

## Checklist

**Phase 1 — app surface (agent side)**
- [ ] `gateway/apps/fitness.py` — `AppSpec(ns="fitness", name="Fitness", entries=(...))`, five entries
      (below)
- [ ] One import line in `gateway/apps/specs.py`
- [ ] Every blocking call through `asyncio.to_thread`
- [ ] `scripts/ci/check_channel_agnostic.py` green
- [ ] Live: `declared N apps to the jarvis-app hub` on restart, each entry answers

**Phase 2 — app client** *(handoff to `roiguri/jarvis-app`, written after Phase 1 ships from the
real payload — not from this sketch)*
- [ ] Handoff doc, `FITNESS_APP_HANDOFF.md`, written against the shipped response
- [ ] `ui/apps/fitness/` package + one line in `AppCatalog.kt`

---

## Decisions

**No new tables, no new tools.** Every number on the screen is already derivable from `plans`,
`workouts`, `exercise_logs`, `cardio_logs`. The app module reads SQLite directly rather than calling
the fitness *tools* — same reasoning as `travel.py` and `memory.py`: tool output is English written
for a model, and parsing it here would ship a client that breaks the day a docstring is reworded.

**Read-only v1, confirmed.** No logging from the app. Writes stay in chat, where the confirmation
plane already exists for the destructive ones (there are none here yet).

**Header is per-plan, not blended.** Two plans can be `active` at once (CrossFit + running today),
and they answer different questions — collapsing them into one number would hide which plan is
behind. Each active plan gets its own row: marks, target, streak. A plan that is `paused` or
`completed` does not appear in the header at all (it isn't being worked toward right now); it still
shows up in History, since its past sessions are still real.

**Marks: past year, one block per week, GitHub contribution-graph style.** 52 blocks, oldest→newest,
each block = count of that plan's `completed` workouts in that week (not a single day like GitHub —
a week is the right grain here since a plan's whole cadence is weekly). A header line reports the
year total (`total_year`), same as GitHub's "N contributions in the last year."

**A week's mark is plan-scoped.** For CrossFit's row, a block counts that plan's `completed`
workouts in that week; running's row counts running ones. Falls out of `workouts.plan_id` for free —
no extra join, no cross-plan bleed.

**Streak has no window — it's exactly as long as it's been held.** Not capped at 52 weeks or any
other number: walk backward from the current week, counting consecutive weeks the target was met,
until the first miss or the plan's `start_date`. A plan held for a year straight reports a streak of
52, independent of the 52-week marks array (which would also be fully lit in that case, but the two
numbers are computed separately — the marks window is a *display* choice, the streak is not bounded
by it). Same walk `get_adherence_report` already does, just uncapped instead of stopping at its
`weeks` param.

**Target with no `weekly_target_count`.** `weekly_target_count` is nullable on `plans`, and a
`strict_sequential` plan (running) may reasonably not set one — it's ordered sessions, not a weekly
count. When it's `NULL`, the target and streak sub-fields report `null` rather than a fabricated
number; the client shows marks only for that plan's row. (Today's running plan does carry a target,
per the SKILL.md phase/session grammar, so this is a defensive case, not the common one.)

**Upcoming is `status='scheduled'` AND in the future.** `scheduled` also covers a past class that
`sync_arbox_attendance` hasn't resolved yet (it runs periodically, not on every tick) — a real limbo
state, not a bug. Filtering on `scheduled_time >= now` keeps that limbo window out of Upcoming; it
also won't appear in History, since History is `completed`/`missed` only. Accepted gap: a handful of
hours where a just-finished class shows in neither list until the next sync. Not worth chasing for
v1 — `sync_arbox_attendance` already exists and closes it on its own schedule.

**History mixes session types, newest-first, cursor-paginated.** One list, `workouts` filtered to
`status IN ('completed', 'missed')`, left-joined to `cardio_logs` for running rows. Cursor is
`before` (an ISO timestamp) rather than an offset, so a session logged between page loads can't shift
the second page — same idiom `memory.py` uses for `path`. Page size is a server-side constant (20),
not a client param, for the same reason the memory app caps a read at 1MB rather than trusting the
caller: a bounded default that can't be widened into a footgun.

**Pain level is detail-only.** `History` rows don't carry `pain_level`; `Session` (the drill-down)
does. Keeps the list scannable and matches what was asked for.

**Exercise browsing is two entries, not one.** `Exercises` (no params) lists distinct names for
browsing/typeahead; `ExerciseLog` (params: `exercise_name`) returns one exercise's full history.
Splitting them mirrors `memory.py`'s `list`/`read` split — a browse step and a fetch step are
different navigations, and collapsing them into one entry with an optional param would make the
response shape depend on whether a param was set, which is worse to document and worse to type on
the client.

**`Exercises` groups case-insensitively.** `exercise_name` is stored as typed (`log_exercise_stats`
does `.strip()`, no case-folding), so "Back Squat" and "back squat" would otherwise list as two
exercises. Grouping by `LOWER(exercise_name)` and displaying the most-recently-used casing avoids
that; it's a display fix only; nothing in the DB is renamed.

**`ExerciseLog` is exact match, case-insensitive — not substring.** `query_exercise_history` (the
chat tool) does substring `LIKE '%name%'` because the model might not type the exact stored name.
The app doesn't have that problem: the client always got `exercise_name` from an `Exercises` row, so
it already has the canonical spelling. Exact match keeps "Press" from also pulling in "Overhead
Press" and "Bench Press" when the intent was one specific lift. Sorted newest-first, per your call
above (progression-reading, not PR-hunting).

**Five entries, not one tile.** Travel's tile is one entry because the whole payload is bounded by
construction (one trip, a couple of weeks). Fitness isn't: history is unbounded and needs paging,
and exercise search is a genuine query, not a fixed screen. Splitting `Dashboard` from `History`
from `Session` from the two exercise entries keeps each response small and keeps paging/searching
from re-fetching the header every time.

---

## Phase 1 — app surface

`gateway/apps/fitness.py`, `AppSpec(ns="fitness", name="Fitness", entries=(...))`.

### `Dashboard` — `GET`, no params

Opening payload: header rows for every `active` plan, plus Upcoming. Bounded (a couple of plans, a
handful of future classes), fetched once per screen-open — same shape reasoning as travel's tile.

```
plans      [{ plan_id, name, tracking_mode, weekly_target_count,   -- null if unset
               this_week_done, streak_weeks,                        -- null if weekly_target_count is null; uncapped
               total_year,                                          -- completed-session count, past 52 weeks
               marks: [{ week_start, count }, ...] }]                -- 52 weeks, oldest first
upcoming   [{ workout_id, scheduled_time, session_type, description, source }]
                                                                      -- status='scheduled', future only
```

### `History` — `GET`, params: `before`

```
sessions   [{ workout_id, scheduled_time, session_type, status,     -- 'completed' | 'missed'
              summary,                                              -- wod_result, or "distance · pace"
              plan_id }]
next_before  <timestamp>  |  null                                   -- null = no more pages
```

`summary` is pre-formatted server-side (not raw fields) so the client renders a list row without
knowing crossfit vs. running formatting rules — same reasoning `travel.py`'s `_entry()` gives for
computing `crosses_midnight` itself rather than leaving the client to derive it.

**`summary` formatting — decided against real prod data** (`/app/jarvis_data/fitness/fitness.sqlite`,
22 workouts inspected 2026-08-06):

- **CrossFit:** `wod_result` if non-empty (it's already a short human-written result, e.g. `"3 Wall
  Walks per round (Single Unders)"`). Else `notes`, truncated, if present. Else the `description`'s
  bracketed tag plus its first ~40 chars (e.g. `"[PUMP] 20 sec on / 20 sec off x 12..."`) — the
  fallback that matters: roughly 1 in 6 real sessions have both `wod_result` and `notes` blank, with
  only the assigned WOD text (`description`) to show.
- **Running:** compose from whichever `cardio_logs` fields are non-null, same pattern
  `get_weekly_fitness_summary` already uses: `"{duration} min"` always present, then append
  `"· {distance} km"`, `"· {pace}/km"` (via the existing `_fmt_pace` helper), `"· HR {hr}"` for each
  field that isn't null. Real data has both ends: one logged session has only `duration_min` (`"just
  a long walk"`, no watch data), most have all four fields.

### `Session` — `GET`, params: `workout_id`

Full detail for one history row.

```
workout_id, scheduled_time, session_type, status, description, notes, wod_result
exercise_logs   [{ exercise_name, weight, sets, reps, notes, logged_at }]   -- crossfit
cardio          { duration_min, distance_km, avg_pace_sec, avg_hr,
                  pain_level, prehab_done, prehab_notes, notes }  |  null   -- running
```

### `Exercises` — `GET`, no params

```
exercises   [{ exercise_name, last_logged_at, log_count }]   -- LOWER()-grouped, newest-used first
```

### `ExerciseLog` — `GET`, params: `exercise_name`

```
exercise_name
entries   [{ weight, sets, reps, notes, logged_at }]   -- exact match (case-insensitive), newest-first
```

Handlers raise `AppNotFound` for an unknown `workout_id`/`exercise_name`, `AppInvalidRequest` for a
malformed `before` cursor. Every blocking call goes through `asyncio.to_thread`, matching `travel.py`
and `memory.py` — one event loop serves the poll, any in-flight turn, and this drain.

---

## Handoff — Android app (`roiguri/jarvis-app`)

**For:** the Android client. **Agent side is implemented and verified** against real production data
(`/app/jarvis_data/fitness/fitness.sqlite`) but not yet merged to `main` — currently on branch
`feat/fitness-app`. **Design reference:** `Jarvis Fitness.dc.html` in the
[Jarvis Android app design brief](https://claude.ai/design/p/9e8f2146-d1f6-48ed-9511-d23fdf626786?file=Jarvis+Fitness.dc.html)
project — that mock is the source of truth for layout/interaction; this document is the source of
truth for the data contract. **Every sample below is a real response**, not a sketch.

### Wiring

`GET /v1/apps/fitness/q/<entry>`, same convention as `memory`/`travel`:

| Entry | Params |
|---|---|
| `dashboard` | none |
| `history` | `before` (optional — an ISO timestamp cursor; omit for the first page) |
| `session` | `workout_id` (required) |
| `exercises` | none |
| `exercise_log` | `exercise_name` (required — exact, case-insensitive; get it from an `exercises` row, don't hand-type it) |

**Errors:** `not_found` for an unknown `workout_id`/`exercise_name`; `invalid_request` for a
malformed `before` cursor or an undeclared param. Both are caller mistakes. Anything else is an
internal fault and should read as one.

### `dashboard` — real response

One row per `active` plan (today, that's exactly one — "Weekly CrossFit"), plus `upcoming`.

```json
{
  "plans": [
    {
      "plan_id": 1, "name": "Weekly CrossFit", "tracking_mode": "flexible_quota",
      "weekly_target_count": 2, "this_week_done": 0, "streak_weeks": 0, "total_year": 13,
      "marks": [
        { "week_start": "2025-08-10", "count": 0 },
        { "week_start": "2025-08-17", "count": 0 },
        "... 49 more, oldest → newest ...",
        { "week_start": "2026-07-26", "count": 1 },
        { "week_start": "2026-08-02", "count": 0 }
      ]
    }
  ],
  "upcoming": []
}
```

`upcoming: []` above is the **real current state** — today's class already passed, not a placeholder
for "empty state not designed yet." `weekly_target_count`/`this_week_done`/`streak_weeks` are
tabular-nums-friendly: `weekly_target_count` and `streak_weeks` are `null` together when a plan sets
no target (nothing to divide by, nothing to hold a streak against) — render marks only, no ring/pips,
no streak number, in that case. `marks` is always exactly 52 entries, oldest first; `total_year` is
their sum, shown once above the strip (GitHub's "N contributions" line, weekly grain not daily —
that's the whole reason it's a block-per-week strip and not a day grid).

A populated `upcoming` row:

```json
{ "workout_id": 1606, "scheduled_time": "2026-08-06 19:00:00", "session_type": "crossfit",
  "description": "[WOD NEVE TZEDEK] every 1:30 x 8 sets - alt betwen ...", "source": "arbox" }
```

`description` is `null` when Arbox hasn't posted the WOD yet — render "Not posted yet," not a blank.
**`session_type` is always `"crossfit"` here** — see the "not backed yet" note below for why a run
can never appear in this list.

### `history` — real response (first page)

```json
{
  "sessions": [
    { "workout_id": 1560, "plan_id": 3, "scheduled_time": "2026-07-29 00:00:00",
      "session_type": "running", "status": "completed", "summary": "40 min" },
    { "workout_id": 1511, "plan_id": 1, "scheduled_time": "2026-07-28 20:00:00",
      "session_type": "crossfit", "status": "completed",
      "summary": "3 Wall Walks per round (Single Unders)" }
  ],
  "next_before": "2026-05-11 20:00:00"
}
```

`summary` is the **one** string to render — it's already resolved server-side (wod_result → notes
→ WOD text for crossfit; composed duration/distance/pace/HR for running). Don't try to re-split it
into a score + caption. `plan_id` can be `null` (a class fetched while no plan was `active`).
`next_before` is `null` on the last page — pass it back as `before` to fetch the next one; there is
no total-count field, page until `next_before` is `null`.

### `session` — two real shapes, by what's actually logged

A **crossfit** session (lift logged, no cardio):

```json
{
  "workout_id": 1511, "plan_id": 1, "scheduled_time": "2026-07-28 20:00:00",
  "session_type": "crossfit", "status": "completed",
  "description": "[WOD NEVE TZEDEK] every 2:00 x 5 sets :\n5-4-3-3-3 \npush press...",
  "notes": "Consistent 3 wall walks per round. Still doing single unders.",
  "wod_result": "3 Wall Walks per round (Single Unders)",
  "exercise_logs": [
    { "exercise_name": "Push Press", "weight": 32.5, "sets": 3, "reps": 3,
      "notes": "Built to 32.5kg for the final sets. Felt like a great workout.",
      "logged_at": "2026-07-28 18:00:34" }
  ],
  "cardio": null
}
```

A **running** session (cardio logged, no lifts) — `exercise_logs: []`, `cardio: {...}` — see the
sample already in Phase 1 above. **An upcoming (not-yet-happened) `workout_id` returns the same
shape** with `notes`/`wod_result` both `null` and `exercise_logs: []`/`cardio: null` — that's how the
client tells "nothing logged yet" from "logged, nothing recorded." Render only `description` (the WOD
text) for that case; there is no separate "upcoming" response shape to branch on.

### `exercises` / `exercise_log` — real responses

```json
// exercises
[
  { "exercise_name": "Bench Press", "last_logged_at": "2026-08-06 17:04:28", "log_count": 2 },
  { "exercise_name": "Power Clean", "last_logged_at": "2026-07-14 06:05:27", "log_count": 4 }
]

// exercise_log?exercise_name=Power+Clean — every entry, newest first, NOT just PB + last
{ "exercise_name": "Power Clean", "entries": [
  { "weight": 37.5, "sets": 5, "reps": 2, "notes": "1.1 complex, building to 9 RPE. Felt strong.",
    "logged_at": "2026-07-14 06:05:27" },
  { "weight": 37.5, "sets": 5, "reps": 2, "notes": "1.1 complex, building to 9 RPE. Felt strong.",
    "logged_at": "2026-07-14 06:05:12" }
] }
```

The first two `Power Clean` entries above **are the known duplicate-log artifact** from the "Known
data quirk" section below — real data, not a client bug. There's no server-computed "PB" flag; if the
design wants one, derive it client-side from the full list (max `weight`), same as any other reader
of this array could.

### Screen

Three tabs — **Progress** (dashboard: per-plan marks/streak/target + upcoming), **History**
(paginated, tap a row for `session` detail), **Exercises** (browse `exercises`, tap one for its full
`exercise_log`). Exact layout, colors, and interaction follow `Jarvis Fitness.dc.html`.

### Designed in the mock, not backed by any endpoint yet

The Android mock intentionally designs a few things ahead of the backend — **don't wire them to a
real call, they'd 404 or don't exist:** friends attending a class, "Cancel registration"/"Reserve a
spot," the "Ask Jarvis to modify" chat deep-link, and a planned/scheduled run. Full reasoning for each
is in "Designed ahead of the backend" further down this document — read it before scoping any of
them, since two are architecturally blocked (no write tool exists at all), not just unwired.

---

## Out of scope, with reasons

- **Writes / logging from the app.** Confirmed not needed yet. `log_exercise_stats`,
  `log_wod_result`, `log_running_session` stay chat-only.
- **Friends.** The `friends` table drives booking-buddy hints in chat, not history or progress —
  no natural home on this screen.
- **`missed` breakdown / reasons.** History shows a missed session as a row like any other; nothing
  today captures *why* it was missed.
- **Cross-plan combined marks.** Considered and rejected above — per-plan is the whole point of
  "which plan is behind."

### Designed ahead of the backend — flagged for the app handoff

The `Jarvis Fitness.dc.html` Android mock (`claude.ai/design` project) intentionally designs a few
things this plan and the shipped endpoint do **not** back yet. Recorded here so the handoff doc
doesn't imply they're wired, and so picking any of them up later starts from the real gap:

- **Friends attending a class.** `fetch_weekly_gym_schedule`'s friend-matching cross-references the
  `friends` table against Arbox's live `booked_users` field — that pairing is never persisted to any
  table. `Dashboard.upcoming` is a DB-only read (same pattern as every other entry), so surfacing
  this needs either a live Arbox call from the app handler (a first, breaking that pattern) or
  persisting matched friends at upsert time in `fetch_upcoming_arbox_classes`. Neither exists.
- **Cancel registration / reserve a spot.** No write tool against Arbox exists at all — not a
  missing app-layer wrapper, the underlying tool doesn't exist in `tools/fitness/`.
- **"Ask Jarvis to modify" chat deep-link.** Same class of gap travel already logged in its own
  plan: needs a navigation block kind in the jarvis-app hub contract before chat can be opened
  pre-filled from a tile. Not a Jarvis-side change.
- **Scheduling a future run.** `log_running_session` can't represent a planned-but-not-run session —
  `status='completed'` is a hardcoded SQL literal (not a parameter), `duration_min` is required, and
  a `cardio_logs` row is inserted unconditionally right after. A "planned run" would need a new
  tool/action: insert a bare `workouts` row (`status='scheduled'`, no `cardio_logs` yet), then a
  second step to attach real numbers and flip it to `completed` — mirroring how Arbox rows get
  upserted then resolved by `sync_arbox_attendance`. Until that exists, `Upcoming` can only ever
  contain Arbox-sourced CrossFit classes, never a run.

---

## Open questions

None outstanding — window, streak, and summary formatting are all settled above.

## Known data quirk (not fixed by this plan)

One `exercise_logs` row in prod (`log_id 18`) has `workout_id = NULL` and duplicates another row
that's correctly attached to a workout (`log_id 16`, same weight/sets/reps/notes, same day) — looks
like a logging artifact, not a real second set. It won't appear in any `Session` drill-down (nothing
to join to), but *will* surface in `ExerciseLog` search since that queries `exercise_logs` directly,
where it'll read as an unexplained duplicate. Not proposing a fix here — flagging so it isn't
mistaken for an app bug when it shows up during Phase 1 verification.

---

## Verification

Per repo norms, Claude cannot restart the service — each phase ends with the owner restarting and
checking. Phase 1: confirm `declared N apps to the jarvis-app hub` includes `fitness` on restart,
then exercise each entry (`Dashboard`, `History` across a page boundary, `Session` on both a crossfit
and a running row, `Exercises`, `ExerciseLog`) against real staging data via the app query path.
Phase 2 is exercised from the app client once the hub has re-declared the manifest.
