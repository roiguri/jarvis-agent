# Fitness logging — compositional session model, explicit plan binding

**Status:** planned, not started.
**Date:** 2026-09-06.
**Goal:** make "a workout happened" recordable from anywhere (travel, hotel WODs, other gyms),
attach workouts to plans explicitly instead of by heuristic, and restructure the logging tools
around the create-then-enrich split the whole industry converged on — while fixing two latent
bugs the design review surfaced and reserving the seams a future Pixel-Watch sync will need.

---

## Context

Arrived here via a same-day discussion that started with "how is the fitness streak calculated?"
and a planned trip abroad. The chain of findings:

1. **Streaks are per-plan and pause-blind.** `_streak_weeks` (gateway/apps/fitness.py) counts
   completed workouts per `plan_id` per Sunday-anchored week; pausing a plan only hides it from
   the dashboard — on reactivation, the paused weeks are misses and the streak resets to zero.
2. **Workout creation is 100% coupled to its source.** Only two paths insert `workouts` rows:
   the Arbox class sync (`tools/fitness/classes.py`) and `log_running_session`
   (`tools/fitness/logs.py`). A hotel WOD, a session at another gym, or any travel workout is
   *unrecordable*, which silently breaks streaks and understates the year totals.
   `log_exercise_stats` only enriches an existing row; `query_fitness_db` is read-only.
3. **Plan attachment is implicit magic.** Arbox workouts attach to `SELECT plan_id FROM plans
   WHERE status='active' LIMIT 1` (first active plan, no ORDER BY); runs attach to the active
   plan named `LIKE '%Running%'`. Creating a plan creates no place to log into it.
4. A three-way design debate (per-shape tools / fully compositional / one fat tool — full cases
   in Appendix A) picked the **compositional** design, decisively because only an enrichment-
   shaped cardio tool can attach stats to a row the Arbox sync owns (the Saturday Endurance
   class is a real, current instance of that gap).
5. Research into open-source fitness apps and platform health standards (Appendix B)
   independently validated the same split — and rejected adopting any of them as a backend.

**Today's baseline:** `plans` → `workouts` (status scheduled/completed/missed, `session_type`
TEXT, `arbox_class_id` UNIQUE, `source`) → `exercise_logs` + `cardio_logs`
(`duration_min NOT NULL`); `plan_target_history` judges each week by the target in force when
it began; weekly adherence/streak math exists **twice** (tools/fitness/reports.py and
gateway/apps/fitness.py) with two known behavioral divergences.

---

## Decisions carried in from discussion

- **Compositional (create-then-enrich) tool design.** One generic `log_workout` creator; stats
  attach afterward via per-shape enrichers. This matches wger's `WorkoutSession`/`WorkoutLog`,
  FitTrackee's create-then-PATCH, Endurain's parent + per-shape child tables, and Health
  Connect's session-record + measurement-records model. No open-source app has per-sport
  creation endpoints; per-activity logger tools would recreate what none of them do.
- **`log_running_session` is retired**, via a short deprecation shim (internally = create +
  enrich) so the 50-message thread window doesn't keep teaching the old pattern mid-cutover.
- **Enrichers find-or-create their parent** (wger's `assign_session()` lesson): a cardio or
  strength log with no matching workout row today auto-creates one instead of erroring. This
  restores one-call logging for "ran 5k in 28 min" and removes the two-call fragility that was
  the compositional design's main weakness.
- **Keep SQLite; adopt no open-source backend.** wger has no streaks/missed-state and no real
  cardio model; FitTrackee has no strength modeling; OpenTracks' API is read-only; Endurain is
  a full self-host stack whose value (GPS streams, maps, Strava import) isn't needed. The
  plans/targets/streaks layer has no counterpart in any of them — it stays ours. Design ideas
  reimplemented from this plan are clean re AGPL; no code is copied.
- **Plan attachment becomes explicit** via a `plans.binding` column; the two creation-path
  heuristics are retired, and their last act is a one-time backfill that records their answer.
- **No plan deletion / table-clearing mechanism.** Retiring a plan is `status='completed'`;
  old plans' workouts must stay in the shared tables (year totals and overall marks are
  plan-agnostic by design). Rows are cheap; history is the point.
