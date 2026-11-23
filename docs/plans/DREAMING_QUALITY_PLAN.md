# Dreaming Quality — Plan

**Date:** 2026-11-22 · **Status:** Phase 1 in progress on `feat/dream-daily`.
**Goal:** make daily memory suggestions high-precision and low-noise ("prefer misses over verbose or incorrect changes"), while keeping `dream` as a standard Jarvis turn (`scope="dream"`) with its normal tool surface.

---

## Commit Checklist

### Branch 1: `feat/dream-daily` (Phase 1)
- [x] **Plan:** `docs(plans): add dreaming quality improvement plan`
- [x] **P1-S1:** `test(evals): add prod-derived suggestion cases and precision grading`
- [x] **P1-S2:** `feat(dream): add homes registry, generic tool trail, media-alert filter, and guards`
- [x] **P1-S3:** `feat(dream): tighten daily prompt for precision and add dream_preferences.md`
- [ ] **Ship Phase 1:** Push `feat/dream-daily` → PR to `main` → CI green → merge → deploy to prod

### Branch 2: `feat/dream-weekly` (Phase 2)
- [ ] **P2-S1:** `feat(dream): add weekly maintenance review over daily logs and memory`
- [ ] **Ship Phase 2:** Push `feat/dream-weekly` → PR to `main` → CI green → merge → deploy to prod

---

## Phases & Slices

### Phase 1 — Daily Dreaming Fixes (`feat/dream-daily`)

- [x] **P1-S1 — Prod-derived evals & precision grading.** Add `max_suggestions` to `evals/grade.py`; add ~10 synthetic, anonymized eval cases in `evals/cases/suggestions/` covering every failure pattern observed in production (bare `"don't apply"` day, mid-chat writes to `HEARTBEAT.md` or memory already saved, media-upgrade alerts, unacknowledged morning briefings, one-off media/shopping over-interpretation, tentative recipe tweaks, short-term weekly schedule variations, stale untouched files ignored on daily, plus repeated explicit preferences and silent days). Record baseline before code changes.
- [x] **P1-S2 — Shared `homes.py` registry + generic tool trail + media-alert filter + guards.** Add `homes.py` (`SKILL.md` `stores:` discovery + optional `@homes.provider` hooks) and wire initial providers; drop non-conversational `notification` / `llm_notification` alerts in `conversation()`; include a tool-agnostic 1-line tool-call trail (args truncated, `-> ok`/`error`, never `tool_result` bodies); fix `MAX_TOKENS` (fail even on partial text in `dream` scope) and `WRAPPED_UP` + `NOTHING` handling.
- [x] **P1-S3 — Tightened daily prompt + `dream_preferences.md`.** Drop the raw 7-day past-notes dump; inject owner-curated `jarvis_memory/dream_preferences.md` in `dream` scope only when non-empty (+ one line in `prompts/AGENTS.md` for chat feedback updates); apply the precision-first redesign to `prompts/dream.md`; update `docs/architecture/MEMORY.md` and `evals/baseline.json`.

### Phase 2 — Weekly Maintenance Pass (`feat/dream-weekly`, after Phase 1 settles in prod)

- [ ] **P2-S1 — Weekly maintenance review & evals.** Add `prompts/dream_weekly.md` reading the last 7 `daily/daily_YYYY-MM-DD.md` logs, all memory files (with last-changed dates), `homes.summarize()`, and `dream_preferences.md` (if non-empty); wire Sunday schedule in `triggers/scheduler.py` and `/dream week` in `gateway/commands/handlers.py`; add weekly eval cases (completed trip archival, duplicate files, `USER.md`/`MEMORY.md` bloat, multi-day patterns).

---

## 1. Failure Patterns Observed in Production

An audit of 30 days of production dream runs (29 runs, 30 LLM calls, only 3 tool calls total, 3 timeouts, ~95% of output tokens spent on thinking) surfaced six concrete failure modes:

