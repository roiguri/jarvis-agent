# OpenClaw Architecture Deep Dive (source-level)

**Date:** 2026-08-25. **Method:** produced by a research agent from a shallow clone of
`github.com/openclaw/openclaw` @ commit `d7455529` plus the project docs; every substantive claim
carries a file:line or doc citation and a VERIFIED (read directly) / INFERRED tag. Commissioned to
answer the cross-scope-context, memory-layer, multi-channel, and prompt/caching questions in
[../PROBLEMS.md](../PROBLEMS.md); synthesis across both reference systems lives in
[../REFERENCE_ARCHITECTURES.md](../REFERENCE_ARCHITECTURES.md).

OpenClaw is TypeScript, a single "Gateway" process; the persistence unit is a per-agent SQLite DB
(`~/.openclaw/agents/<id>/agent/openclaw-agent.sqlite`).

---

## 1. Proactive-message → conversation path

**The core design decision: there is ONE rolling "main session" and the heartbeat runs INSIDE it by
default.** Every DM from every channel collapses into a single session keyed `agent:<agentId>:main`
(`docs/concepts/main-session.md:10-21`, VERIFIED). "Heartbeat runs periodic agent turns **in the
main session**" (`docs/gateway/heartbeat.md:14`, VERIFIED; session resolution in
`src/infra/heartbeat-runner-session.ts:18-118` — every fallback path returns `mainSession()`). The
heartbeat prompt is sent **verbatim as a user message** into that session
(`docs/gateway/heartbeat.md:74`), so the heartbeat turn (prompt + assistant reply + tool calls) is
literally appended to the same message array the next user turn replays. UIs *hide* heartbeat
prompts and OK-only acks, but "the underlying session transcript can still contain those turns for
audit/replay" (`docs/gateway/heartbeat.md:325-327`, VERIFIED). **So when the user replies to a
proactive alert, the antecedent is trivially present — the alert is an ordinary assistant message
in the same transcript.** This is the inverse of Jarvis's two-thread split: OpenClaw pays context
bloat for free continuity (see §6 — that cost is their #1 complaint), and `isolatedSession` was
added later as the escape hatch.

**Response contract** (`docs/gateway/heartbeat.md:103-114`, VERIFIED): reply `HEARTBEAT_OK` when
nothing needs attention (stripped/dropped if remaining content ≤ 300 chars), or call the one-shot
`heartbeat_respond` tool (`src/agents/tools/heartbeat-response-tool.ts:21-39`: `outcome`, `notify`,
`summary`, `notificationText`, `priority`, `nextCheck`, `scratch`) — structured tool response wins
over text. Duplicate-alert suppression: last delivered alert text is persisted on the session row
(`lastHeartbeatText`/`lastHeartbeatSentAt`, `src/config/sessions/types.ts:318-320`) and an
identical text within 24h is silently dropped (`src/infra/heartbeat-runner-delivery.ts:302-317`,
VERIFIED).

**Three distinct bridge mechanisms carry background output into conversational context:**

1. **System-event queue** — an in-memory, per-session-key queue (max 20, deduped, keyed replace
   semantics) of human-readable lines "prefixed to the next prompt"
   (`src/infra/system-events.ts:1-4,38,165-172`, VERIFIED). A cron job with `sessionTarget: "main"`
   doesn't run a model at all — it enqueues a system event into the main session and optionally
   wakes the heartbeat (`--wake now|next-heartbeat`); "the event is processed with that session's
   existing context" (`docs/automation/cron-jobs.md:319`, VERIFIED). The next turn (user or
   heartbeat) drains the queue and formats entries as timestamped `System:` lines prepended to the
   prompt (`src/auto-reply/reply/session-system-events.ts:106+` `drainFormattedSystemEvents`,
   VERIFIED). Ownership rules prevent double-consumption: cron-tagged events are left queued for
   the heartbeat prompt-builder during heartbeat runs, but ordinary user turns drain them as
   fallback if the heartbeat was skipped (`session-system-events.ts:29-42` comment, VERIFIED). On
   session reset, queued stale events are **discarded** so old background notices don't lead the
   new session (`docs/concepts/session.md:157-160`, VERIFIED).

