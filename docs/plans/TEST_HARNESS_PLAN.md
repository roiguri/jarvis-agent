# Test Harness & Cost Reporting

**Issue:** not yet filed (the predecessor doc carried "TBD" for two weeks — file one when this starts).
**Date:** 2026-07-16 · **rescoped 2026-07-30.**
**Companion:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) — this plan builds the instrument
that roadmap is verified with. Three of the four tests below **are** the acceptance criteria for its
WS2, WS4 and WS7.
**Goal:** make context/cost drift *visible* and the roadmap's structural changes *checkable*. Today
there is no test suite, no way to run the agent against a throwaway state tree in CI, and no
regression check on the metric the whole context roadmap optimizes.

**Rescope note (2026-07-30).** This file was `TESTING_AND_FEEDBACK_LOOP_PLAN.md`, and held three
unrelated subjects: a re-measurement of the context roadmap, a heartbeat-cost fix, and this harness.
The first two are now [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) §0 and WS8 respectively.
What remains is the harness — the only part the old title described, and the part that never shipped.

**Non-goals (deliberately deferred):** CD / auto-deploy on merge (the owner does every restart by
choice; the box is behind default-deny on an LXC — a self-hosted runner is real work and blocks
nothing). LLM-judge eval frameworks (exactly one workstream needs a judge — WS3's briefing voice; see
CONTEXT_HANDLING_PLAN §0.5).

---

## Why this exists

`heartbeat.py` returns before recording telemetry, so a gated tick writes no row to `turns.jsonl` —
and *fewer rows is the measurement*. Everything needed to catch WS1's cost regression sat in
`turns.jsonl` the whole time. `observability/usage.py` exists, is correct, and **nothing runs it**.

> A metric regressed ~68% in plain sight for three days while the roadmap said "verified in
> production." The gap was not instrumentation, evals, or isolation. Nothing ever *read the
> instrument* — no test, no CI, no alert, no scheduled report.

Adding more meters would not have helped. A scheduled report and four asserts would have. Full
measurement writeup: [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) §0.

**What this plan does not fix.** It reduces zero tokens by itself. The token wins are WS8 and WS7 in
the roadmap. This plan's value is that those get *chosen on evidence*, and that the next "verified in
production" claim is falsifiable.

---

## Prerequisite — config seam: **SHIPPED (different shape)**

This plan originally specified a `config.py` with `JARVIS_MEMORY_DIR` / `JARVIS_DATA_DIR` env vars
defaulting to today's literals, because paths were frozen literals in 8 modules and no test could
point at a fixture directory.

**That prerequisite is met, via the staging work rather than this plan.** `config.py` now derives
every state path from a single declared `JARVIS_ROOT` (commits `c5ff435`, `1256b93`), which is
stricter than the original design: an undeclared root is a hard error rather than a default, so an
unconfigured checkout refuses to start instead of silently sharing another instance's memory,
database, and bot token. CI already enforces it — `scripts/ci/check_paths.py` asserts no module
hardcodes a prod state path, and runs on every PR.

**Consequence for this plan:** Phase 3's conftest sets **one** env var, not two, and the "8 modules
with frozen literals" work is gone. Don't redo it.

---

## Phase 3 — pytest + fixtures

- [ ] Add `pytest` to `requirements.txt` (none of pytest/mock/coverage is currently present)
- [ ] `tests/fixtures/memory/` — small, stable `SOUL.md`, `USER.md`, `MEMORY.md`, `HEARTBEAT.md`, `daily/`
- [ ] `tests/fixtures/data/` — `logs/`, `heartbeat/state.json`
- [ ] Root `conftest.py` — set `JARVIS_ROOT` at the fixture tree + a dummy `GOOGLE_API_KEY`
      **before** any project import (module-level constants bind at import; conftest top-level runs
      first). Note `config.py` imports nothing from the project precisely so it can be the first
      import in every entrypoint
- [ ] Verify: `pytest` collects and runs with prod dirs provably untouched (stat mtimes before/after)

*Verified feasible:* `import agent` succeeds with a dummy key in 1.2s with no network, and
`build_system_prompt` is directly callable (probed 2026-07-16).

---

## Phase 4 — The tests that matter, in value order

Each of the first three is an acceptance criterion for a roadmap workstream, so landing it *with*
that workstream is cheaper than landing it standalone.

- [ ] **Context-budget ceiling** — assert `len(system_prompt) + len(tool_schemas)` per scope stays
      under a threshold. Catches prompt/schema bloat. Seed at today's measured values (heartbeat ~33k
      chars, user ~21.5k chars) + headroom. → **WS4's criterion.**
- [ ] **Checkpoint weight** — assert the serialized history a tick carries stays bounded. **This is
      the 63%.** Fails today (108,785 bytes). → **WS7's acceptance criterion**; it is what unparking
      WS7 is measured against.
- [ ] **Golden prompt snapshots** — both scopes against fixtures; catches silent content drops in any
      WS2/WS3 reorder. → guards **WS2/WS3.**
- [ ] **Cache-prefix invariant** — `common_prefix(build(), build()) > 0.9 * len`. Mark `xfail` today
      (the clock is line 1, `agent.py:355`, confirmed by probe); it flips green when WS2 lands. →
      **WS2's criterion.**
