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

**1c — the mirror (drain-at-next-turn design).** The delivered text of every owner-addressed
proactive send becomes real conversation history — materialized at the start of the next user
turn, not at delivery time.

*Mechanism.* No new store and no new writer: `notifications.jsonl` — which the Outbox already
writes on delivery success — **is** the pending queue. A code-owned cursor
(`jarvis_data/agent/mirror_cursor.json`, the timestamp of the last mirrored row) marks progress.
At the start of a user-scope turn on the owner thread (both conditions — heartbeat scope never
drains), `ask_jarvis` reads rows newer than the cursor and coalesces them into **one user-role
message** with provenance per line, passed in the same `invoke` input ahead of the user's message:

```python
invoke({"messages": [
    HumanMessage("[Messages Jarvis sent you since the last turn:]\n[Reminder] …\n[Heartbeat] …"),
    HumanMessage(user_input),
]})
```

User-role-coalesced is load-bearing, not stylistic (independent review, 2026-08-25): the message
reducer advances the window to the first *user* message (Gemini requires user-first), so a leading
assistant-role mirror on a fresh thread would be **silently dropped while the cursor still
advanced** — and multiple assistant-role mirrors would also put consecutive model-role contents on
the Gemini wire, the exact hazard hermes hit before switching to user-role mirrors. One user-role
block survives the reducer structurally, keeps the wire alternating, and stays one message
regardless of backlog. Provenance lives in the text (`[Reminder] …` per line; event → prefix
covers all four frozen kinds, `llm_notification` included; unknown events render as
`[Notification]`; `heartbeat_outcome` rows are excluded — they are 1d's).

The cursor (stamped with the **last drained row's timestamp**, never `now()`) advances **only on
successful turn completion** — not in the exception path — so a failed turn re-delivers.
First run / missing cursor file: drain a recency window (today only), which re-mirrors at most
what the old slice already showed; the same window bounds any backlog. Timestamp-equality and
clock-step edge cases are accepted in writing (microsecond ISO stamps, single writer; a
monotonic row id is the additive fix if they ever bite).

*Why drain-at-next-turn rather than append-at-delivery* (both references append at delivery):
our checkpoint writer is not safe to race — a mid-turn `update_state` from the delivery path can
be orphaned off the checkpoint lineage — and nothing reads the thread between turns, so deferring
is observably identical, keeps `invoke` the **single** entry point into thread state, and removes
the race by construction (the drain runs inside the already-serialized turn path). Closest
reference analogue: OpenClaw's system-event queue, made durable.

*Resistance, case by case:* restart with pending rows → files, nothing lost. Crash mid-turn after
draining → cursor never advanced → re-delivered next turn (fail-toward-redelivery, the
`state.json` stamp direction; a checkpointed-then-redelivered block means "seen twice in the
thread" — accepted and honest). Flood after a long gap → the recency window, with a generous
per-entry length cap (no 240-char truncation — that was C5 gap 3). Slash commands short-circuit
before `ask_jarvis`, so command-only interactions leave briefings pending — they wait for a real
turn. Telemetry expectation, stated so it isn't misread later (E3): user input/turn may show a net
**rise** after 1c+1d — mirrors ride the window while the deleted slice only shaved the prompt —
and that is the design working, not a regression.

*Prerequisite fixed under review (1b follow-up):* the claimed "already-serialized turn path" was
per-channel only — nothing serialized Telegram against jarvis-app, which since 1b means two
concurrent turns could race the one owner checkpoint. A shared owner-turn lock in
`process_inbound_message` (also wrapping the confirmation-outcome turn) now provides the
serialization the drain design assumes.

*Also in 1c:* **retire `_load_recent_heartbeat_notifications`** — the slice reads the same
delivered-sends log; with those in the thread it is a duplicate feed, and it never covered silent
ticks anyway (silent ticks write no notification row), so nothing is lost by removing it now.
Drained messages live in the checkpoint only — deliberately not re-appended to
`chat_history.jsonl` (they are already recorded in `notifications.jsonl`; double-recording would
put the same text in two agent-readable logs).

*Delivered text only*, never the tick's tool calls or reasoning — both references' rule.
**Felt test:** a reminder fires; ask "what did you just remind me about?"; then reply to a
briefing with a bare pronoun and watch it resolve.

**1d — silent-outcome carryover, on the same rails.** Ticks that act without messaging (sync,
cleanup, notes) currently leave no trace the user scope sees. The tick writes its structured ack
to the **same log** as a new event kind (`event="heartbeat_outcome"` — additive; the frozen
`event="heartbeat"` filter is untouched), and the same drain splits by event: send-events join
the 1c user-role block; outcome-events become a **labeled internal-context line — never
transcript text**, because Jarvis never said it (the line both references refuse to cross).
Review-driven specifics: the writer is `heartbeat.py` directly (silent ticks never reach the
Outbox — this is a new writer, and GATEWAY.md's "notifications.jsonl is proactive pushes only"
gets updated with it); **only acted-and-silent ticks write** (a no-op tick writing rows would let
nothing overwrite something); **all undrained outcomes are delivered, capped** — not
latest-one-wins, which could discard a real 03:00 sync behind a 07:00 cleanup; the injection
mechanism is a per-turn state field overwritten every turn, exactly the `heartbeat_due_tasks`
pattern; `get_notification_history` filters outcome rows so they don't surface as "notifications
sent". The shared cursor already gives delivered-once for both kinds — no second stamp.

**Open at 1d implementation — the file-naming fork:** outcome rows stretch the file's name (an
outcome is not a notification). Decide then between keep-one-file-and-filter (as specified above)
and a separate outcomes file (honest names, but a second file + stamp). Renaming
`notifications.jsonl` itself is off the table: the log's identity — what was delivered — is
unchanged by 1c, and the queue is the cursor's *view* of the log, not the file.

Close #50 at the end of 1d.

*Independent review, 2026-08-25:* the design above is post-review — two blockers (the
cross-channel race; the reducer dropping assistant-role mirrors) and the 1d semantics were found
by an adversarial pass against the code and fixed here before implementation. The review also
confirmed: the invoke-input mechanism, the shared-cursor delivered-once property, and the
ack-walker/telemetry non-interactions.

**Blast radius note (1b):** the spine is the one step with product-visible behavior change and the
one that touches the checkpoint key. It is deliberately its own PR with its own staging soak
before 1c builds on it.

**Known deviation from the references, accepted:** Hermes's seed puts the briefing in the session
even if the user never replies; ours materializes only on the next turn. For every current reader
(the only consumer of thread state is a turn) this is a non-difference; it is recorded here as
the one behavior a future feature could trip over.

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