2. **Transcript mirroring** — when an **isolated** run (cron `sessionTarget: "isolated"`, or
   isolated heartbeat) delivers text directly to a channel, the delivered assistant message is
   **appended into the target conversation's transcript** as an assistant message via
   `appendAssistantMessageToSessionTranscript`, guarded by session-identity CAS (expected sessionId
   + lifecycle revision) so it can't append into a rolled/renamed session
   (`src/cron/isolated-agent/delivery-dispatch-awareness.ts:564-580, 585-645`
   `appendAdmittedDirectCronDeliveryTranscriptMirror`, VERIFIED). **This is exactly the missing
   Jarvis mechanism: the proactive send becomes a first-class assistant message in the thread the
   user will reply in, so the reply has its antecedent.** The mirror resolves the destination
   session key through the same outbound routing as normal sends, and only persists the route after
   platform delivery succeeds (`delivery-dispatch-awareness.ts:305-317, 322-343`, VERIFIED).

3. **Awareness events for the *other* session** — in addition to the mirror, isolated cron runs
   with an explicit delivery target enqueue a system event into the **main** session ("so the brain
   knows what its background limb did") and/or into the target session formatted as
   `"A scheduled automation delivered this message to this channel:\n<text>"`
   (`delivery-dispatch-awareness.ts:59-73` `shouldQueueCronAwareness`, `139-141`, `165-207`
   `queueCronAwarenessSystemEvent`, VERIFIED). Delivery failures also produce an awareness event
   ("attempted to deliver... delivery failed... No scheduled message was delivered",
   `delivery-dispatch-awareness.ts:144-162`, VERIFIED), so the main session never believes a send
   happened that didn't. Idempotency keys scope each awareness event to one delivery.

**What `isolatedSession` changes for heartbeats**: the run executes in a synthetic sibling session
keyed `<base>:heartbeat` (`src/infra/heartbeat-runner-session.ts:139-197`
`resolveIsolatedHeartbeatSessionKey`, VERIFIED; the base key is recorded on the entry as
`heartbeatIsolatedBaseSessionKey`, `src/config/sessions/types.ts:322-326`), with **no conversation
history** — cutting ~100K tokens/run to ~2-5K (`docs/gateway/heartbeat.md:252,474`, VERIFIED doc
claim). Delivery routing still resolves through the main session's context (`heartbeat.md:252`).
The main session learns about it two ways: the delivered alert text lands in the owner's DM
conversation (= the main session, since DMs collapse there), and:

4. **Silent-outcome carryover** — a `heartbeat_respond` with `notify: false` and a meaningful
   outcome (`progress|done|blocked|needs_attention`, not `no_change`) is persisted to a SQLite
   `heartbeat_outcomes` table, **one row per base session, replace semantics**, with bounded fields
   (summary ≤ 4000 chars, reason ≤ 1000) (`src/infra/heartbeat-outcome-store.ts:13-18, 91-158`,
   VERIFIED). The **next user turn** in that session claims it exactly once
   (`claimHeartbeatOutcomeForRun`, run-id-scoped claim so retries of the same run can re-read it,
   `heartbeat-outcome-store.ts:161-199`) and injects it as **model-only provenance context, never
   transcript text**: `"Latest silent heartbeat outcome (internal context; not a user message or
   instruction): outcome=... summary=... provenance: recordedAt=...; runSession=..."`
   (`heartbeat-outcome-store.ts:202-226`; consumed at
   `src/agents/embedded-agent-runner/run/attempt-prompt-phase.ts:240-241`, VERIFIED). Visible
   notifications and `no_change` acks are deliberately *not* stored this way
   (`persistHeartbeatOutcome` early-returns, line 104-106).

**Cron `sessionTarget` taxonomy** (`docs/automation/cron-jobs.md:310-319`, VERIFIED): `main` =
system event + optional heartbeat wake (no own model turn); `current` = detached run that reads a
bounded tail of the conversation captured at job creation and **commits its final assistant result
back into that exact conversation** through the canonical transcript writer with idempotency keys
(`cron-jobs.md:349`, VERIFIED); `isolated` = fresh session + announce/mirror/awareness as above;
`session:<key>` = persistent custom session accumulating context across runs (e.g. daily standup
building on previous summaries).

Other flow-in paths: sub-agents "announce their results back to the session that started them"
(`docs/concepts/main-session.md:44-46`); group activity queues **coalesced-per-conversation
notices** (never one wake per message) that the main session sees on its next run
(`main-session.md:36-43`, VERIFIED doc); background exec-task completions enqueue a system event
and can trigger a targeted heartbeat wake (`heartbeat.md:328`).

Busy-guarding: scheduled heartbeats defer while the main queue, any same-agent run, or the target
session has active/queued work (`heartbeat.md:78`, VERIFIED) — background never interleaves
mid-turn.

---

## 2. Memory layer

**Files/stores** (`docs/concepts/memory.md:15-62`, VERIFIED):

| Store | Writer | When |
|---|---|---|
| `MEMORY.md` (workspace root) | Agent (chat), dreaming deep-phase consolidation subagent | On request; nightly promotion |
| `USER.md` | Agent | Preference directives with observed-date + active/superseded metadata; supersede-in-place, not append (`memory.md:37-40`) |
| `memory/YYYY-MM-DD.md` daily notes | Agent; memory-flush turn; `/new`-`/reset` tail save | Working layer; searchable, NOT bootstrap-injected |
| `DREAMS.md` | Dreaming diary subagent | Human review only, never a promotion source (`dreaming.md:103-105`) |
| `memory/.dreams/` | Dreaming machine state (recall store, phase signals, checkpoints, locks) | Each sweep (`dreaming.md:20`) |
| Per-agent SQLite | Runtime | Session rows, transcripts + FTS, memory index chunks, heartbeat outcomes, plugin state |

**memory_search mechanics** (VERIFIED, `docs/concepts/memory-search.md:65-158`,
`memory-builtin.md:85-109`): corpus = `MEMORY.md` + root `USER.md` + `memory/*.md` (+ optional
`extraPaths`; session transcripts only if `experimental.sessionMemory: true` with `"sessions"` in
sources — default corpus is memory files only, `memory-search.md:180-183`). Chunked at **400 tokens
with 80-token overlap**, indexed in per-agent SQLite. Retrieval = parallel **vector search**
(default OpenAI `text-embedding-3-small`; Gemini supported incl. multimodal) + **BM25 via FTS5**,
weighted merge, then deterministic `hybrid relevance × recency decay × importance multiplier`
(30-day half-life for dated dailies; `MEMORY.md`/`USER.md` evergreen; importance scored once at
write time — no query-time model call, explicitly modeled on Generative Agents arXiv:2304.03442),
then **MMR diversity** (λ=0.7, Jaccard over snippet tokens, 24 candidates/leg). Filenames indexed
separately from bodies. Each chunk carries **SQLite-owned provenance** (origin class
`owner|agent|untrusted|system`, session kind, observation time, supersession key) "stored
separately from Markdown so recalled prose cannot rewrite its own trust classification"
(`memory-builtin.md:96-99`, VERIFIED). Fail-loud policy: an explicitly configured provider that's
down reports memory *unavailable* rather than silently degrading to keyword-only
(`memory-search.md:132-138`).

**How memory gets into the prompt** — three lanes:

1. **Bootstrap injection**: `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` injected
   whole into the system prompt as "Project Context", bounded by `bootstrapMaxChars` 20,000/file
   and `bootstrapTotalMaxChars` 60,000 total; overflow truncates the injected copy (file intact on
   disk) and injects a built-in truncation notice (`docs/concepts/system-prompt.md:92-122`,
   VERIFIED). Daily notes are **never** bootstrap-injected — except on a bare `/new`/`/reset`,
   where recent dailies are re-primed as a one-shot startup block (`system-prompt.md:109-111`).
2. **Deterministic trigger recall**: on eligible interactive turns, the inbound message is matched
   against short trigger phrases stored on indexed entries (`<!-- trigger: ... -->` written by
   dreaming); strong matches inject **up to 3 compact entries** as hidden pre-reply context — no
   recall model call, and only promoted/trusted entries from `MEMORY.md`/`USER.md` qualify;
   dailies/imports/transcripts are never auto-injected (`memory-search.md:110-122`, VERIFIED).
3. **Active Memory escalation**: a blocking recall sub-agent runs *only* when the message asks
   about the past AND deterministic recall found no strong trigger match
   (`docs/concepts/active-memory.md:10-15`, VERIFIED). `rememberAcrossConversations` (default ON
   for single-user/main-dmScope installs, OFF the moment any DM isolation is configured) adds
   bounded retrieval over the agent's *other private* transcripts — excerpts, never merged
   transcripts; groups excluded both directions (`active-memory.md:24-62`, `session.md:117-136`).

**Pre-compaction memory flush** (VERIFIED, `docs/concepts/memory.md:215-244`,
`compaction.md:156-174`): before compaction summarizes, OpenClaw runs a **silent extra agent turn**
prompting the model to write durable facts to memory files. On by default
(`compaction.memoryFlush.enabled`); can be pinned to a cheap local model via an exact
`memoryFlush.model` override that deliberately does not inherit the session's fallback chain.
`/new` and `/reset` similarly save the ending conversation's tail into daily notes
(`main-session.md:73-77`) — this was retrofitted after issue #45608 complained the flush only fired
on compaction (see §6).

**Write pressure / consolidation — "dreaming"** (VERIFIED, `docs/concepts/dreaming.md`): default-on
nightly cron (`0 3 * * *`). Three phases per sweep — **light** (stage/dedupe recent short-term
signals; no durable writes), **REM** (theme/reflection summaries; reinforcement signals), **deep**
(the only `MEMORY.md` writer). Deep promotion requires passing **all** of `minScore` +
`minRecallCount` + `minUniqueQueries` gates; ranking = 6 weighted signals (relevance .30,
frequency .24, query diversity .15, recency .15, multi-day consolidation .10, concept richness .06)
+ phase-hit boosts (`dreaming.md:137-150`). Promotion runs a **consolidation subagent rewrite** of
`MEMORY.md` that must preserve prior entries within `maxPriorEntryLossFraction`, cite
`Source: path#Lx-Ly` for every promoted candidate, fit the bootstrap budget, and parse as
structured output — else append-only fallback; the pre-rewrite `MEMORY.md` is snapshotted in SQLite
plugin state (`dreaming.md:72-97`). **Taint gate**: candidates with `untrusted`/`system` provenance
are structurally removed before the consolidation prompt — not score-penalized, excluded
(`dreaming.md:77-81`). Only interactive sessions feed ingestion — cron/heartbeat/subagent sessions
are excluded, recalled-context is stripped so recall can't self-reinforce into "new" memory
(`dreaming.md:68-70`). Promoted entries carry `<!-- trigger: ... -->` (≤3 phrases) +
`<!-- importance: N -->` metadata feeding lane 2 and the ranking multiplier (`dreaming.md:25-29`).

---

## 3. Sessions and history

**Session model** (VERIFIED, `docs/concepts/session.md:21-57`): a session maps to a *routing
scope*, not a channel. Defaults: all DMs (every channel) → one main session; each group/room → its
own session; each cron run → fresh; each webhook → isolated. `dmScope` options
`main | per-peer | per-channel-peer | per-account-channel-peer`; `identityLinks` maps one human's
multi-channel identities to a single canonical peer. Sessions are **rolling and unbounded in count
of turns**: default `reset.mode: "none"`, with opt-in daily (`atHour`) and idle (`idleMinutes`)
resets; **heartbeat/cron/exec system-event turns never extend idle/daily freshness** — only real
user interaction does (`session.md:148-160`, `heartbeat.md:326`, VERIFIED; separate
`sessionStartedAt` / `lastInteractionAt` / `updatedAt` timestamps, `session.md:191-197`). Reset
assigns a new live sessionId but the old transcript stays searchable under the same key
(`main-session.md:73-77`). Maintenance: 30-day prune, 500-row cap with protected classes, 10 GB
disk budget with verified compressed archive extraction of oldest unreferenced history
(`session.md:209-269`, `main-session.md:82-85`).

**Compaction** (VERIFIED, `docs/concepts/compaction.md`): triggers proactively near the context
limit AND reactively on provider context-overflow errors (dozens of matched error strings) with
compact-and-retry. Summarizes older turns into a compact transcript entry; keeps a recent tail
(`keepRecentTokens` default 20,000); **moves the split point so assistant-toolCall/toolResult pairs
are never severed** (`compaction.md:17`). Full history stays on disk — compaction only changes what
the model sees. New-config default `mode: "safeguard"` adds a summary **quality audit**: required
headings, pending asks, and exact identifiers (`identifierPolicy: "strict"`) must survive in the
stored text, with bounded corrective retries, else compaction aborts and keeps original history
(`compaction.md:22-31,111-113`). Separate `maxActiveTranscriptBytes` guard compacts on transcript
byte size. Compaction model is override-able (`compaction.model`), and the memory flush runs first
(§2).

**Cache-TTL-aware tool-result pruning** (VERIFIED, `docs/concepts/session-pruning.md:21-33`):
in-memory only, per-request, never touches disk. Gated on (a) cache TTL elapsed (else skip
*entirely* to preserve Anthropic prompt-cache reuse) and (b) context ≥ ~30% of window. Then
soft-trim tool results > 4,000 chars to first+last 1,500; if still ≥ ~50% usage with ≥ 50,000
prunable chars, hard-clear old tool results to a placeholder. TTL clock resets **only when pruning
actually changed context** so the next request re-caches once. Invariants: last 3 assistant turns
never pruned; nothing before the first user message pruned (protects bootstrap file reads). A
separate idempotent replay view strips already-processed image payloads from older turns while
byte-preserving the 3 most recent completed turns to keep cache prefixes stable
(`session-pruning.md:35-43`).

**Cross-session visibility** (VERIFIED): `sessions_search` — exact FTS over own past transcripts,
indexed in the same transaction that persists the message (never lags), active-branch only,
redacted excerpts, `sessions_history` opens surrounding context (`docs/concepts/session-search.md`);
visibility default `"tree"` = current session + spawned; the canonical main session sees every
same-agent session (`memory-search.md:185-191`). Plus: `sessions_send(sessionKey, message)` for
direct cross-session messaging (`src/agents/system-prompt.ts:633-635`), `sessions_spawn` with
push-based auto-announce of results back to the requester (`system-prompt.md:59`), semantic
session-memory (opt-in, §2), and Active Memory transcript recall (§2).

---

## 4. Multi-channel

**One conversation spine, per-channel side rooms.** Channels don't own conversations; routing does
(§3). All DMs from Telegram/WhatsApp/Slack/iMessage/web converge on the main session — "Ask
something on your phone, follow up from your laptop, and the agent has the same context in both
places" (`main-session.md:11-14`, VERIFIED). Groups/rooms stay per-group sessions by default and
surface into the main session as coalesced activity notices; a per-binding
`session.groupScope: "main"` can pull a trusted room into the spine, changing only session-key
selection — reply routing still targets the originating room (`session.md:74-105`, VERIFIED).

**Proactive channel selection**: heartbeat `target`: `owner` (default — first concrete
`commands.ownerAllowFrom` entry, then channel `allowFrom`; *never* resolves to a group;
unresolvable → skip `reason=no-route` before any model call) | `last` (explicit opt-in to most
recent external conversation incl. groups) | explicit channel id + `to` | `none` (internal-only
run) (`heartbeat.md:71,262-278`, VERIFIED). Cron announce delivery requires an explicit `--channel`
on multi-channel hosts unless a provider-prefixed `--to` (`telegram:123`) or preserved session
route disambiguates; mismatched channel/prefix pairs are rejected rather than misinterpreted
(`cron-jobs.md:380-388`, VERIFIED). Agent-initiated proactive sends go through a channel-neutral
`message` tool (`message(action=send, target, channel, ...)`).

**Capability abstraction — the agent is told what the surface can do, per turn, in the system
prompt.** Runtime adapters gather channel capabilities and pass them to the prompt builder
(`system-prompt.md:15`); the below-cache-boundary Runtime line renders
`channel=telegram | capabilities=inlineButtons,markdownDetails,...`
(`src/agents/system-prompt.ts:1639-1647`, VERIFIED). Capability flags gate whole prompt sections:
`markdownDetails` → a "Collapsible Details" section teaching `<details>` usage; `inlineButtons` →
button syntax guidance, and when buttons are *off* for the channel the prompt says exactly that
plus the config key to ask the owner for (`system-prompt.ts:1087-1092, 656-661, 671-686`,
VERIFIED). Channel plugins advertise message capabilities through discovery hooks
(`src/channels/plugins/message-capabilities.ts:4`).

**Interactive blocks**: a neutral `presentation.blocks` payload on `message(send)` — e.g.
`{"blocks":[{"type":"buttons","buttons":[{"label":"Yes","action":{"type":"callback","value":"yes"},"style":"primary"}]}]}`
(`system-prompt.ts:658`, VERIFIED) — mapped by each channel adapter (Telegram inline keyboards with
per-scope allowlist gating `off|dm|group|all|allowlist`, `docs/channels/telegram.md:493-563`,
VERIFIED). Native approval cards get prompt guidance to prefer the channel UI and fall back to
`/approve` text only when the tool result says buttons are unavailable (`system-prompt.md:68`).
This matches Jarvis's gateway-blocks layer almost exactly — validation that "neutral block contract
+ channel adapters" is the right shape; OpenClaw adds the prompt-side half Jarvis lacks (telling
the model per-turn which block kinds the current surface supports).