1. **The `"don't apply"` echo loop:** On days where the owner's only message was rejecting the morning note (`"don't apply"`), feeding 7 days of raw past suggestion notes into context primed the model to echo the previous day's entire batch almost verbatim — including repeatedly proposing meta-rules in `USER.md` about how memory suggestions should behave.
2. **Blind to mid-chat writes into other homes:** When the owner corrected a recurring gym schedule rule in chat and Jarvis immediately updated `HEARTBEAT.md` via `manage_heartbeat_task`, stripping `tool_call` rows left dreaming unaware that the rule was already saved, so it suggested duplicating it into `USER.md`.
3. **Mining media alerts & unacknowledged `[jarvis-sent]` sends:** One-way media download/upgrade alerts (`event="notification"`) and unacknowledged morning briefings were mined repeatedly as "project status updates" or "facts about the owner."
4. **Over-interpreting one-off requests into durable facts:** Single movie-tracking requests became "project use cases," one-off household shopping questions became new profile sections, and a busy single week of bookings became standing schedule rewrites.
5. **Promoting tentative one-off tweaks to standing rules:** `prompts/dream.md` currently says *"If you can't tell, suggest it and say it may be one-off"*, which turns tentative single-task requests (e.g. formatting one recipe) into permanent rules.
6. **Daily nagging about stale untouched files:** Inviting daily checks on untouched files and `MEMORY.md` organization caused the same untouched personal log or completed past trip to be flagged repeatedly on unrelated days.

---

## 2. Target Architecture

### Daily vs. Weekly separation

| Dimension | Phase 1 — Daily pass | Phase 2 — Weekly pass (Sundays) |
|---|---|---|
| **Job** | Capture yesterday's delta with high precision | Tidy memory & spot cross-day patterns |
| **Prompt** | `prompts/dream.md` (redesigned) | `prompts/dream_weekly.md` (new) |
| **Primary input** | Yesterday's transcript + generic 1-line tool trail + memory files + `homes.summarize()` + `dream_preferences.md` (if non-empty) | Last 7 `daily/` logs + all memory files (with last-changed dates) + `homes.summarize()` + `dream_preferences.md` (if non-empty) |
| **Looks for** | 1. Lasting corrections & preferences<br>2. Durable new facts<br>3. Direct contradictions from yesterday's chat | 1. Out-of-date plans / stale untouched files<br>2. Duplicates across files & misfiled content<br>3. `USER.md` (>4,000) / `MEMORY.md` (>8,000) bloat<br>4. Multi-day patterns across the week |
| **Explicitly excluded** | `Guesses`, `Organization` audits, untouched-file staleness checks, media alerts, meta-rules about dreaming, one-off tasks, items already saved in another home | Re-reading raw daily chat transcripts |
| **Cap & default** | Max 3 items; prefer misses over noise (`Nothing to suggest.`) | Max 5 items; default to `"Nothing to suggest."` |

### Core principles

- **No fragile heuristics:** No arbitrary reply time windows, no bare-reply regexes, no hardcoded tool-name filter lists, and no structured-output code validators.
- **Keep `dream` on the normal Jarvis turn surface:** No throwaway read-only isolation; keep `agent.ask_jarvis(..., scope="dream")` intact.

---

## 3. Component Design

### A. Shared "Other Homes" Interface (`homes.py`)

Mirrors `files/references.py` (`@references.holder`) and `tools/registry.py` (`@tool_register`) so `memory_suggestions.py` never imports any skill's DB directly and new skills work out of the box:

1. **Zero-code baseline via `SKILL.md` frontmatter:**
   Any skill that persists owner data adds one optional `stores:` line to its `SKILL.md` YAML frontmatter. `tools/registry.py` (`_skill_meta` → `compact_skill_list`) reads only `description` and the body, so `stores:` is **never** passed into normal `user` or `heartbeat` system prompts (verified by `tests/test_prompt_snapshot.py`).
2. **Optional live summary hook (`@homes.provider`):**
   Subsystems with dynamic entity names register a zero-arg read-only function next to their own store (imported at startup via `tools.registry.import_all()` or core imports). `homes.summarize()` merges `SKILL.md` `stores:` entries with registered providers, calls each provider fresh inside `try/except`, **fails open** to the static description if a DB is absent or errors, and caps each home at max 20 labels / 300 chars.

| Skill / Store | Persistent home? | Mode | Exposed to `dream` |
|---|---|---|---|
| `collections` | Yes | `@homes.provider` | Active (non-archived) collection names from `collections.sqlite` |
| `travel` | Yes | `@homes.provider` | Draft/current trip titles + dates and saved destination names from `travel.sqlite` |
| `heartbeat` | Yes | `@homes.provider` | Task names from `heartbeat_state.parse_tasks()` |
| `triggers` | Yes | `@homes.provider` | Count of pending one-off reminders & wakes from `triggers.store.all_triggers()` |
| `cookbook` | Yes | `SKILL.md` `stores:` | Static: family recipes (ingredients, steps, source URLs, photos) |
| `fitness` | Yes | `SKILL.md` `stores:` | Static: gym class bookings, workout logs, PBs & running sessions |
| `files` | Yes | `SKILL.md` `stores:` | Static: stored photos, voice notes, videos & documents |
| `media` | Yes | `SKILL.md` `stores:` | Static: tracked movies & TV series (Radarr/Sonarr/Jellyseerr) |
| `web`, `google_health`, `github` | No | — | Omitted (stateless queries / dev issue tracker) |

