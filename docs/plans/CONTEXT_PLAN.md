# Context — one owner conversation, and the cross-scope bridge

**Status:** planned, not started. Phase 0 first; phases 1a–1d are the committed scope.
**Date:** 2026-08-25.
**Problem record:** [context/PROBLEMS.md](context/PROBLEMS.md) — this plan solves C5 (issue #50)
and touches C1/A-cluster edges; it deliberately does not restate the problems.
**Evidence:** [context/REFERENCE_ARCHITECTURES.md](context/REFERENCE_ARCHITECTURES.md) (source-level
deep dives of OpenClaw and hermes-agent, 2026-08-25). The mechanisms below are the two systems'
convergence points, not inventions.

---

## Goal

Jarvis's conversations stop feeling glitchy across boundaries. Concretely, after phase 1:

- A conversation started in the app continues on Telegram (and back) with full context — one
  owner conversation, not one per channel.
- A reply to a heartbeat briefing lands with its antecedent in the thread — the briefing is real
  history, not a truncated system-prompt line that vanishes at midnight.
- Silent background work surfaces once, in the next user turn, then stops costing anything.

Phases 2–4 (memory pressure, history search, prompt stability) are important but are **not the
source** of the felt problem; they are sequenced after and recorded at the end so they don't rot
unfiled.

---

## Decisions carried in from discussion

- **One owner conversation (the spine), phase 1b.** Per-channel threads are an implementation
  artifact for a single-owner assistant. OpenClaw's single-user default is DM-collapse for exactly
  this UX; hermes is the per-channel-lane counterexample and their top pain (fragmented sessions,
  amnesia bugs patched one session-key lane at a time, an 89%-waste replay incident) is the direct
  consequence. Owner-approved as a product change, not just plumbing.
- **Mirror, don't inject.** Both reference systems converged on the same C5 answer from opposite
  poles: run background work isolated; at delivery, write the *delivered text* — only the
  delivered text, never the background run's transcript — into the conversation the user will
  reply in. Neither uses system-prompt slices for this. Provenance goes **in the message text**
  (`[Heartbeat] …`) — hermes's hard-won rule, because metadata does not survive storage
  boundaries.
- **The spine ships before the mirror.** It dissolves the mirror's only open question (which
  thread receives the send) and delivers the cross-channel half of the fix on its own.
- **Fresh checkpoint, no key migration.** The window is 50 rolling messages and thread resets are
  a known-safe operation (PROBLEMS.md F2; durable state lives outside the checkpoint;
  `chat_history.jsonl` keeps the record). The unified thread starts empty.
- **Origin routing moves off the thread prefix.** `get_confirmation()` / `origin_channel()`
  currently derive the turn's channel by parsing `telegram_…` off `CURRENT_THREAD_ID`; with one
  thread that breaks, so routers set an explicit `CURRENT_CHANNEL` context var. This lands first
  (1a) as a no-behavior-change refactor.
- **Every phase is bracketed by readings** (phase 0), per the E-cluster's record: two prior
  context claims were wrong because nobody read the instrument. No phase's summary may claim a
  cost outcome without the before/after numbers in this file.

---

## Phase 0 — the instrument (preliminary)

One script, `scripts/context_report.py`, printing from data that already exists (recipes in
[context/RESEARCH.md](context/RESEARCH.md) §2):

- input tokens/turn and turns/day, per scope (`turns.jsonl`)
- cache-hit ratio per scope (`turns.jsonl` cached-token fields)
- checkpoint weight per thread (`threads.sqlite` row bytes)
- system-prompt section sizes (`build_system_prompt` called directly, both scopes)

Run once → baseline table below. Re-run at the end of each phase; the deltas are the verification.

Baseline taken 2026-08-25 (`JARVIS_ROOT=/app … --days 7` — **prod data, read-only**; staging's
traffic is dev noise). One known-unparseable turns.jsonl line reported and skipped.