Per-channel `heartbeatVisibility` (`showOk`/`showAlerts`/`useIndicator`, precedence account →
channel → defaults → built-in) decides whether background acks/alerts are even rendered on a given
surface — and if all three are off the model call is skipped entirely (`heartbeat.md:333-362`,
VERIFIED).

---

## 5. Prompt assembly, caching, cost

**Structure** (VERIFIED, `docs/concepts/system-prompt.md:9-51`): fully code-built per run (no
provider default prompt); a pure renderer (`buildAgentSystemPrompt`) + config resolver + runtime
adapters. **Stable-prefix / dynamic-suffix boundary is an explicit architectural line**: large
stable content (tooling rules, safety, skills list, workspace "Project Context" = bootstrap files)
sits **above the internal prompt-cache boundary**; volatile sections (Messaging, Group Chat
Context, Reactions, Heartbeats guidance, Runtime line, channel-capability guidance) are appended
**below** it so prefix caches survive channel-to-channel turns (`system-prompt.md:51`; enforced in
code, e.g. "Channel/session-specific guidance lives below the cache boundary"
`src/agents/system-prompt.ts:1487,1526`; a "dynamic context files" bucket exists for
frequently-changing injected files, `system-prompt.ts:95,156-158,210`; stable-prefix render cache
of 64 entries, `system-prompt.ts:98`).