- **Pixel-Watch ingestion is a separate later slice** — this plan only reserves its seams
  (generalized `(source, external_id)` key; creator callable with a supplied source/id/time).
  Cross-source dedup is genuinely hard (no platform auto-merges sessions; see Appendix B) and
  deserves its own tested slice.
- **Streak-math consolidation happens first**, before any semantics-adjacent change, so future
  edits land in one implementation instead of two divergent ones.
- **Travel and streaks:** with these tools, a trip becomes a real choice — keep the main plan
  with a lowered target (`plan_target_history` protects past weeks) and log manual sessions
  against it, or open a separate travel plan and let the main streak reset knowingly. No
  "exemption week" semantics are added; a streak is preserved by actually training at a
  declared target.

---

## Slice 0 — consolidate streak/adherence math + fix latent read-side bugs

**Goal:** one implementation of week/target/streak logic; the app can't crash on rows the new
tools will create.

- New shared module (e.g. `tools/fitness/_adherence.py`, imported by both `reports.py` and
  `gateway/apps/fitness.py` the same way `_db` already crosses that boundary): Sunday-week
  iteration, per-week completed counts, `_target_for_week` lookup, and one streak function.
  Resolve the two known divergences deliberately: the streak is *not* bounded by a report
  window (the app's semantics win; the chat report says so when its window truncates the
  display), and a zero/absent-target week ends the walk in both.
- **Bug fix:** `_running_summary` (gateway/apps/fitness.py) formats
  `cardio['duration_min']` from a LEFT JOIN — a `session_type='running'` workout with no
  cardio row is a `TypeError` that faults the whole history endpoint. Guard it (fall back to
  the description/no-stats summary). Needed regardless of design; mandatory once creation and
  cardio stats are separate writes.
- No behavior change intended beyond the two named divergences; verify by comparing
  `get_adherence_report` and app-dashboard streaks before/after on the live staging DB.

## Slice 1 — schema: binding, generalized external id, session types

All via the existing `_init_db` migration pattern (try/except `ALTER TABLE` list + idempotent
backfills, tools/fitness/_db.py) — lands identically whether staging next boots via chat or an
app poll.

- `ALTER TABLE plans ADD COLUMN binding TEXT` — values `'arbox' | 'running' | 'manual'`,
  validated in `manage_fitness_plan` (same pattern as `tracking_mode`; no retrofitted CHECK).
  Backfill applies the retiring heuristics once:
  name-`LIKE '%Running%'` → `'running'`; lowest-id remaining active plan → `'arbox'`;
  guards `WHERE binding IS NULL` keep it a no-op afterward.
- Rename `workouts.arbox_class_id` → `external_id` (SQLite `ALTER TABLE ... RENAME COLUMN`;
  wrap in the same try/except), plus
  `CREATE UNIQUE INDEX IF NOT EXISTS idx_workouts_source_external ON workouts(source,
  external_id) WHERE external_id IS NOT NULL`. This is the platform-standard idempotency key
  (Health Connect `clientRecordId`, HealthKit `syncIdentifier`, Strava `external_id`);
  `source='google_health'` becomes a legal future value with no further migration.
