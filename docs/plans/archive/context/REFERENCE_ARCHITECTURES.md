# Context Handling — Reference Architectures (second pass)

**Date:** 2026-08-25.
**Companions:** [PROBLEMS.md](PROBLEMS.md) (the problem inventory this informs) and
[RESEARCH.md](RESEARCH.md) (the 2026-08-06 first pass, whose §1 this supersedes).
**Sources:** two source-level deep dives, produced by research agents from shallow clones with
file:line citations and per-finding VERIFIED/INFERRED tags —
[reference/OPENCLAW_DEEP_DIVE.md](reference/OPENCLAW_DEEP_DIVE.md) (`openclaw/openclaw` @
`d7455529`) and [reference/HERMES_DEEP_DIVE.md](reference/HERMES_DEEP_DIVE.md)
(`NousResearch/hermes-agent` @ `41447a6`). This file is the synthesis; the deep dives are the
evidence. Citations below name the deep-dive file, not the upstream source — follow the chain.

**What this is.** What the two reference systems actually do about the concerns in PROBLEMS.md,
read from source rather than docs, with the first pass's errors corrected. Mechanisms are recorded
as candidates with the problems they address — this is still not a plan, a ranking of Jarvis work,
or a decision.

---

## 1. The headline: both systems converged on the same C5 answer, from opposite directions

The first pass framed the design space as a choice between OpenClaw's shared conversation spine
and hermes's isolated sessions. Source reading shows both projects have spent their recent history
migrating **toward the same middle**, each from its own pole, and the middle is one pattern:

> **Run background work isolated; at delivery time, write the *delivered text* — and only the
> delivered text — into the transcript of the conversation where the user will reply.**