**The clock**: "Temporal Context" carries only the local **date + timezone**, deliberately below
the cache boundary "so day rollover or a timezone change does not invalidate the stable prefix";
the **exact current time comes from the `session_status` tool** on demand
(`system-prompt.md:44,133-137`, VERIFIED). This is a direct answer to Jarvis's
`[Current time: ...]`-in-every-turn pattern: coarse date in suffix, precise time behind a tool.

**Provider caching** (VERIFIED): the Anthropic plugin auto-configures
`contextPruning: cache-ttl, ttl 1h` and stretches heartbeat cadence to 1h for OAuth auth (30m for
API key) the first time it resolves Anthropic auth — cadence and cache TTL are deliberately
co-tuned so heartbeats land within the cache window (`session-pruning.md:45-54`). Pruning's whole
design is cache-aware (§3). `cacheRetention: none|short|long` resolves per provider family; Gemini
2.5/3 is prompt-cache-eligible via `promptCacheKey`
(`src/agents/embedded-agent-runner/prompt-cache-retention.ts:13-60`, VERIFIED — relevant to Jarvis
on Gemini: implicit caching benefits from the same stable-prefix discipline). Provider plugins may
inject stablePrefix/dynamicSuffix contributions relative to the boundary
(`system-prompt.md:19-27`).