| Reading | Baseline 2026-08-25 | After 1b | After 1d |
|---|---|---|---|
| user input/turn | 83,203 (53 turns; 2.55 llm calls/turn) | | |
| heartbeat input/turn | 67,561 (110 turns; 3.80 llm calls/turn) | | |
| user cache ratio | 36.7% (heartbeat: 41.8%) | | |
| checkpoint bytes, owner threads | telegram 48,106 + jarvis-app 67,590 (heartbeat thread: 51,241) | one thread: | |
| user prompt total (chars) | 7,831 at 05:48 UTC — before any notification slice; the slice varies 0–~5k with time of day, so compare same-time runs | | (slice retired) |

Context for the trend: user input/turn was 46.7k at the 07-16 baseline and 78.6k at 08-06
(PROBLEMS.md §0) — still climbing; the user scope is where window weight lands.

## Phase 1 — one conversation, with the background in it

**1a — origin plumbing.** Routers set `CURRENT_CHANNEL` in `turn_context`; `get_confirmation()`
and `origin_channel()` read it instead of parsing the thread prefix. No behavior change; ships
alone; verified by the existing confirmation walkthrough + block harness.

**1b — the spine.** Both channel routers stamp the same owner thread id; fresh checkpoint; reply
routing stays per-router (each replies on its own channel, unchanged). `chat_history.jsonl` rows
carry the new thread id; readers that filter by prefix are updated. The heartbeat's chat slice is
unaffected in intent (it already reads all non-heartbeat threads). **Felt test:** start a topic in
the app, continue it on Telegram, and back.

**1c — the mirror.** When the Outbox successfully delivers an owner-addressed proactive send
(heartbeat, reminder), append the delivered text into the owner thread's LangGraph state and
`chat_history.jsonl` — after send success only (matching the Outbox's log-on-success seam),
idempotent, provenance-prefixed in the text. Delivered text only; the tick's tool calls and
reasoning stay in the heartbeat thread. **Felt test:** reply to a briefing with a bare pronoun
("book the second one") and watch it resolve.

**1d — carryover, then retire the slice.** Two halves that must land together:

1. *Silent-outcome carryover:* persist each tick's structured ack (`heartbeat_respond` already
   produces it) into a one-row, replace-semantics store; the next user turn claims it exactly
   once and receives it as a labeled internal-context line. Covers ticks that acted without
   sending anything — the case the mirror cannot.
2. *Retire `_load_recent_heartbeat_notifications`:* with sends in the thread as real history, the
   flat slice (240-char truncation, today-only) is a second worse copy — delete it. This is also
   the cost payback for the mirror's window usage; the phase 0 delta is the check.

Close #50 at the end of 1d.

**Blast radius note (1b):** the spine is the one step with product-visible behavior change and the
one that touches the checkpoint key. It is deliberately its own PR with its own staging soak
before 1c builds on it.

---

## Sequenced after (filed as problems, designs open)

Phases 2–4 are filed so the work doesn't rot untracked (the G-cluster lesson), but **their design
decisions are deliberately not made here** — each opens with its own discussion when its turn
comes, with REFERENCE_ARCHITECTURES.md §7's candidate mechanisms as input, not as choices.

- **Phase 2 — memory write pressure** (C1/C4): something must push back on growth in the
  always-injected memory files. Candidates on record: write-time caps (hermes) vs gated batch
  consolidation (OpenClaw) vs a mix. Important, not the source.
- **Phase 3 — history recall** (C2/C3): conversation beyond the window and memory beyond the
  filename need a path. Candidates on record: transcript FTS (hermes), hybrid search + trigger
  phrases (OpenClaw).
- **Phase 4 — prompt stability** (B1/B2): the mid-turn clock and prompt ordering vs Gemini
  implicit caching; verified by phase 0's cache ratio either way.

## Deferred

- **Compact-don't-trim + flush-before-forget** (B3): our trim is continuous (the reducer slices on
  every state update), so the flush trigger needs real design, and both references' compaction
  war stories (data-loss regressions → safeguard audits) mark this as the risky one. Revisit when
  phases 0–4 readings show it binding.
- **Multi-channel robustness kit** (home-channel/dead-target/delivery ledger) and the
  **capability-stamped prompt line**: right ideas, no current pain at two channels; backlog.