- New `session_types(name PRIMARY KEY, stat_shape)` table, `stat_shape ∈
  {'strength','cardio','none'}`, seeded in `_init_db` (crossfit→strength, running→cardio,
  plus a small starter set: walking/hiking→cardio, mobility→none). Code-seeded, not
  agent-writable — but it is a **soft vocabulary**: it drives the enrichers' automatic
  routing ("which child table fits this row?"), it does not gate creation. `log_workout`
  accepts an unknown `session_type` as-is — the row is created, counts toward its plan and
  streak, and renders via the description fallback the app already has — it just gets no
  automatic stat routing, and the reply says so. Structured stats still aren't blocked: the
  enrichers take an explicit `workout_id`, bypassing routing. Adding *automatic* routing for
  a new type is a one-line seed edit, not a schema change (FitTrackee's sport-table lesson).
- Timezone: **no storage change in this plan.** `scheduled_time` stays Israel-local text (all
  week math and the Arbox sync assume it); the `logged_at`-is-UTC inconsistency is recorded
  here as a known wart. The OpenTracks-style fix (UTC instant + captured local offset) touches
  every query and is deferred to its own slice if `/tz` away mode ever needs it.

## Slice 2 — tools: creator, enrichers, retirement, gating

- **New `log_workout(description, date=today, session_type='crossfit', plan=None)`**
  (tools/fitness/logs.py): inserts a completed workout row, `source='manual'`,
  `scheduled_time = date + a midday sentinel time` (see tie-break below). Plan resolution:
  explicit `plan` (name or id) wins; else the active plan whose `binding` matches the
  session type's natural home (crossfit → the arbox-bound plan, running → the running-bound
  plan); ambiguity or no match → error string naming the candidates, never a silent NULL —
  an unattached row would fix the year totals but not the per-plan streak, which is the point
  of this whole plan. Unknown session types are accepted (soft vocabulary — see Slice 1);
  the reply notes that no automatic stat routing applies. Internally callable with
  `(source, external_id, time)` for the future watch sync. Reply text leads with the new `workout_id` and, for cardio-shaped types, an
  explicit next step: "now call `log_cardio_stats(workout_id=N)` with the session stats" —
  in-context return-value chaining, the dependable mechanism, with docstrings as
  reinforcement.
- **New `log_cardio_stats(duration_min, distance_km=None, avg_pace=None, avg_hr=None,
  pain_level=None, prehab_done=None, prehab_notes=None, notes=None, workout_id=None)`**:
  the cardio enricher, mirroring `log_exercise_stats`. Auto-link targets today's latest
  workout **without an existing cardio row and whose session type's `stat_shape` is cardio**;
  if none exists, find-or-create a `session_type='running'` workout via the `log_workout`
  path (wger's auto-vivification) — one call logs a run end-to-end. `duration_min` stays
  required (schema `NOT NULL`).
- **`log_exercise_stats` auto-link hardening:** restrict to today's latest workout whose
  `stat_shape` is strength; keep the explicit `workout_id` escape hatch. Together with manual
  rows' midday sentinel time (vs Arbox rows' real class time), this settles the
  double-session-day tie-break that previously attached stats to the wrong row.
- **`log_running_session` is deleted outright** (no deprecation shim). A shim was planned
  to keep recent thread history executable, but the observability log showed the tool was
  called exactly twice ever in prod, last on 2026-07-29 — far outside any 50-message
  window — so there is no in-context history to protect.