**Cost governance / model tiering**: separate model overrides for heartbeat runs
(`heartbeat.model`), compaction (`compaction.model`), memory flush (`memoryFlush.model` — exact, no
fallback inheritance), dreaming (`dreaming.model`) — each background lane can run on a cheap/local
model (VERIFIED across docs cited above). Known hazard: a non-isolated heartbeat on a small-window
model leaves that model set on the shared session ("heartbeat model bleed"), and the
overflow-recovery message names it (`heartbeat.md:480-484`, VERIFIED). **lightContext mechanics**:
`lightContext: true` maps to bootstrap mode `"lightweight"`, whose filter **returns an empty file
list** — zero bootstrap files injected; a heartbeat run then carries only: system prompt sans
Project Context + the heartbeat prompt + monitor scratch (appended by the runner as
`Heartbeat monitor scratch:\n...`) + any queued cron/exec event prompts
(`src/agents/bootstrap-files.ts:154-166` VERIFIED; scratch append
`src/infra/heartbeat-runner-prompt.ts:212-221`). Scratch is capped at 256 KiB, and
**effectively-empty scratch skips the model call entirely** (`reason=empty-heartbeat-file`), as
does all-visibility-off (`reason=alerts-disabled`) and no resolvable route (`reason=no-route`) — a
whole family of pre-LLM gates (`heartbeat.md:394-415,315-321`, VERIFIED). Inspection tooling:
`/context list|detail|map`, `/usage tokens` with per-file raw-vs-injected sizes and tool-schema
overhead (`docs/concepts/context.md:20-27,36-55`, VERIFIED).