- [ ] Extend `observability/usage.py` (or `scripts/`) with a skip-rate + per-tick-input report
- [ ] Consider tagging a gated tick in telemetry — today an absent `turns.jsonl` row means either
      "gate skipped" or "service was down", indistinguishable. Hygiene, not a blocker; it belongs
      with the reporting work
- [ ] Decide how the report runs (open question 2 below)

---

## Open questions to settle before coding

1. **Budget thresholds (Phase 4).** Seed at measured + headroom, or at a target? Seeding at current
   values locks in today's bloat as acceptable; seeding at a target means red on day 1.
   **Recommend:** seed at current, ratchet down as WS7 lands.
2. **Cost report cadence (Phase 4).** Heartbeat task, cron, or manual? A heartbeat task costs an LLM
   turn to report on LLM spend, which is a little self-defeating; cron + Telegram push via the Outbox
   may be cleaner.
3. **CI.** Worth adding pytest to `.github/workflows/ci.yml` (which already runs the path-isolation
   and channel-agnosticism guards) once Phase 4 exists? Cheap, no secrets, no deploy, and it makes
   the tests gate merges. Deferred by default — but the marginal cost is now near zero, since the
   workflow and the `JARVIS_ROOT` scratch-tree pattern both already exist.

---

## Files touched

| Phase | Files |
|---|---|
| 3 | **new** `conftest.py`, `tests/fixtures/**`; `requirements.txt` |
| 4 | **new** `tests/test_{context_budget,checkpoint_weight,prompt_assembly,cache_prefix}.py`; `observability/usage.py`, **new** `scripts/cost_report.py`; possibly `.github/workflows/ci.yml` |

---

## Appendix — verification log: the episode that argued for this plan

Kept because it is the case study, not for the fix itself (that work is
[CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) WS8a, now GREEN). Deploys are manual — the owner
restarts — so every "after" reading lands hours or days after the commit. Record them rather than
trusting recall; WS1 was called "verified in production" from memory, and that claim was worth
nothing.

**Re-measure with** `observability.usage.summarize_usage(group_by="scope")` for the token rows; join
`turns.jsonl` ↔ `tool_calls.jsonl` on `turn_id` for per-tool counts (`scripts/trace.py` does the join
for a single turn).

### WS8a — drop the redundant `get_chat_history` instruction

Commit `a8e1b70` · deployed 2026-07-20 16:05 UTC.

- **Failed.** 5 of the 7 post-deploy ticks still called it. LLM calls/tick: 5.4 → 5.0 mean (n=7,
  error turns excluded). Mean input/tick 102.5k → 114.8k — no measurable win.

**The premise was wrong.** The phase assumed the only thing asking for the call was the tick-message
instruction it removed, with in-context imitation as the fallback explanation. Neither was the driver.
`get_chat_history`'s **own docstring** carried `Example: '2026-05-08T00:00:00Z' for today's
conversations only` — bound into every tick via `llm.bind_tools()`, advertising exactly the redundant
use. The model's calls reproduced that example's shape (`since='2026-07-21T00:00:00Z'`, `Z`-suffixed),
not the removed instruction's (`+03:00`, Israel midnight).

Two generalisable costs:

- **Prompt prose was edited to change tool-usage behaviour.** CLAUDE.md already states tool usage is
  driven by docstrings; the phase was scoped without grepping the tool surface.
  `grep -rn "get_chat_history" prompts/ agent.py heartbeat.py` returned nothing — the docstring was
  the *only* remaining driver, and was never in scope.
- The `Z` example was **also a live correctness bug**: days here are Israel time, so a UTC-midnight
  bound silently dropped 00:00–03:00 Israel from every fold-in.

Docstring fix `0191dc4` · deployed 2026-07-21 06:11 UTC. **Necessary but not sufficient** — the
07:11 tick still called it, and the heartbeat checkpoint (read-only via `SqliteSaver`) showed **three
identical `Z`-pattern calls in the 50-message window**. That is in-context imitation: with the
docstring corrected and code/prompts grep-clean, the polluted thread history was the only remaining
driver, and it re-seeded its own window — self-perpetuating, would not self-clear.

**Remedy: reset the heartbeat checkpoint, not a negative prompt.** Cleared the `heartbeat` thread
2026-07-21 07:44 UTC. Durable state (`state.json`, notes files, `scheduled_events.json`) lives outside
the checkpoint and was untouched, as was the telegram user thread. A negative instruction was
deliberately *not* added — it would guess at an unobserved driver, and adding tick-prose to steer a
tool is the anti-pattern this episode already taught.

**Merge decision (2026-07-21).** `fix/heartbeat-cost` merged to `main` before the metric read ~0. Both
commits are strict improvements regardless of the metric (`a8e1b70` removes an instruction; `0191dc4`
also fixes the `Z` correctness bug), so the target was fix-forward, not a merge blocker.

**Outcome — GREEN, 2026-07-28.** 1 call / 36 ticks = **0.03/tick** against a 0.95 baseline (2 calls in
~7 days over ~123 ticks). The negative instruction was never earned, so the "earn it by evidence"
discipline held. `read_memory` 2.36/tick and `write_memory` 1.97/tick — below the 3.1/2.3 baseline but
still the bulk of per-tick round-trips, which is what WS8b/WS8c target.

**The lesson worth carrying:** three explanations were proposed and two were wrong; only the
checkpoint read and the tool-call profile distinguished them. Every step of this took a manual
measurement that a test would have made free — which is the whole argument for Phases 3–4.