### B. Transcript Building (`memory_suggestions.py`)

1. **Structural `[jarvis-sent]` filter:** Drop `event in ("notification", "llm_notification")` (one-way media broadcast alerts and past `memory_suggestions` notes). Keep `heartbeat` and `reminder` sends intact; no time-window slicing or reply-text regexes.
2. **Tool-agnostic 1-line tool trail:** Include every `tool_call` row from `compaction._span_rows(...)` (never `tool_result` bodies), formatted generically by truncating long string arg values (~80 chars per arg, ~140 chars total) and appending `-> ok` / `-> error` by matching `call_id`. Any newly added tool works automatically with zero hardcoded tool lists.

### C. Prompt Redesign (`prompts/dream.md`)

Keep `_DREAM_FRAMING` in `agent.py` untouched so calibration lives in one place:

1. **Flip *"if you can't tell, suggest it"* (`dream.md:11`)** → *"When in doubt, leave it out — prefer missing a borderline item over suggesting something noisy, speculative, or one-off. If it truly matters, it will come up again."*
2. **Raise the bar on incidental facts (`dream.md:12`):** Only facts that will materially change how Jarvis helps weeks from now; exclude mundane possessions, one-off purchases, single requests, and short-term weekly schedule variations.
3. **Narrow `Out of date` (`dream.md:13`):** Direct contradictions from yesterday's conversation only (move untouched-file checks to Phase 2 Weekly).
4. **Drop `Organization` and `Guesses` sections from daily (`dream.md:14,25-26`):** Keep 3 sections (`Corrections & preferences`, `Facts`, `Out of date`); lower cap from 7 to **3**.
5. **Expand `Not worth suggesting` (`dream.md:16`):** Explicitly exclude actions already handled via `[tool]` calls (memory writes or other homes), unacknowledged `[jarvis-sent]` messages, system/integration operational status updates, and meta-rules about dreaming / `"don't apply"` rejections.
6. **Concise, surgical edits (`dream.md:28`):** Match target file style, avoid inventing new sections for single minor facts, date facts only when genuinely time-sensitive, and keep `Why:` to one crisp line with an exact `[owner]` quote.
7. **Drop the 7-day past-notes rule (`dream.md:18`):** Remove the raw past-notes dump and paragraph entirely.

### D. Feedback Loop (`jarvis_memory/dream_preferences.md`)

- **Starts empty (created lazily):** Absent on day 1.
- **Conditional injection:** `build_instruction()` injects `dream_preferences.md` (along with its header *"follow these; never suggest editing this file"*) **only when the file is non-empty**. Excluded from `_memory_files()` and never injected into `user` or `heartbeat` scopes.
- **Updated organically:** One line in `prompts/AGENTS.md` directs live Jarvis to update `dream_preferences.md` via `write_memory` whenever the owner gives explicit feedback on what memory suggestions should or shouldn't include; the owner can also edit it directly.

### E. Reliability Guards

- **`MAX_TOKENS` guard (`agent.py`):** Treat `finish_reason == "MAX_TOKENS"` as `FAILED` in `dream` scope even when partial text was emitted (never deliver a truncated note).
- **`WRAPPED_UP` + `NOTHING` check (`memory_suggestions.py`):** Strip `turn_budget.WRAPPED_UP_LINE` before comparing against `"Nothing to suggest."`.

---

## 4. Verification & Git Workflow

- All edits happen in `/app/jarvis_staging/code`; `/app/jarvis_code` is deploy-only via `./deploy/deploy.sh`.
- One feature branch per phase (`feat/dream-daily`, then `feat/dream-weekly`), one commit per slice.
- Every commit requires: (1) `venv/bin/python -m pytest` passing (+ `evals.run --case 'suggestions/*'` where applicable), (2) manual staging verification, and (3) explicit per-commit approval. Push, PR, and prod deploy each require explicit approval.