---

## 6. What hurts (issues/community; titles VERIFIED via search, contents INFERRED from summaries)

- **Main-session heartbeat context bloat** —
  [#20011](https://github.com/openclaw/openclaw/issues/20011): "built-in heartbeat only runs in
  main session — causes inevitable context bloat with no escape hatch"; hundreds of accumulated
  heartbeat turns inflate every request; multi-MB session files.
  [#43767](https://github.com/openclaw/openclaw/issues/43767): heartbeat ignored `lightContext`,
  288 full-context calls/day at 5m cadence. The `isolatedSession` + `lightContext` +
  empty-scratch-skip machinery in §1/§5 is visibly the remediation arc. **Lesson for Jarvis: the
  "background in the main thread" default buys context continuity but is the single most
  complained-about cost driver; Jarvis's two-thread split is the mirror-image failure (cheap but
  amnesiac). The synthesis is isolated runs + mirrored sends + silent-outcome carryover.**
- **Short-session agents never compact → never flush memory** —
  [#60719](https://github.com/openclaw/openclaw/issues/60719): compaction-triggered memory writes
  never fire for agents whose sessions stay short;
  [#51572](https://github.com/openclaw/openclaw/issues/51572) and
  [#45608](https://github.com/openclaw/openclaw/issues/45608) asked for the flush on reset/prune,
  not just compaction — since addressed by the `/new`/`/reset` tail-save (`main-session.md:73-77`).
  Maintainer-acknowledged weakness: memory durability was coupled to one lifecycle event.
- **Compaction data-loss regressions** —
  [#32106](https://github.com/openclaw/openclaw/issues/32106) (aggressive compaction loop with
  memoryFlush), [#60213](https://github.com/openclaw/openclaw/issues/60213) (post-overflow
  compaction silently killed a session, all context lost). The `safeguard` mode + quality audit +
  abort-rather-than-write behavior (`compaction.md:22-31`) is the response.
- **Bootstrap re-injection cache-busting** —
  [#67419](https://github.com/openclaw/openclaw/issues/67419): volatile bootstrap files (edited
  `MEMORY.md`) invalidate the prompt cache every turn; drove the dynamic-context-file bucket and
  moving heartbeat instructions out of a workspace file into DB scratch (`heartbeat.md:411-413`
  doctor migration of `HEARTBEAT.md` → scratch).
- **Delivery wedges** — [#92082](https://github.com/openclaw/openclaw/issues/92082):
  `pendingFinalDelivery` retried forever and blocked all heartbeats (the
  `CLEARED_PENDING_FINAL_DELIVERY_FIELDS` recovery code in `heartbeat-runner-delivery.ts:35` is
  the fix trail). Delivery state machines need operator escape hatches.
- **Cost** — community reports of $50-100/day unoptimized
  ([optimization guide](https://github.com/OnlyTerp/openclaw-optimization-guide),
  [betterclaw.io](https://www.betterclaw.io/blog/openclaw-memory-fix)); the Anthropic
  smart-defaults (1h heartbeat, cache-ttl pruning) exist because defaults were burning money.
- Structural (INFERRED from docs' warning density): single-user-by-default DM collapse is a privacy
  footgun for multi-user installs (`session.md:36-40` warning + `security audit` recommendation);
  duplicate-alert suppression, busy-deferral, and ghost-reminder tests
  (`heartbeat-runner.ghost-reminder.test.ts`) show proactive-nagging and re-delivery correctness
  are perennial bug farms.

---

## Top 10 transferable mechanisms for Jarvis

Ranked by relevance to (a) cross-scope context / C5, (b) memory layer, (c) multi-channel
generalization:

1. **(a) Transcript mirroring of proactive sends** — when the heartbeat/Outbox delivers to the
   owner, append the delivered text as an assistant message *into the user thread's LangGraph
   state* (idempotency-keyed, only after send success — matching Jarvis's log-on-success Outbox
   seam). This is OpenClaw's `appendAssistantMessageToSessionTranscript`
   (`delivery-dispatch-awareness.ts:564`) and it directly closes the "reply lands without an
   antecedent" gap — strictly better than Jarvis's current prompt-injected notification slice,
   because the reply's antecedent survives as real history, not a per-turn system-prompt rebuild.
2. **(a) Silent-outcome carryover store** — persist each heartbeat tick's structured ack (Jarvis
   already *has* `heartbeat_respond`-shaped acks!) into a one-row-per-thread, replace-semantics,
   bounded table; the next user turn claims it once and injects it labeled "internal context; not a
   user message" (`heartbeat-outcome-store.ts:91-226`). Gives the user scope awareness of *silent*
   background work — the case the notification slice can't cover.
3. **(a) Session-scoped system-event queue** — a small (≤20), deduped, keyed-replace queue of
   one-line notices drained into the next turn's prompt, with owner-scoped consumption and
   discard-on-reset (`system-events.ts`). Cheaper and more targeted than Jarvis's "whole day of
   chat/notifications" injection: events are consumed once instead of re-injected every turn, and a
   `contextKey` replace policy means an updated status overwrites rather than accumulates.
4. **(b) Trigger-phrase deterministic recall** — write `<!-- trigger: ... -->` +
   `<!-- importance: N -->` annotations at memory-write time; on each user turn, match the inbound
   message against triggers (keyword-level, no model, no embeddings required) and inject ≤3 curated
   entries from protected files only (`memory-search.md:110-122`). This is a memory-search on-ramp
   for Jarvis that needs **no** vector infrastructure and respects the "index-file, read-on-demand"
   design.
5. **(b) Pre-compaction/pre-trim memory flush** — before Jarvis's hard 50-message trim discards
   turns, run a silent cheap-model turn: "write durable facts from the soon-to-be-dropped span into
   memory/daily log" (`compaction.md:156-174`, and heed #60719/#45608: hook it to the *trim*,
   Jarvis's actual forgetting event, not to a compaction Jarvis doesn't have). Highest-leverage
   single memory change available.
6. **(b) Gated consolidation ("dreaming-lite")** — a scheduled heartbeat task that promotes
   daily-log material into MEMORY.md only through thresholds (recurrence across days, distinct
   contexts) with a validated rewrite (prior-entry preservation floor, source references, size
   budget, snapshot-before-write, append-only fallback) (`dreaming.md:57-97`). Prevents the
   write-pressure bloat Jarvis's freely-writable memory dir will otherwise accumulate; the
   validation contract is the transferable part even without the three-phase machinery.
7. **(a) Structured escape-hatch spectrum for background runs** — the
   `main / isolated+mirror+awareness / current-commit / persistent-custom` sessionTarget taxonomy
   (`cron-jobs.md:310-319`). For Jarvis: keep the heartbeat thread, but classify each task's
   *output* — "commit result into user thread" (current-style) vs "notify + awareness note"
   (isolated-style) vs "internal only" — instead of one delivery path for everything.
8. **(c) Capability-stamped prompt suffix** — per-turn `channel=... capabilities=...` runtime line
   plus capability-gated guidance sections, including "this capability is OFF here, ask the owner
   for <config key>" (`system-prompt.ts:660,1087-1092,1639-1647`). Jarvis's blocks layer already
   abstracts the wire; this adds the model-facing half so the same prompt works on Telegram and
   jarvis-app without hardcoding channel names in tool docstrings — the piece that makes a third
   channel free.
9. **(a/c) Pre-LLM skip reasons + duplicate-alert suppression** — the named-reason gate family
   (`no-route`, `empty-heartbeat-file`, `alerts-disabled`, busy-deferral) and last-alert-text
   dedupe within 24h (`heartbeat-runner-delivery.ts:302-317`). Jarvis has the due-gate; adopting
   *named skip reasons* (observable in logs/trace.py) and send-dedupe hardens the proactive path
   against nagging — OpenClaw's test suite shows this is where the bugs live.
10. **(b/cost) Stable-prefix/dynamic-suffix discipline + tool-triggered clock** — order Jarvis's
    `build_system_prompt` so per-turn volatiles (time envelope, today's slices, active-scope
    framing) sit at the *end*, keep bootstrap files byte-stable across turns, and move the precise
    clock behind a tool with only date+tz in the prompt (`system-prompt.md:44,51,133-137`).
    Gemini's implicit caching rewards exactly this; Jarvis's current time-envelope-first ordering
    busts the prefix every minute.

**One caution transfer**: OpenClaw's #1 pain (main-session heartbeat bloat) is the failure mode
Jarvis avoided by design — so don't import "heartbeat in the user thread." Import the *bridges*
(1-3), which OpenClaw built precisely so isolated background runs could behave as if they were in
the conversation.
