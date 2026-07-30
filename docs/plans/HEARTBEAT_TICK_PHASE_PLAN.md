# Heartbeat Tick Phase — Pin the Lattice

**Issue:** [#37](https://github.com/roiguri/jarvis/issues/37) (bug, priority: high)
**Date:** 2026-07-30 · **rescoped 2026-07-30 after independent review** (was 5 slices, now 2)
**Status:** plan only — nothing implemented.

---

## Executive summary

Heartbeat ticks are registered with `IntervalTrigger(hours=1)` and no `start_date`
(`main.py:234-243`), so APScheduler anchors the first fire at *scheduler start + 1h*. Every restart
re-phases the whole schedule to whatever minute the process happened to boot at — four distinct
phases in the last three days. Because the due-gate asks "has 24h *elapsed* since the last run?"
with only 60s of slack (`heartbeat_state.py:326`), a phase that moves **earlier** makes a daily task
read short at every tick its window has left, and the task is skipped for the day. Silently: no
error, no warning, no log line.

**The fix is two changes, ~15 lines, one commit:** pin ticks to the top of the hour with a cron
trigger, and make the gate's cadence comparison lattice-aware so it cannot read short by a few
minutes. Everything else that was in this plan — a warning log, authoring-time window validation,
and the #32 scheduler refactor — is cut or deferred; none of it fixes the bug, and one of them was
dead code as specified.

**Scope of the bug is narrower than #37 states.** Only two tasks are genuinely exposed, and one of
them is not the one the issue names. See [What is actually exposed](#what-is-actually-exposed).

**This is a patch on a deeper mismatch**, and the plan says so rather than claiming a cure: an
*elapsed-time* cadence is being used to gate a *wall-clock-recurring* window. The durable fix is to
compare window **occurrences** instead of elapsed hours — named as the successor in
[Deferred](#deferred--follow-up-work), deliberately not built here.

---

## Checklist

**Implement** — one commit, both changes together (S1 alone regresses once on deploy day; see S2).

- [ ] `main.py:9` — swap the `IntervalTrigger` import for `CronTrigger`.
- [ ] `main.py:234-243` — register the tick with `CronTrigger(hour="*/1", minute=0, timezone=utc)`;
      `misfire_grace_time` 3600 → 60.
- [ ] `main.py` — move `HEARTBEAT_INTERVAL_HOURS` out to `heartbeat_state.TICK_INTERVAL_HOURS`
      (the gate owns the lattice), and document there that only divisors of 24 are valid.
- [ ] `main.py:281` — the `"Heartbeat interval: %dh"` boot line reads the moved constant; log the
      job's next fire time alongside it so the phase is visible in the boot block.
- [ ] `heartbeat_state.py` — add `TICK_INTERVAL_HOURS` and a `_floor_to_tick()` helper.
- [ ] `heartbeat_state.py:326` — permissive floored comparison (**raw OR floored**, never floored
      alone — see the M1 trap in S2).
- [ ] `heartbeat_state.py:39-44` — leave `_CADENCE_GRACE` at 60s; update the comment to describe
      what it now absorbs (bounded misfire lateness, not tick jitter).

**Verify** — by hand, against `any_due` directly. Nothing committed.

- [ ] Work the 9 cases in [Verification](#verification) as direct `any_due(...)` calls with an
      injected clock and throwaway fixture files, including the **M1 regression**
      (`prev=06:00:00, now=06:59:59, every 1h` → must be due).
- [ ] Paste the results into the PR as evidence.
- [ ] Staging: wiring check only — do ticks land at `:00`? Read the hazards in Deployment first.
- [ ] Prod after restart: `journalctl -u jarvis | grep "due tasks"` shows `HH:00:0x`, **and still
      does after the next restart** — that persistence is the real acceptance criterion.

**Deploy**

- [ ] `./deploy/backup_state.sh` before deploying.
- [ ] `./deploy/deploy.sh` in the prod checkout (never restarts; fails closed).
- [ ] **The owner restarts** — Claude cannot restart either service and must not infer its state.
- [ ] No `state.json` edit needed in either environment (S2 absorbs the off-lattice stamps).
- [ ] Revert any temporary `JARVIS_HEARTBEAT_ENABLED=true` line from
      `deploy/jarvis-staging.service` — it is a committed file.
- [ ] Confirm tomorrow's `morning-readiness-check` fires at the 06:00Z tick = **09:00 Israel**.

**Docs**

- [ ] `docs/architecture/HEARTBEAT.md:3` — replace the `IntervalTrigger(hours=1)` reference; state
      that the phase is fixed and restart-independent.
- [ ] `docs/architecture/HEARTBEAT.md` gate-semantics section — document the floored comparison and
      the DST residual it does *not* cover.
- [ ] `CLAUDE.md` heartbeat section — "every hour" → "on the hour".

**File as follow-ups** (see [Deferred](#deferred--follow-up-work))

- [ ] Occurrence-quantized gate (the successor design).
- [ ] Last-tick-in-window warning (the redesigned S3).
- [ ] Unreachable-window rejection in `manage_heartbeat_task` (the old S4).
- [ ] #32 stays open, unbundled.

---

## Why this exists

Phase movement over three days (`journalctl -u jarvis`):

```
:16:52   → until Jul 29 05:22
:22:41   → until Jul 30 07:35
:35:46   → until Jul 30 11:59
:59:26   ← phase at the time of writing
```

### The failure condition, precisely

> An `every 24h` task is skipped for a day when the tick phase moves **earlier** by more than
> `_CADENCE_GRACE` (60s), **and** the task's stamp came from the **last** tick inside its window.

Three clarifications that change what is and is not exposed:

- **Time of day is irrelevant; only minute-of-hour matters.** A restart re-phases to its own minute
  within the hour, and what matters is how that compares to the minute the stamp carries.
- **For `HH:MM-HH:MM` windows, phase does not change the tick count** — a 2h window holds two
  hourly ticks at every phase. The skip is not "fewer ticks"; it is the cadence comparison reading
  short at every tick the window still has. (This does *not* hold for `±radius` windows, which are
  closed at both ends — `Sat 09:00±2h` gets 5 ticks at phase `:00` and 4 at `:30`.)
- **The stamp position self-perpetuates.** A stamp at 06:xx makes the next day's 05:xx tick read
  23h — not due — so the task re-stamps at 06:xx forever, permanently occupying the exposed slot.

### It is armed right now

`morning-readiness-check` (`every 24h | due: 08:00-10:00` Israel = 05:00–07:00 UTC) is stamped
`2026-07-30T06:22:41Z` — the second of its two in-window ticks. Next-day outcome by phase
(threshold `24h - 60s`):

| new phase | in-window tick | elapsed | result |
|-----------|----------------|---------|--------|
| `:00` – `:21` | 06:xxZ | 23h37m – 23h58m | **skipped** |
| `:22` | 06:22Z | 23h59m | fires 09:22 Israel |
| `:30` | 06:30Z | 24h07m | fires 09:30 Israel |
| `:59` | 06:59Z | 24h36m | fires 09:59 Israel |

A restart landing in the **first ~22 minutes of any clock hour** (37.8% of restart moments) costs
tomorrow's Daily Readiness. The 07:xx tick is 10:xx Israel and the window is `[08:00, 10:00)`, so
there is no third chance. With no restart at all, the current `:59` phase fires at 09:59:26 Israel
— 34 seconds before the window closes.

### Two facts that shape the fix

1. **Stamping already uses the tick's start time** — `heartbeat.py:52` reads `now_utc` and
   `heartbeat.py:168-170` passes that same value to `stamp`. Turn duration causes no drift, which is
   why pinning the phase is sufficient: on a fixed lattice, elapsed is *exactly* one cadence at the
   same tick next day.
2. **`misfire_grace_time=3600` is inert today.** `AsyncIOScheduler` uses an in-memory jobstore
   (apscheduler 3.11.3), so nothing survives a restart to be "missed" — misfire applies only to a
   running scheduler whose loop was blocked past the fire time. Under a fixed lattice, an hour of
   grace would actively regress things by permitting off-lattice ticks.

---

## What is actually exposed

#37 names `weekly-attendance-sync` and `weekly-fitness-scouting` as same-exposure weekly tasks.
**That is wrong** — verified against live state. Both are `every 24h` with a *day-restricted*
window, so elapsed at the next window opening is ~168h against a 24h threshold: they clear it by two
orders of magnitude and fire at the first in-window tick at any phase. Same for
`running-evening-prep`.

| Task | Cadence · window | Exposed? |
|------|------------------|----------|
| `morning-readiness-check` | `24h · 08:00-10:00` | **Yes — armed now.** Stamped at the last in-window tick. |
| `reading-list-suggestion` | `7d · Sat 09:00±2h` | **Yes — structurally.** Cadence period equals window recurrence, so it self-perpetuates onto the last in-window tick and a phase move costs a full week. Never stamped, so not yet armed. |
| `memory-index-audit` | `7d · 10:00-18:00` | Structurally, but an 8h window gives many ticks of slack. |
| `weekly-attendance-sync` | `24h · sun 08:00-11:00` | No — 168h ≫ 24h. |
| `weekly-fitness-scouting` | `24h · thu 19:00-21:00` | No — same. |
| `running-evening-prep` | `24h · mon,fri 19:00-21:00` | No — same. |
| `crossfit-sync-and-remind` | `1h · 06:00-22:00` | No — hourly cadence, 16h window. |
| `running-post-check` | `1h · 20:30-23:30` | No — same. |

`reading-list-suggestion` is the genuinely-weekly exposure the issue was reaching for and never
names. Correct #37 when this ships.

---

## The fix

### S1 — Pin the phase

**Files:** `main.py:9`, `main.py:234-243`, `main.py:281`.

```python
CronTrigger(hour=f"*/{heartbeat_state.TICK_INTERVAL_HOURS}", minute=0, timezone=timezone.utc)
```

- **UTC, not `Asia/Jerusalem`.** Israel is a whole-hour offset, so top-of-hour is the same instant
  either way — but UTC yields exactly 24 ticks every day, where a local cron drops an hour on
  spring-forward and repeats one on fall-back.
- **`hour="*/N"` only generalizes for divisors of 24.** Verified: `*/5` fires at 00,05,10,15,20 and
  then jumps 4h across midnight. Harmless at N=1, but since the constant is being promoted
  specifically so the trigger and the floor cannot drift, that constraint belongs in a comment at
  the constant.
- **`misfire_grace_time` 3600 → 60**, not 300. Rationale under S2: bounding lateness by the same
  60s as `_CADENCE_GRACE` gives one clean invariant — *any off-lattice deviation is ≤ the grace* —
  and closes a near-duplicate-run edge that 300s leaves open.

### S2 — Make the gate lattice-aware

**Files:** `heartbeat_state.py:39-44`, `heartbeat_state.py:326`.

```python
floored = _floor_to_tick(now) - _floor_to_tick(prev)
cadence_due = (now - prev >= t.cadence - _CADENCE_GRACE) or (floored >= t.cadence - _CADENCE_GRACE)
```

**The `or` is load-bearing — flooring alone is a regression.** `floor(now) - floor(prev)` can be
*smaller* than the raw difference whenever `prev`'s minute is below `now`'s. Verified:
`prev=06:00:00, now=06:59:59, every 1h` → raw 59m59s is due today, floored is **0** and would not
be. `crossfit-sync-and-remind` would silently lose that hour. The invariant to hold is: **flooring
may only make a task due earlier, never later.**

Floor at **comparison time only, never at write time** — `state.json` stays a truthful record of
when ticks actually ran, which is what makes it auditable.

**Why ship it, given S1 alone nearly suffices.** S1 on its own costs exactly one missed morning
message: the pre-deploy stamp sits at some arbitrary phase, so the first `:00`-lattice tick reads
short. But the stamp is not advanced, so the next day it reads 46h+, fires at the *first* in-window
tick, and stabilizes at 08:00 Israel — a better steady state than today's 09:59. Self-healing in
24h. So S2 is not strictly required. It earns its place for two other reasons:

1. **It closes the misfire hole S1 opens.** A tick permitted to run late stamps off-lattice, and
   the 60s grace cannot absorb a 5-minute deviation — the next day's on-lattice tick reads short
   and the skip is back, permanently. Flooring makes `floor(06:04) == floor(06:00)`, so it is
   absorbed. (With `misfire_grace_time=60` the raw comparison also survives on its own; the two
   together are belt and braces.)
2. **It removes a manual deploy step** — no `state.json` edit, in either environment, ever.

**`_CADENCE_GRACE` stays at 60s.** An earlier draft proposed widening it to 30 min as defence in
depth. With the permissive floored form it buys nothing, and it would contradict the constant's own
comment ("must stay well under the smallest supported cadence").

---

## Verification

`any_due` already takes `now`, `heartbeat_path` and `state_path` as parameters
(`heartbeat_state.py:282-287`), so the whole matrix can be exercised by **calling it directly** with
an injected clock and a throwaway fixture dir — no service, no LLM call, no Telegram send, no real
state. The bug is "wrong answer at one specific timestamp", and this is the only way to see
tomorrow's answer today.

Run by hand during development; paste the results into the PR. **Deliberately not committed** as a
test or a CI guard: the two scripts in `scripts/ci/` assert architectural invariants that would
otherwise rot silently (hardcoded state paths, channel coupling), and a one-off correctness proof
for a bug fix is not that. There is no `tests/` dir and no pytest in `requirements.txt`; this fix is
not the occasion to introduce either.

```
fixture HEARTBEAT.md:  - **t** | every 24h | due: 08:00-10:00 | notes: `x.md`
fixture state.json:    {"last_run": {"t": "<prev>"}}
call:                  any_due(<now>, heartbeat_path=…, state_path=…)  → (bool, [names])
```

| # | Case | Expect |
|---|------|--------|
| 1 | Stamp `06:22Z`, tick `06:00Z` next day (the live changeover case) | due |
| 2 | Stamp `06:59Z`, tick `06:00Z` next day (worst-case changeover) | due |
| 3 | Both on-lattice, exactly one cadence apart | due |
| 4 | Both on-lattice, one hour short of cadence | **not** due — no early firing |
| 5 | **M1 regression:** `prev=06:00:00, now=06:59:59, every 1h` | **due** — flooring must not delay |
| 6 | `every 1h`, consecutive on-lattice ticks | due every tick — unchanged |
| 7 | Off-lattice stamp `06:04Z` (misfire), tick `06:00Z` next day | due |
| 8 | Window closed, cadence elapsed | not due |
| 9 | Empty / unreadable HEARTBEAT.md, unparseable cadence, bad stamp | fail open — unchanged |

Cases 4 and 5 are the two regression guards; case 2 is the one that justifies S2 over a hand-edit.

---

## Deployment

### Prod (`/app/jarvis_code`, `JARVIS_ROOT=/app`)

Steps are in the checklist. Two notes:

- **No `state.json` migration.** S2 absorbs the off-lattice stamps already in the file. If S2 were
  ever dropped, the fallback is a single hand-edit of `morning-readiness-check`'s stamp down to the
  hour, with the service stopped — flooring never delays a task, so it is safe — and even skipping
  that costs one morning message.
- **`due:` window audit — done, all 8 tasks reachable** on a top-of-hour lattice, so nothing
  existing breaks: `08:00-10:00`, `06:00-22:00`, `thu 19:00-21:00`, `mon,fri 19:00-21:00`,
  `20:30-23:30`, `sun 08:00-11:00`, `10:00-18:00`, `Sat 09:00±2h`.

### Staging (`/app/jarvis_staging`, `JARVIS_ROOT=/app/jarvis_staging`)

Staging runs with the heartbeat **disabled** — `deploy/jarvis-staging.service` sets no
`JARVIS_HEARTBEAT_ENABLED` and `config.py:87` defaults it to `False`. Enabling it for a live tick
has three hazards:

1. **`deploy/jarvis-staging.service` is committed.** A test-only `Environment=` line must be
   reverted before the PR, or staging's inert-by-default posture ships changed.
2. **Staging state is a week stale** (`jarvis_data/heartbeat/state.json`, last written 2026-07-23).
   Enabling the heartbeat makes every task instantly due: one large multi-task LLM turn and a burst
   of real Telegram messages to the staging bot. Reset the stamps to "now" before starting, or trim
   staging's `HEARTBEAT.md` to a single harmless task.
3. **Staging's `HEARTBEAT.md` is its own file** (2026-07-20) and lacks `reading-list-suggestion`.
   Do not assume the two environments match.

Given all three: prove correctness offline, and use staging only for the narrow "do ticks land at
`:00`" wiring check.

### Restart timing

Once this ships, restart timing stops mattering — that is the point. **Before** it ships, a restart
falling in the first ~22 minutes of a clock hour costs tomorrow's Daily Readiness. Deploying cures
the pending exposure rather than causing it.

---

## Deferred / follow-up work

Named here so the boundaries of this fix are explicit. None of it belongs in this PR.

**Occurrence-quantized gate — the successor design.** The real defect is that an *elapsed-time*
cadence gates a *wall-clock-recurring* window; tick flooring is a narrow patch on that. The general
form is: due iff the window is open **and**
`occurrence_start(now) - occurrence_start(last_run) >= cadence - grace`, compared in Israel local
days. `DueWindow.is_open` already computes the occurrence start in its `day_offset` loop
(`heartbeat_state.py:83-99`); returning it instead of a bool is ~10 lines. That version is immune to
phase, restarts, misfires, off-lattice stamps **and** DST, and needs no migration. Two traps: a
naive `last_run < occurrence_start` test makes `memory-index-audit` (`every 7 days`, *daily*
window) fire every day — the occurrence-delta form above avoids it; and it moves daily tasks to the
first in-window tick, which is user-visible.

**DST residual — not fixed by this plan.** Windows are evaluated in Israel time
(`heartbeat_state.py:80`) while the cadence is absolute elapsed hours, so on spring-forward a window
shifts an hour earlier in UTC and a task pinned to its last in-window tick reads 23h at every tick
of the shifted window and is skipped. Once a year, self-heals next day. Flooring does not help; the
occurrence-quantized gate does. An earlier draft of this plan claimed DST immunity it does not have.

**Last-tick-in-window warning** (was S3, cut). The original predicate — "in-window but cadence-short
by *less* than one tick interval" — is dead code after S2, since every floored shortfall is an exact
multiple of an hour. Loosening it to `<=` fires daily on a task that still has another tick coming,
i.e. not a smoking gun. The real signal needs the window *end*: "this is the last in-window tick and
the task is still short." Worth building on top of the occurrence work, where the occurrence end is
already in hand.

**Unreachable-window rejection** (was S4). `due: 08:10-08:50` parses, validates, and can never fire.
Once ticks are pinned this is decidable at authoring time (`tools/core/heartbeat.py:256`). Write-path
only, so nothing existing breaks. Two subtleties for whoever picks it up: for a half-open range,
`duration >= 1h` already implies a top-of-hour falls inside, so the test reduces to "does a boundary
fall in the span"; and the two window forms need different tests, because `is_open` is half-open for
ranges (`heartbeat_state.py:98`) but **closed** for `±radius` (`heartbeat_state.py:89`).

**#32 — scheduler module split.** Stays open and unbundled. Attaching a whole-file mechanical move
to a priority-high bug fix works against the clean-revert property that motivated slicing in the
first place; the shared surface is two lines of `add_job`. When it is done, note the import-site
list in #32 is incomplete: `tools/core/scheduling.py` calls `get_scheduler()` at both `:116` and
`:159`.

---

## Open decisions

1. **`misfire_grace_time`: 60s (recommended) or 300s?** It is inert today either way; it only
   matters if the event loop stalls past `:00` (the agent turn itself is capped at 90s,
   `heartbeat.py:90`). 60s gives the invariant *off-lattice deviation ≤ `_CADENCE_GRACE`*, so the
   raw comparison never needs the floor to rescue it. It also closes an edge 300s leaves open: with
   the permissive floored form, a stamp at `06:59` (from a late tick) and a tick at `07:00` floor to
   a full hour apart, so an `every 1h` task could run twice a minute apart. Bounded at 60s that
   cannot happen. Cost of 60s: a stall between 60s and 300s loses the tick entirely — acceptable,
   since the gate is stateful and the task simply comes due next hour.
2. **Trigger timezone: UTC (recommended) or `Asia/Jerusalem`?** Identical top-of-hour behavior; UTC
   gives a constant 24 ticks/day across DST transitions.