- **Provenance gating** (FitTrackee's lesson): tools never edit the identity fields
  (`scheduled_time`, `external_id`, status flips owned by the sync) of `source='arbox'` rows;
  enrichment (`wod_result`, stats, notes) stays allowed. Manual rows stay fully editable.
- **Rewire the two heuristics:** Arbox sync resolves `binding='arbox'` (exactly one active
  match expected; zero or many → the sync reports an error string, never guesses); the shim /
  cardio path resolves `binding='running'`.
- **SKILL.md rewrite:** the logging rules (immediate logging, form-prefill flow, running-
  program phase descriptions, pain flagging) re-anchored on the new tools; the form flow
  becomes "log the workout when the owner says they trained; the submitted stats form maps to
  one `log_cardio_stats` call." Add the boundary rule for gym days: sessions the Arbox sync
  owns are enriched, never re-created (`log_workout` also refuses when a same-day row already
  covers the session, with a redirect to the enrichers).

## Slice 3 — verification (staging, live)

- Migration: boot staging, confirm columns/index/seeds exist and backfills stamped the two
  real plans correctly; confirm the app dashboard still renders (binding is invisible to it).
- Tool behavior, via Telegram against staging: log a manual WOD + lifts (creator + both
  enrichers, right plan, streak/count moves); log a run in one utterance (auto-vivification);
  report stats after a synced Arbox class (enrich, no duplicate row); attempt an ambiguous-
  plan log (clean error). Re-run `sync_arbox_attendance` twice for idempotency under the
  renamed column.
- Read side: `get_adherence_report` vs app dashboard streak equality; history endpoint with a
  stats-less running row (the slice-0 guard).

## Deferred (recorded, not planned here)

- **Pixel-Watch session sync** — heartbeat-driven ingestion from the existing
  `tools/google_health` integration: time-overlap matching against Arbox/manual rows
  (~±45 min same civil date), fixed source priority (Arbox > watch-measured > watch-manual),
  matched sessions enrich (`cardio_logs` fits the watch summary exactly; capture the
  dataPoint `name` as `external_id`), unmatched sessions — if imported at all — get
  `plan_id NULL` so they never touch adherence math. Open questions: threshold tuning,
  MANUAL-entry policy, whether unmatched sessions deserve rows.
- **Timezone storage fix** (UTC instant + captured offset) — see Slice 1.
- **PB/records table** (FitTrackee-style stored records with delete-triggered recompute) —
  derive on read until actually wanted.
- **`strict_sequential` tracking_mode** — schema/validation vocabulary with no implementation;
  running sequencing lives in the owner's program memory notes. Drop or implement, some other
  time.
- **`friends` write path** — hand-maintained by design for now.

---

## Appendix A — design alternatives considered

Three advocate cases were argued against the same five scenarios (run abroad; hotel WOD +
lifts; a future activity type; "ran 5k" with no stats; form-submitted log):

- **A — per-shape tools** (keep `log_running_session`, add plain `log_workout`): wins raw
  one-call ergonomics and zero migration; permanently carries creator-selection ambiguity
  ("a run *is* a workout"), needs a runtime guard + SKILL.md boundary + shared insert helper
  to be safe, and cannot attach cardio stats to sync-owned rows — so it grows the enricher
  later anyway and then carries a redundant tool forever.
- **B — compositional** (one creator + per-shape enrichers): matches the schema
  (`cardio_logs` is already an enrichment table with a NOT NULL FK) and the codebase's
  dominant idiom (the strength flow is already create-elsewhere-then-enrich); the only design
  that covers the Saturday-Endurance gap; two-call fragility on the run hot path is its real
  cost — addressed by return-value chaining and find-or-create enrichers. **Chosen.**
- **C — one fat tool** (creator with optional cardio params): eliminates creator-selection
  errors and the 12-param fear is overblown (`log_running_session` already runs 9 in prod),
  but it is internally inconsistent (runs monolithic, lifts compositional) and structurally
  cannot enrich sync-created rows. Dominated by B.

Shared findings all three surfaced: plan resolution must default through `binding` or streaks
stay broken; the `_running_summary` crash; the auto-link tie-break hazard; tool-count is a
non-differentiator at this scale.

## Appendix B — external research findings

- **wger** (AGPL, Django): 2.3 redesign landed on exactly the thin-session + per-log split;
  `WorkoutLog.assign_session()` finds or auto-creates the parent session — the pattern this
  plan's find-or-create enrichers copy. Targets are snapshotted onto logs at write time (their
  answer to our `plan_target_history`). No streaks, no missed-state, no real cardio model
  (their cardio-support request is an open issue); verdict: take the design, not the backend.
- **FitTrackee** (Python/Flask): one workout model for 27 sports, one creation schema, every
  stat nullable, rows legally exist before their stats arrive; sports are a code-seeded table
  whose rows carry per-sport *behavior* (→ our `session_types`); edits are gated by
  provenance (file-derived stats read-only → our arbox gating); UTC storage with user-tz
  interpretation at one boundary. No strength modeling at all; no goals/streaks.
- **OpenTracks / Endurain**: both split summary row vs detail tables; Endurain's
  `activities` + `activity_sets` + `activity_laps`/`streams` is our exact layout at larger
  scale. OpenTracks stores start time *plus the local UTC offset at recording time* — the
  deferred timezone fix. Neither adoptable (read-only API / oversized stack).
- **Health Connect / HealthKit / Garmin FIT**: all separate "a session existed" from its
  measurements (independent records associated by time / linked samples / session-lap-record
  hierarchy). Idempotency is per-writer (`clientRecordId` / `syncIdentifier` / `external_id`)
  — the generalized key in Slice 1. **No platform dedupes sessions across sources** (Health
  Connect returns both; Strava keeps both deliberately): a watch sync must own its own
  overlap-and-priority heuristic, which is why it's a separate slice. Health Connect's
  planned↔completed session ID pairing validates our scheduled-row-becomes-completed model.