- **OpenClaw** started with the heartbeat *inside* the main session. That default is their #1
  community complaint (issues #20011, #43767: hundreds of accumulated heartbeat turns inflating
  every request; 288 full-context calls/day). Their remediation arc built `isolatedSession` +
  `lightContext` and then three bridges so isolation doesn't cost continuity: **transcript
  mirroring** (the delivered message is appended into the target conversation as an assistant
  message, idempotency-keyed, only after platform delivery succeeds), **awareness events** (a
  deduped per-session queue of one-line notices, consumed once by the next turn — including
  delivery-failure notices, so the main session never believes a send happened that didn't), and a
  **silent-outcome carryover store** (a tick that acted without notifying persists its structured
  ack — same shape as our `heartbeat_respond` — one row per thread, replace semantics; the next
  user turn claims it exactly once as labeled internal context). (OPENCLAW_DEEP_DIVE §1, §6.)
- **hermes** started with full isolation and the gap accepted — a user reply to a cron delivery
  landed in a session that had never seen the brief (their documented "what is Task #2?" amnesia).
  Their remediation is the **mirror/seed**: opt-in per job, the delivered brief is written into
  the origin chat's session — or, on thread-capable platforms, into a dedicated thread whose
  session is *seeded* with the brief so the future reply's session key resolves to a conversation
  that contains its antecedent. "The seed IS the feature." (HERMES_DEEP_DIVE §1.)

Neither system bridges the way Jarvis does — **neither injects cross-scope material as
system-prompt slices**. Both put it in the message stream of the receiving conversation, durable
and consumed once, rather than re-injected every turn and gone at midnight.

Both also agree on a boundary Jarvis's C5 discussion should inherit: the reply surface receives
**only the delivered text**, never the background run's own transcript (tool calls, reasoning).
The full background transcript remains reachable — in both systems — through search (§3).

**One implementation detail with teeth: message role.** OpenClaw mirrors as *assistant*-role
(true authorship). hermes deliberately mirrors as **user-role with a provenance prefix**
(`[Cron delivery: {job}]`), after assistant-role mirrors broke strict-alternation providers, and
because mirror metadata died at their storage boundary — the prefix is the provenance that
survives (HERMES_DEEP_DIVE §1, §6 "Alice incidents"). Gemini is not strict-alternation, so Jarvis
could take either; the transferable rule is *provenance must live in the text, because metadata
does not survive storage boundaries*.

**Pain-point symmetry, for the record.** OpenClaw's pole (spine) cost them context bloat — their
top complaint. hermes's pole (fragmentation) cost them a 2026 bug class of amnesia incidents
patched one session-key lane at a time, and 89%-waste token replays across fragmented sessions
(#5563). Each system's worst pain is the other's founding choice. Jarvis's two threads +
prompt-slices sit off both poles: structurally cheap, avoids hermes's key-resolution bug class
(one owner thread per channel — nothing to mis-resolve), but with the weakest bridge of the three
systems (PROBLEMS.md C5's four gaps).

## 2. Memory: two opposite lifecycles, one shared premise

The premise both share and Jarvis lacks: **memory has a lifecycle with write pressure, not just
storage.** They implement it in opposite ways:

| | OpenClaw | hermes |
|---|---|---|
| Curation forced | **Nightly, in batch** — "dreaming": staged promotion into `MEMORY.md` through hard gates (min score + recall count + query diversity), a validated consolidation rewrite (prior-entry preservation floor, per-candidate source citations, size budget, snapshot-before-write, append-only fallback), and a taint gate that structurally excludes untrusted-provenance candidates | **At write time** — hard whole-file caps (`MEMORY.md` 2,200 chars, `USER.md` 1,375), a `[{pct}% — n/limit chars]` gauge in the injected block, over-cap writes rejected with current entries echoed + "consolidate then retry this turn", and a 3-strike circuit breaker |
| Search | Hybrid vector+BM25 over **memory files** (400-tok chunks, recency decay, write-time importance, MMR diversity; provenance stored in SQLite so recalled prose can't rewrite its own trust class) | **None over memory files.** FTS5 (bm25) over the **message transcript store** instead — `session_search`, ~20ms, no LLM, automation sessions demoted below interactive |
| Cheap recall lane | **Trigger phrases**: `<!-- trigger: ... -->` written at memory-write time; inbound messages keyword-matched, ≤3 curated entries injected; no model, no embeddings | Frozen-snapshot injection: the tiny capped files are always fully in-prompt — the caps *are* the injection budget (~1,300 tok) |
| Forgetting hook | **Pre-compaction memory flush**: a silent cheap-model turn writes durable facts *before* history is summarized away; retrofitted to `/new`/`/reset` after #60719/#45608 showed coupling it to compaction alone missed short sessions | Background review fork every 10 user turns ("should anything be saved?"), ~30K tok/event, skipped in cron |

Read against PROBLEMS.md: C2 (recall requires knowing the filename) is answered by *either*
search design — and hermes proves the no-vector version is complete (FTS5 over transcripts covers
C3 too, including compacted-away material, which stays searchable by design). C4 (nothing pushes
back on growth) is answered by either pressure design — caps-at-write or gated-batch-promotion.
C1 (uncapped injection) is answered by both: OpenClaw budgets bootstrap files (20k/file, 60k
total, truncation notices); hermes caps the stores themselves.

The first pass's "20k chars, 70/20 head/tail" claim about hermes memory was misattributed: that is
their *generic context-file* injection (a floor under a dynamic, window-proportional cap), not the
memory path (HERMES_DEEP_DIVE verdict #2).

## 3. Sessions, history, and the trim

Neither system hard-trims. Both compact — summarize-the-middle with a protected tail — and both
keep the full history on disk with the compacted-away portion **still searchable** (hermes flags
rows `active=0, compacted=1` and includes them in FTS by default; OpenClaw's compaction "only
changes what the model sees"). Both learned compaction is dangerous: OpenClaw's `safeguard` mode
audits the summary (required headings, identifiers preserved, abort-rather-than-write on failure)
after data-loss regressions; hermes anchors summaries to past tense, force-redacts credentials,
merges re-compactions into the prior summary, and raised its trigger to 75% for sub-512K windows
after "too permissive" complaints. Jarvis's 50-message hard slice (B3) has no analogue in either
system — it is the outlier design, and it discards what both systems go to lengths to keep
reachable.

Both also maintain a **cheap continuity channel for background tasks that bypasses sessions
entirely**: OpenClaw's per-monitor scratch (capped, prompt-injected, empty-scratch skips the model
call); hermes's per-job KV notepad (16KB/value) + `context_from: "self"` (≤8K chars of the
previous run's own output). Relevant to D1: both keep machine state code-owned and byte-capped
precisely because it is prompt-injected every run.

## 4. Multi-channel

Opposite session models, same capability story:

- **Conversation mapping.** OpenClaw: all DMs from every channel collapse into one main session
  (identity-linked); groups get side sessions surfacing as coalesced notices. hermes: platform is
  a hard component of the session key — one lane per channel — with explicit handoff (rebinding a
  whole transcript to a new channel's key) and mirroring as the only cross-channel continuity.
  Jarvis's one-owner-thread-per-channel is hermes-shaped.
- **Proactive channel choice.** Both are config-canonical, not recency-based: OpenClaw's heartbeat
  `target: owner` resolves through configured allowlists and *never* to a group, skipping
  pre-model with a named reason when unresolvable; hermes has per-platform home channels
  (`/sethome`), a persistent dead-target registry, and a durable delivery-obligation ledger with
  honest at-least-once markers. Jarvis's `JARVIS_DEFAULT_CHANNEL` is the same shape, minus the
  robustness kit.
- **Capability abstraction — both split it into a mechanical layer below the model and a prompt
  layer that tells the model what the current surface can do.** OpenClaw stamps a per-turn runtime
  line (`channel=telegram | capabilities=inlineButtons,...`) below the cache boundary and gates
  whole guidance sections on capability flags — including "this is OFF here, ask the owner for
  <config key>". hermes injects per-platform prose hints (markdown dialect, media semantics,
  explicitly *negative* capabilities) and ships numbered-text fallbacks for buttons so any channel
  can satisfy an interactive ask. Both validate the shape of Jarvis's gateway-blocks layer
  (OpenClaw's `presentation.blocks` is nearly isomorphic to it) — and both carry the model-facing
  half Jarvis deliberately deferred when capability-scoped tool binding was rejected: the model is
  *told* the surface's capabilities instead of having tools appear and vanish.

## 5. Prompt assembly, caching, cost

**The corrected convergence.** The first pass recorded "both keep a live clock out of the system
prompt and put ephemeral material in the user message." Half survives contact with source: the
real shared invariant is **byte-stability of the prompt prefix at the provider-cache granularity**
— coarse time only (OpenClaw: date+timezone below an explicit cache boundary, precise time behind
a tool; hermes: a date-granularity timestamp inside the volatile tier of a system prompt that is
built **once per session** and never rebuilt mid-conversation — an invariant their AGENTS.md lists
as forbidden to violate). "Never in the system prompt" was wrong for hermes; "ephemeral rides the
user message" describes their memory-prefetch fences, not the temporal material.

Read against the B cluster: B1 (the model's "now" moves mid-turn) and B2's mechanism (a per-minute
clock upstream of the whole prefix) have no counterpart in either system — both engineered them
away, on Anthropic *and* Gemini paths. Notably, **hermes emits no explicit cache markers for
Gemini at all**: their entire Gemini strategy is prefix ordering for implicit caching. That is
directly load-bearing for Jarvis-on-Gemini — the reachable win is ordering (stable → volatile,
coarse time last), not an API.

**Cache-scope subtleties worth keeping:** hermes derives `prompt_cache_key` from a
compression-lineage root so compaction doesn't cold-start the cache, and strips cron's per-fire
timestamp so all fires of one job share a cache scope. OpenClaw's tool-result pruning is gated on
cache-TTL expiry (prune only when the cache is already dead, then let it re-form) — and their
Anthropic plugin co-tunes heartbeat cadence to the cache TTL (1h/1h). The first pass's "cross-tick
caching is unreachable" claim is therefore **provider-conditional**, not absolute: reachable on
Anthropic by TTL pinning; still effectively true for Gemini implicit caching at hourly cadence.

**Cost governance, both:** per-lane model tiering (background/compaction/flush/consolidation each
overridable to a cheap model — with OpenClaw's "model bleed" hazard when a non-isolated lane sets
the shared session's model), pre-model skip gates with named reasons, token budgets on background
self-work (hermes capped its review fork at 600K input after one replayed 1.49M), and no
client-side USD caps anywhere — spend control is structure, not billing.

## 6. Corrections ledger (first pass → source)

| First-pass claim | Status after source reading |
|---|---|
| hermes: ephemeral never in system prompt; in user message | **Corrected** — volatile tier is in the system prompt; invariant is per-session byte-stability (verdict #1) |
| hermes: memory injected 20k/70-20 head-tail | **Misattributed** — that is generic context-file injection; a floor under a dynamic cap (verdict #2) |
| hermes: cron isolated, cron tools disabled | Verified; nuance: memory is *enabled* in cron — their own AGENTS.md is stale on this (verdict #3) |
| hermes: FTS5 over all session history | Verified; corpus is messages only, memory files not indexed (verdict #4) |
| hermes: caps 2,200/1,375 + gauge + forced consolidation | Verified; gauge format corrected; plus an undocumented 3-strike breaker (verdict #5) |
| hermes: head/**torso**/tail, protect ~20 msgs/~20k tok, compact at 50% | **Corrected** — head/middle/tail; message floor 8; tail = threshold×0.20; 75% trigger floor for <512K windows (verdict #6) |
| "Both keep a live clock out of the system prompt" | Refined — shared invariant is prefix byte-stability + coarse time; placement differs (§5) |
| "Cross-tick caching is unreachable" (RESEARCH.md §1) | **Provider-conditional** — OpenClaw reaches it on Anthropic by co-tuning cadence to TTL; still effectively true for Gemini implicit caching (§5) |
| OpenClaw session model (first pass table row: only `isolatedSession` noted) | Expanded — main-session default is their top pain; the bridge triad is the remediation (§1) |

Both deep dives also found **doc drift inside the reference projects themselves** (hermes's
AGENTS.md contradicting shipped cron behavior; OpenClaw docs lagging the heartbeat remediation) —
a reminder that this file's own §-references should be re-verified against upstream before any
build that depends on them.

## 7. Candidate mechanisms, mapped to PROBLEMS.md

Recorded as candidates with the entries they address — not a plan, not an order. Full detail and
rankings in the deep dives' "Top 10" lists, which agree to a striking degree.

| Candidate | Addresses | Both systems? |
|---|---|---|
| Mirror delivered proactive sends into the owner thread's transcript (provenance in the text; delivered text only; after send success) | C5 | **Yes — the convergence** |
| Silent-outcome carryover (persisted tick ack, claimed once by the next user turn) | C5 gap 4 | OpenClaw |
| Consumed-once event queue instead of re-injected daily slices | C5, A-cluster | OpenClaw (hermes has no slice mechanism at all) |
| FTS5 (bm25, no LLM) over the transcript/notification store | C2, C3, C5 backstop | hermes (OpenClaw: opt-in session sources) |
| Memory caps + gauge + same-turn consolidation, or gated batch promotion | C4, C1 | Both, opposite designs |
| Pre-forgetting memory flush hooked to the actual forgetting event (Jarvis: the 50-message trim) | C1/C3/B3 adjacency | OpenClaw (their #60719 lesson: hook it to *every* forgetting path) |
| Trigger-phrase deterministic recall (no vectors) | C2 on-ramp | OpenClaw |
| Compact-don't-trim, summaries in-transcript, archived rows searchable | B3, C3 | Both |
| Stable→volatile prompt ordering, coarse time, build-once-per-turn-window | B1, B2, A4-adjacent | Both (hermes: Gemini implicit caching relies on it exclusively) |
| Capability-stamped prompt line / platform hints incl. negative capabilities | blocks layer, multi-channel | Both |
| Per-task capped notepad + last-output injection for heartbeat continuity | D1, A6 | Both (scratch / notepad) |
| Named pre-model skip reasons + delivered-text dedupe window | E6, proactive hygiene | Both |
| Home-channel + dead-target registry + delivery ledger | Outbox robustness (multi-channel) | hermes |
