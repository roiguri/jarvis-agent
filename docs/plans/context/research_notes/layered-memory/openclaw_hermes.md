# OpenClaw and hermes-agent: whole-system memory/context architecture (October 2026 update)

**Scope and pins.** This updates and extends the internal 2026-08-25 deep dives, which were pinned at
`openclaw/openclaw@d7455529` (commit date 2026-08-24) and `NousResearch/hermes-agent@41447a6`
(2026-08-25): see `docs/plans/context/reference/OPENCLAW_DEEP_DIVE.md`, `HERMES_DEEP_DIVE.md` and
the synthesis `REFERENCE_ARCHITECTURES.md`. This pass reads:
- **OpenClaw @ `cc7e664dfe34`** (main, 2026-10-06). The latest stable release is `v2026.9.8`, and
  `v2026.10.1-beta.1` is tagged. There are 23,803 commits since the earlier pin.
- **hermes-agent @ `9dcab1e440cd`** (main, 2026-10-06). The latest tags are `v0.21.4+canary.20261006`
  and `rc.35-v0.21.5`. There are 24,700 commits since the earlier pin.

Both repos move very fast (about 500 commits a day), so any claim below may drift within days.
Links are permalinks at the pinned commits. Abbreviations: `OC/` means
`https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/` and `HM/`
means `https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/`.
Most OpenClaw evidence in this pass comes from its docs at HEAD, which the project keeps
in-tree and edits in the same PRs as the code. I checked the dreaming constants and the
consolidation call directly in source. Where this pass only read docs, it says so.

---

## 1. OpenClaw as one system: tiers, write path, recall lanes, background work

### Takeaway
OpenClaw now documents its memory as an explicit **five-tier model**: instructions, curated
core, episodic, prospective, and review. The tiers are tied together by one rule: **durable
memory has exactly one primary writer, the nightly dreaming consolidation pass, and every other
path only feeds the episodic tier.** Each tier has its own write rule, trust rule and injection
rule. Provenance, kept in SQLite columns, is the thread that runs through all of them. Since the
August pin the main changes are in the consolidation write mechanics, prospective memory, the
USER.md contract, and heartbeat context.

### Cited Findings

**The tier model (new as a single documented architecture; `docs/concepts/memory-architecture.md` changed +71 lines since the pin)**
- There are five tiers. **Instructions** (`AGENTS.md`) are written only by a human and always injected at session start. **Curated core** (`MEMORY.md`, `USER.md`) is written by dreaming consolidation or on direct user request, and injected at session start when provenance is eligible, within a budget. **Episodic** (`memory/YYYY-MM-DD.md` daily notes and session transcripts) is written by the agent, the memory flush and transcript capture, and is injected only on recall. **Prospective** (standing intents in SQLite, plus cron jobs) is injected only when a trigger fires. **Review** (`DREAMS.md`, dreaming reports) is never injected. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- The project states five design principles. (1) No hidden state. (2) "Writing is the hard part": curation moves off the reply path into a background pass, citing LongMemEval arXiv:2410.10813. (3) "The write path is the security boundary." (4) "Deterministic gates, model judgment inside them." (5) "Failures never block replies": every memory step on the reply path has a timeout and/or a fallback. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- "Durable memory has exactly one primary writer: the dreaming consolidation pass. Everything else feeds it." The feeders are agent notes written during work, the pre-compaction flush (facts go to daily notes), and transcript ingestion at session end. The design deliberately serves both usage shapes. A long single session feeds the pipeline through the flush. Many short sessions feed it through transcript ingestion. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- The agent can still write `MEMORY.md` on direct request ("Remember that I prefer TypeScript"). The generated workspace instructions still encourage recording durable facts, and "the default heartbeat prompt performs no memory maintenance on its own." — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md)

**Provenance hygiene (strengthened since the pin)**
- Origin class is a closed set: `owner|agent|untrusted|system`. Unknown provenance is never defaulted to `owner`. **Session-kind gating:** cron, heartbeat and sub-agent sessions "do not produce durable memory candidates." **Recall-loop prevention:** content injected from memory is structurally marked and never re-extracted ("A fact recalled one hundred times stays one fact"). The docs justify this with production audits that found "the overwhelming majority of auto-captured memories" were scaffolding restatements, heartbeat noise and recall feedback loops. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- New: **within-turn taint propagation.** After a tool result that declares network-sourced content, every later assistant message in that turn is classified `untrusted`, even inside an owner turn. The taint clears on the next user message. A memory flush records the least-trusted class for the whole file. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- New: `openclaw memory forget` removes tracked entries derived from selected sessions and excludes those session IDs from future ingestion. An admission policy can also keep channels or chat types out of dreaming ingestion. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)

**Prospective memory: standing intents (new in this pass)**
- "Remembering to act" is split out from fact memory. Time-based intents become cron jobs when they are uttered. Event-based intents go into a per-agent SQLite table through an `intent` tool, with machine-checkable triggers (keywords, optional embedding, channel/sender scope, expiry, fire budget, cooldown). A deterministic prefilter runs on every inbound message, and a hit injects the intent as hidden context with no model call. Defaults: 24h cooldown, 3-fire budget, 90-day expiry, at most 3 intents injected per turn. The rationale given is that "prospective recall degrades sharply with context length." — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)

**User model contract (USER.md; detail new since the pin)**
- `USER.md` entries are imperative directives ("Always/Never/Prefer"), each with an observed date and active/superseded status. A changed preference is superseded in place, never appended, "because append-only preference history reliably causes models to answer from the stale value." The docs cite PrefEval (ICLR 2025). — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- `USER.md` has a **fixed 4,000-character bootstrap cap**. Config can lower it but not raise it. The docs give an eviction ladder for when it approaches the cap: drop superseded entries, move non-profile facts to `MEMORY.md`, move detail to daily notes, move event-conditioned items to standing intents. — [OC/docs/concepts/user-model.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/user-model.md)

### Inferences
- The architecture rests on a **separation of writers by tier**: a human writes instructions, a gated batch writes the core, anyone writes the episodic tier, and code (intent/cron tables) holds the prospective tier. Jarvis currently lets the agent write MEMORY.md, USER.md and every other file freely, so it has no such separation.
- Standing intents are OpenClaw's answer to "conditional reminders stored as prose". Jarvis's triggers/heartbeat task system covers the time-based half. The event-conditioned half (trigger matched against inbound messages) has no Jarvis counterpart.

### Gaps
- I did not read source for the standing-intents prefilter scoring or the taint-declaration coverage list. These claims are docs-level.

---

## 2. OpenClaw dreaming: phases, scheduling, gates, rewrite safeguards, cost, model tier

### Takeaway
Dreaming is one managed cron sweep (default `0 3 * * *`) that runs light, then REM, then deep. In
source, the deep gate defaults are **minScore 0.75 + minRecallCount 3 + minUniqueQueries 3, top
10, 14-day recency half-life**. The biggest change since August is that **consolidation no longer
lets the model rewrite `MEMORY.md`.** A tool-free completion (60 s timeout) returns *operation
decisions*: add, merge or supersede. Deterministic code then composes the file from bounded,
sourced snippets (≤160 tokens each), with these checks:
- at most 25% prior-entry loss,
- a hard 10,000-character promotion budget,
- an optimistic-concurrency hash check before an atomic rename,
- the pre-image stored before any change,
- append-only fallback when any check fails.

It runs on the agent's **default model** unless overridden. The only operator lever on cost is
cadence plus a model override, and all background completions share a budget of 3 concurrent runs.

### Cited Findings
- The phases are **Light**: stage and dedupe recent material from daily files, redacted transcripts and recall state, with no durable write. **REM**: theme and reflection summaries plus reinforcement signals, no durable write. **Deep**: score and promote, the only `MEMORY.md` writer. They run in that order within one sweep, and the docs call them "internal implementation phases, not separate user-configured modes." — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- Source defaults (`src/memory-host-sdk/dreaming.ts`): frequency `"0 3 * * *"`, deep limit 10, `MIN_SCORE = 0.75`, `MIN_RECALL_COUNT = 3`, `MIN_UNIQUE_QUERIES = 3`, `RECENCY_HALF_LIFE_DAYS = 14`, `MAX_PROMOTED_SNIPPET_TOKENS = 160`, `MAX_PRIOR_ENTRY_LOSS_FRACTION = 0.25`. A calibration comment explains the 0.75 threshold: "3-day/3-query durable facts at 0.750-0.756, versus repeated filler at 0.489-0.549 and high-relevance one-offs at 0.529-0.606." The execution config type also has `speed`, `thinking: low|medium|high` and `budget: cheap|medium|expensive` fields. — [OC/src/memory-host-sdk/dreaming.ts](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/src/memory-host-sdk/dreaming.ts)
- The ranking weights are unchanged: relevance .30, frequency .24, query diversity .15, recency .15, multi-day consolidation .10, conceptual richness .06, plus a small light/REM phase-hit boost. One change: query diversity now counts "distinct *interactive* recall queries" (it used to count query/day contexts). — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- **Changed since the pin: the consolidation contract.** The old text said "a consolidation subagent rewrite". The current text says "a tool-free completion that chooses additions, merges, and supersessions against the current `MEMORY.md`", and "The model returns operation decisions, not replacement memory prose. The memory writer applies those decisions to the existing file using each candidate's bounded, sourced entry." — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md) (diff vs `d7455529`)
- In source, the deep phase groups candidates by project key. For each group it calls `subagent.complete({message: buildConsolidationPrompt(existingMemory, candidates, maxPromotedSnippetTokens), extraSystemPrompt: CONSOLIDATION_SYSTEM_PROMPT, model?, timeoutMs: CONSOLIDATION_TIMEOUT_MS})` with `CONSOLIDATION_TIMEOUT_MS = 60_000`. If the output parses to no structured plan, it logs and uses append-only fallback. — [OC/extensions/memory-core/src/dreaming-consolidation.ts](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/extensions/memory-core/src/dreaming-consolidation.ts)
- An accepted rewrite or append compaction must preserve prior entries within `maxPriorEntryLossFraction` (default 0.25), include every promoted candidate's `Source: path#Lx-Ly`, fit the bootstrap-safe budget, and parse. Append compaction may remove only whole machine-generated promotion sections. The previous `MEMORY.md` is stored in SQLite plugin state first, and `DREAMS.md` receives added/merged/superseded counts and diff highlights. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- **Write safety (new in docs).** The content hash captured when the consolidation input was built is re-checked just before an atomic rename. If an editor or another session changed the file in the meantime, the rewrite is aborted and the append fallback runs. If even an append cannot fit, `MEMORY.md` stays unchanged and the candidates remain eligible for a later sweep. "The residual race window is milliseconds wide… accepted by design in exchange for not requiring every editor of a plain Markdown file to share a lock." — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- **Size budget.** `DEFAULT_MEMORY_FILE_MAX_CHARS = 10_000` in `memory-budget.ts`. Promotion uses the smallest per-file bootstrap limit among agents sharing the workspace, and config can lower that cap but not raise it. An open feature request (#158897, 2026-09-26) asks to make it configurable. — [OC/extensions/memory-core/src/memory-budget.ts](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/extensions/memory-core/src/memory-budget.ts); [issue #158897](https://github.com/openclaw/openclaw/issues/158897); [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- Each newly promoted entry carries up to 3 `<!-- trigger: ... -->` concept tags and `<!-- importance: N -->` (1–10). Existing annotated entries are kept byte-for-byte unless they are explicitly merged or superseded. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- Inputs are restricted. Only interactive sessions are ingested. Cron, heartbeat, subagent and unknown sessions are excluded, personal/sensitive content is redacted, and runtime-marked recalled context is stripped. `untrusted`/`system` candidates are removed *before* the prompt is built, as a structural gate rather than a score penalty. The deep phase "rehydrates snippets from live daily files before writing, so stale/deleted snippets are skipped." — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- **Model tier and cost.** `dreaming.model` defaults to the workspace agent's *default model* and needs `subagent.allowModelOverride: true` to change. If the configured model is unavailable it retries once with the default model, and trust/allowlist failures produce a fallback diary trace plus a "degraded" outcome. Diary and consolidation completions "use fresh contexts without retaining conversation sessions or delivering chat replies." Dreaming shares the background work budget with Skill Workshop and other plugin completions: "at most three runs in total." — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)
- **Storage moved.** Dreaming machine state (recall store, phase signals, checkpoints, locks) moved from `memory/.dreams/*.json` to SQLite plugin state. JSON journals from before July 2026 are "no longer imported", and Doctor repair is required for unmigrated state. Dreaming is a declared managed cron job (`memory-core:memory-dreaming-promotion`) reconciled by runtime and Doctor. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md); [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md)
- Deep reports now explain non-promotion with counts by rejection category (no snippets). An empty sweep no longer marks a new workspace's first-run setup done. That second change is a fix shipped in 2026.9.4 (#141808), after empty dreaming sweeps suppressed the first naming conversation. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md); [OC/CHANGELOG/2026.9.4.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.4.md)
- A recovery and backfill toolchain exists. `memory promote-explain "<q>"` says why a candidate would or would not promote. `rem-harness` previews without writing. `session-backfill --apply|--rollback` stages historical transcripts through the same store reversibly. — [OC/docs/concepts/dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)

### Inferences
- Moving from "the model rewrites the file" to "the model emits ops and code composes the file" is the main lesson in this area. It turns validation from checking a model's prose into checking a small op list, and it makes a byte-level preservation guarantee possible for untouched entries. This matters for any Jarvis consolidation design.
- Requiring 3 recalls from 3 distinct queries with score ≥ 0.75 is conservative. Combined with the provenance gates, promotion can silently stall, as the incidents in §4 show. The rejection-category reports and `promote-explain` were added because of that.
- Per-sweep cost is bounded by structure rather than tokens: one completion per project-key group, each over ≤10 candidates of ≤160 tokens plus the current ≤10k-char file, plus diary completions. No published dollar figure was found.

### Gaps
- No published per-sweep token or dollar cost for dreaming was found.
- I did not read `CONSOLIDATION_SYSTEM_PROMPT` or the light/REM phase prompts. The light-phase daily ingestion lookback appears as `DEFAULT_DAILY_INGESTION_LOOKBACK_DAYS = 14` in `extensions/memory-core/src/dreaming-phases.ts`, but I only saw the constant, not how it is used.

---

## 3. OpenClaw search, daily notes, compaction/flush, and what background runs see

### Takeaway
Recall uses two lanes. **Lane 1** makes zero model calls: per-turn bootstrap refresh of eligible
core files, ranked hybrid search, and trigger injection of ≤3 curated entries at score ≥0.72.
**Lane 2** is an Active Memory sub-agent that runs only when the message shows recall intent and
lane 1 found nothing strong. Daily notes are never auto-injected except on bare `/new`/`/reset`.
Compaction is still summarize-the-middle with a 20k-token verbatim tail and a pre-compaction
flush, now hardened in three ways:
- a private flush copy,
- a "pending user asks" audit against superseded-task revival,
- a deterministic no-summary fallback on summary timeout.

Heartbeats now share the ordinary system prompt (no heartbeat section). They see monitor scratch
in their user message and, if isolated plus light, no bootstrap files at all. Cron sessions omit
`MEMORY.md`.

### Cited Findings

**Always-injected vs retrieved vs offline, with budgets**
- Bootstrap files `AGENTS.md`, `SOUL.md`, `IDENTITY.md`, `USER.md`, `MEMORY.md` (when present) and `BOOTSTRAP.md` (new workspaces only) are capped at `bootstrapMaxChars` 20,000 per file and `bootstrapTotalMaxChars` 60,000 in total. When truncation happens a fixed, non-configurable notice is injected. Sub-agent sessions inject only `AGENTS.md`. — [OC/docs/concepts/system-prompt.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/system-prompt.md)
- `MEMORY.md` is injected only "in eligible private sessions — subagent, cron, group, and channel sessions omit root memory, and memory without trusted provenance is filtered out." `USER.md` has a fixed 4,000-char cap. — [OC/docs/concepts/user-model.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/user-model.md)
- **Changed: eligible core files "refresh per turn within budgets so long-lived sessions pick up consolidation results without restarting."** This contrasts with hermes's frozen per-session snapshot. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- On the native Codex harness, `MEMORY.md` is not pasted into every turn. Instead "a small workspace-memory note" points the model to `memory_search`/`memory_get`, with a bounded prompt fallback when the tools are off. — [OC/docs/concepts/system-prompt.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/system-prompt.md)
- Temporal context carries date plus timezone only, below the cache boundary. The exact time comes from the `session_status` tool. — [OC/docs/concepts/system-prompt.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/system-prompt.md)

**Daily notes**
- `memory/YYYY-MM-DD.md` and slugged variants `memory/YYYY-MM-DD-<slug>.md` are the "working layer". They are indexed for search but never bootstrap-injected, except that today's and yesterday's notes load on a bare `/new` or `/reset` as a one-shot startup block. — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md)
- New guidance on "action-sensitive memories": a note that changes future behavior should record its condition, expiry, unlock, what to avoid, and source authority. "Memory can preserve approval context, but it does not enforce policy." — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md)

**Search (changes since the pin)**
- Search is hybrid vector plus keyword (FTS5) with recency decay and an importance multiplier. Dated files decay "at any depth" (including slugged and nested dreaming reports). `MEMORY.md`, `USER.md` and undated files are evergreen. The per-leg candidate pool was raised from 24 to **200** (up to 400 unique before MMR). Keyword matches are preserved when every ranked result falls below `minScore` (default 0.35). — [OC/docs/concepts/memory-search.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-search.md) (diff vs `d7455529`); [OC/docs/reference/memory-config.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/reference/memory-config.md)
- The default embedding provider is OpenAI; Gemini and others are configurable. 2026.9.3 made keyword search stay available when optional embeddings cannot start. — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md); [OC/CHANGELOG/2026.9.3.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.3.md)
- Trigger injection matches each inbound message with a lexical plus vector prefilter against entry triggers. A score ≥0.72 injects at most 3 entries, and only from `MEMORY.md`/`USER.md` ("a security property, not a tuning choice"). — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)
- Session transcripts are reached through a 3-tool chain: `memory_search` hits on `sessions/...jsonl` lead to `sessions_search`, and its `sessionKey`/`messageId`/`sessionId` are passed to `sessions_history`. Pre-reset history is reachable by anchor. **Changed:** session-tool visibility default is now `"all"`, where it used to be `"tree"`. — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md); [OC/docs/concepts/memory-search.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-search.md); [OC/docs/concepts/session-search.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/session-search.md)
- New: "project-scoped memory". Entries carry `<!-- project: <origin remote> -->`, and a session keeps up to 4 recently active repo keys, which boost or demote ranking and gate trigger injection. — [OC/docs/concepts/memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)

**Compaction and pre-compaction flush (changes since the pin)**
- Older turns are summarized into a transcript entry. `keepRecentTokens` defaults to 20,000. Tool-call/result pairs are never split. The full history stays on disk. — [OC/docs/concepts/compaction.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/compaction.md); [OC/docs/gateway/config-agents/heartbeat-compaction-and-streaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/config-agents/heartbeat-compaction-and-streaming.md)
- **New: summary-timeout fallback.** If the summary call times out (deadline, HTTP 408/504), compaction commits *without a summary*. It keeps the recent tail, the pending request and the previous summary, and "notes how many older messages were removed." This "gives up its identifier-retention guarantee" because otherwise "the session would stay unusable." — [OC/docs/concepts/compaction.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/compaction.md)
- **New: optional flush and compaction run after reply delivery has settled**, under a separate session owner. A new inbound message cancels them. — [OC/docs/concepts/compaction.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/compaction.md)
- **New: the flush "uses a private copy of the conversation, so its housekeeping messages never appear in later user turns."** Flush failure never blocks compaction. Sessions without writable workspace access skip the file flush. Third-party memory plugins can supply their own persistence tools. — [OC/docs/concepts/memory.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory.md)
- 2026.8.2 shipped "reject summaries that revive superseded tasks" (PR #123737, after incident #123668 on 2026-08-14, where a compacted checkpoint foregrounded an older task and the next reply resumed it). It also stopped repeated byte-triggered compaction (#127110). 2026.9.6 **retired compaction checkpoint/branch/restore controls** (#154131) while keeping summaries and history. — [OC/CHANGELOG/2026.8.2.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.8.2.md); [issue #123668](https://github.com/openclaw/openclaw/issues/123668); [OC/CHANGELOG/2026.9.6.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.6.md)

**What a heartbeat or cron run sees, and how its output reaches the conversation**
- **Changed:** "Heartbeat runs use the same system prompt as ordinary agent turns. There is no heartbeat-specific system-prompt section." Monitor scratch is appended only to the scheduled heartbeat *user message*. The default prompt's silent token changed from `HEARTBEAT_OK` to `NO_REPLY` (legacy is still accepted). — [OC/docs/gateway/heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md); [OC/docs/concepts/system-prompt.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/system-prompt.md)
- By default a heartbeat still runs in the main session. `isolatedSession: true` gives a fresh session with no history, and `lightContext: true` skips the bootstrap files. Delivery routing and conversation context "still follow the selected conversation." **New exception:** a background-command completion that belongs to a conversation continues *in that session with full context*, ignoring isolated/light settings. Event turns are rate-limited to a 30 s minimum spacing and at most 5 per 60 s. — [OC/docs/gateway/heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md)
- **Changed: the silent-outcome carryover store now also records generated `notify:true` alerts whose delivery was blocked or unconfirmed** (alert text plus delivery reason). The store keeps only the latest outcome per session; it is "not an alert history." — [OC/docs/gateway/heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md)
- Transcript markers distinguish `[OpenClaw heartbeat poll]` from exec completion, cron wake and session events. "Silent acknowledgment pairs remain hidden." — [OC/docs/gateway/heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md)
- For cron `current` jobs, the final result is committed into the bound conversation through the canonical transcript writer with job/run provenance and an idempotency key. The run counts as "delivered" only after both the external send and the session commit succeed. 2026.9.2 shipped "keep automation and heartbeat completions attached to their originating conversation and topic while retaining the isolated execution session" (#133323). — [OC/docs/automation/cron-jobs/delivery.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/automation/cron-jobs/delivery.md); [OC/CHANGELOG/2026.9.2.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.2.md)

### Inferences
- OpenClaw's layering, as of 2026-10:
  - **Always-in-prompt (budgeted):** instructions, SOUL, IDENTITY, USER (≤4k), and MEMORY (≤20k inject / ≤10k promotion) in private sessions only.
  - **Per-turn, deterministic, no model:** triggers (≤3), standing intents (≤3), silent heartbeat outcome, system events.
  - **On demand:** memory_search, sessions_search/history, Active Memory sub-agent.
  - **Offline:** dreaming (nightly), flush (pre-compaction), transcript ingestion (session end).
- Removing the heartbeat-specific system-prompt section and moving scratch into the user message both keep the system prompt byte-identical between heartbeat and chat turns. That fits their cache-prefix discipline.

### Gaps
- I did not confirm in source whether `lightContext` still yields zero bootstrap files at HEAD; the docs still say "skip workspace bootstrap files."
- I did not verify whether the isolated-cron transcript mirror and awareness events (`delivery-dispatch-awareness.ts` at the old pin) are unchanged. The current delivery docs describe the `current`-job commit path in detail but I did not re-read the isolated mirror code.

---

## 4. OpenClaw: incidents and lessons (Aug–Oct 2026)

### Takeaway
The recent pain is **silent non-promotion** and **coverage gaps in safeguards**, not dramatic
memory loss. The ranker and applier disagreed for weeks ("Ranked N, Promoted 0"). One workspace
promoted 0 of 512 entries. The flush was silently disabled on CLI backends. The superseded-task
guard covered only some compaction paths. Concurrent sandbox flushes lost appends.

### Cited Findings
- #121232 (open, 2026-08-09): the deep ranker performs no provenance check but the applier rejects `untrusted`/`system` candidates, and the two call the contamination helper with different flags. The nightly report read "Ranked 9–10 … Promoted 0" "for weeks… no warning anywhere." — [issue #121232](https://github.com/openclaw/openclaw/issues/121232)
- #164923 (open, 2026-10-04): one workspace promotes 0/512 while siblings on the same binary promote 50–90%. Raw conversation-turn snippets are rejected by filters that run before the scoring gates, "so threshold tuning is irrelevant." The issue also references an earlier fix, #68882 in v2026.4.23 (gating on total accumulated signal). — [issue #164923](https://github.com/openclaw/openclaw/issues/164923)
- #137613 (open, 2026-09-03): the pre-compaction flush is gated off on CLI backends (`if (!(memoryFlushWritable && !params.isHeartbeat && !isCli))`), so such sessions never write durable notes before compaction. The issue also links #114081: the reactive overflow path has no flush at all. — [issue #137613](https://github.com/openclaw/openclaw/issues/137613)
- #146118 (open, 2026-09-12): the #123737 superseded-task guard does not cover Codex-native compaction or non-overflow (threshold/manual) compaction. Live symptom: after compaction the assistant resumed an older task. — [issue #146118](https://github.com/openclaw/openclaw/issues/146118)
- #117741 (open, P1, 2026-08-02): two concurrent sandboxed flushes both report a successful append, but one note is lost, because the append is implemented as an unlocked read-concat-overwrite. — [issue #117741](https://github.com/openclaw/openclaw/issues/117741)
- 2026.9.2 shipped "allow memories to promote after an earlier daily claim"; 2026.9.1 shipped "deep consolidation works with explicit ownership". 2026.9.3 added "enforce bounded heartbeat and search context" and "use effective model limits for memory flush and compaction." — [OC/CHANGELOG/2026.9.2.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.2.md); [OC/CHANGELOG/2026.9.1.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.1.md); [OC/CHANGELOG/2026.9.3.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.3.md)
- 2026.9.4 shipped "Preserve first-run setup after empty dreaming sweeps" (#141808). — [OC/CHANGELOG/2026.9.4.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/CHANGELOG/2026.9.4.md)

### Inferences
- When gates sit in two places (ranker and applier) and are not shared, the result is a system that looks healthy while doing nothing. The lesson for Jarvis is to put one eligibility predicate in one place and to report "why nothing promoted" by category.
- Every safeguard OpenClaw added (flush, superseded-task audit) later turned out to be wired to only some of the code paths that actually lose context. This matches the earlier #60719 lesson: hook durability to *every* forgetting path, and test the path matrix.

### Gaps
- I found no reports in this window of dreaming *destroying* `MEMORY.md` content. The failures reported are non-promotion. Searches for "dreaming deleted" and "consolidation overwritten" returned nothing after 2026-07-01.

---

## 5. hermes-agent as one system: stores, caps, review fork, search, compaction, background work

### Takeaway
hermes is still a **small always-on core plus a searchable transcript store**:
- `MEMORY.md` (2,200 chars) and `USER.md` (1,375) are injected as a frozen per-session snapshot.
- `session_search` runs FTS5 over everything.
- A **post-turn background review fork** is the only automatic curator of memory.
- There is no nightly consolidation and no memory-file search.

Changes since August:
- Over-cap writes must now be resolved as one atomic `operations` batch.
- Staged writes are pinned to the entry the approver saw.
- The review fork can route to a cheaper model using a conversation *digest*, has a window-derived token budget, and is explicitly told to keep `USER.md` and `MEMORY.md` facts apart.
- The docs now openly say memory only pays off at **session boundaries**.
- Honcho, Mem0, Supermemory and Hindsight were moved out of the tree into a plugin catalog.
- A separate in-session `/heartbeat` (2026-08-05) exists alongside isolated cron. The earlier deep dive did not cover it.

### Cited Findings

**Stores, caps, injection**
- `MEMORY.md` is 2,200 chars (~800 tok) and `USER.md` 1,375 chars (~500 tok), both under `~/.hermes/memories/`. They are injected "as a frozen snapshot at session start", with a header gauge such as `[67% — 1,474/2,200 chars]` and `§`-delimited entries. Mid-session writes persist immediately but appear only next session; tool responses show live state. Memory "does not auto-compact." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- **Changed over-cap contract:** the error now says "Retry as ONE 'operations' batch that removes or shortens (replace) stale entries… AND adds this entry — the limit is checked only on the batch result," and returns `current_entries`. A `replace`/`remove` miss returns `closest_entries`. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- `replace` replaces the whole matched entry, and `old_text` only locates it. The fix in commit `67208caac3` (2026-09-21, #117952) "states and surfaces the whole-entry contract instead of truncating silently." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md); commit `67208caac3` in `tools/memory_tool.py` (git log, 41447a6..HEAD)
- Writes are scanned for injection/exfiltration patterns and invisible Unicode, and exact duplicates are rejected. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- `write_approval: true` stages all writes outside the interactive CLI (`/memory pending|approve|reject`). New (2026-09-24, commits `b96e88b245`, `3da1c59377`): a staged replace/remove is pinned to the exact entry the approver reviewed and is refused if that entry changed. "The background review stages these even with the gate off." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- **New explicit design statement:** "The whole memory system is built around the moment a session ends". On messaging platforms a chat is "one continuous session" that can run for weeks, so "the learning loop of forget → recall from memory → search past sessions almost never gets to fire" and "fresh memory entries also stay invisible to the running session." The recommended practice is "run `/new` at natural boundaries." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- The top-documented failure is "I told it to remember, and the next session it forgot". The most common cause is that the model *claimed* a save without calling the tool, especially models under ~30B. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)

**Background review fork (the only automatic curator)**
- After a turn, a forked agent replays the conversation and may save memory or patch/create skills. By default it surfaces `💾 Memory updated` in chat (`display.memory_notifications: off|on|verbose`). It is triggered by `memory.nudge_interval` and `skills.creation_nudge_interval`. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- **Model tier.** By default the fork runs on the *main chat model*, and the warm prompt cache makes that "cheap cache reads." It is byte-identical to the parent (system prompt, tools, reasoning effort) to keep cache parity. With `auxiliary.background_review.model` set to a different model, the fork replays "a compact **digest** of the conversation (recent turns verbatim + a summary of older ones)" at "~3–5×" lower cost. The project reports that memory capture was identical in testing. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- **Budget (changed 2026-09-18, commits `f797a23c09`, `a6ad3cc3ff`).** `max_input_tokens` caps the summed replay; when unset it is 75% of the review model's window, capped at 600,000, with a 120,000 fallback. `enabled: false` disables automatic forks, though manual `/refine` still works. "The review fork can burn a meaningful share of total tokens on busy hosts." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- The fork's default tools are memory, skill-management and read-only file tools, plus an opt-in `extra_tools` whitelist. On a managed local GPU, reviews are deferred until idle (`defer: auto`, `defer_max_age_s: 1800`) and coalesce per session. — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- Other review changes since the pin: "route memory facts to USER.md vs MEMORY.md and forbid dual-store writes" (`f02be54d73`, 2026-09-19); "scope background review memory access to its trigger" (`1571f502a9`, #105921, 2026-09-09); "cancel background review when session ends or is stopped" (`b797ed9779`, 2026-09-30). — git log of `agent/background_review.py` and `tools/memory_tool.py`, 41447a6..9dcab1e ([HM/agent/background_review.py](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/agent/background_review.py))

**Skills as procedural memory, and the curator**
- The docs position skills as the better home for recurring-task knowledge: a skill "loads only when relevant and does not compete for the 2,200-character budget." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- The curator is triggered by inactivity, not by cron: every `interval_hours` 168 (7 d) once the profile has been idle `min_idle_hours` 2. Its deterministic pass moves skills unused for 14 d to stale and 30 d to archived. Pinned skills and skills referenced by cron jobs are exempt, and never-used skills get a grace floor. The LLM consolidation pass (50–100 API calls per sweep) is **off by default** (`consolidate: false`) "because it costs aux-model tokens on every run and makes broad structural changes." — [HM/website/docs/user-guide/features/curator.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/curator.md)

**Session search**
- Session search is FTS5 over all CLI and messaging sessions in `state.db`. It returns real messages with no LLM summarization, in ~20 ms (the project's own figure). The docs' cost table shows memory at "~1,300 tokens total" fixed per session and search as "free". — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)
- New since the pin: `after`/`before` bounds with relative times (`7d`/`24h`/`2w`) and `exclude_session_ids` (2026-09-15, `5655920f9a`, `e819846b10`). Title-match and per-message content caps were added on the read shape (2026-09-18). Recent-session browsing is bounded (2026-09-03). — git log of [HM/tools/session_search_tool.py](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/tools/session_search_tool.py)

**Compaction**
- The small-window floor is still there: `_SMALL_CTX_WINDOW_LIMIT = 512_000`, `_SMALL_CTX_THRESHOLD_PERCENT = 0.75`. — [HM/agent/context_compressor.py](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/agent/context_compressor.py)
- Recent compaction fixes (2026-10-03 to 10-06): "bench a failing summary model instead of blocking compaction" and persist that deadline; "a denied tool call is not summarized as if it ran"; "keep a refused write_file's outcome in its stub"; replay dedup must preserve user-turn-first alternation; "a stall-fallback pin replaces the main route." — git log of [HM/agent/context_compressor.py](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/agent/context_compressor.py)

**External memory providers (changed packaging)**
- When a provider is active, hermes does six things:
  1. injects provider context into the system prompt,
  2. prefetches before each turn (background, non-blocking),
  3. syncs turns after each response,
  4. extracts on session end,
  5. mirrors built-in memory writes to the provider,
  6. adds provider tools.

  Providers are additive and never replace built-in memory. — [HM/website/docs/user-guide/features/memory-providers.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory-providers.md)
- The in-tree providers at HEAD are OpenViking, Holographic, RetainDB and ByteRover. **Honcho, Mem0, Hindsight and Supermemory moved to the plugin catalog.** Bundled Honcho was removed in commit `7e53b3ef82` (2026-10-02), with auto-install for existing `memory.provider: honcho` profiles. Memori is listed as an external installer. — [HM/website/docs/user-guide/features/memory-providers.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory-providers.md); [HM/plugins/memory/](https://github.com/NousResearch/hermes-agent/tree/9dcab1e440cdc482320af002e44036b947d6d709/plugins/memory)
- The comparison table lists Honcho's "dialectic user modeling", Hindsight's "knowledge graph + reflect synthesis", ByteRover's "pre-compression extraction" and Supermemory's "context fencing". — [HM/website/docs/user-guide/features/memory-providers.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory-providers.md)

### Inferences
- hermes's layering, as of 2026-10:
  - **Always-in-prompt:** SOUL/context files and memory+user at ~1,300 tok, frozen per session; plus the external-provider block.
  - **Per-turn, cheap:** provider prefetch in a `<memory-context>` fence on the user message (earlier dive).
  - **On demand:** session_search, skills (procedural, loaded on use).
  - **Background:** review fork after turns, curator weekly while idle.
  - **No nightly consolidation of memory files.**
- The "memory needs session boundaries" admission is a real architectural lesson for Jarvis, whose owner thread is also a never-ending session. A frozen snapshot plus no session ends means memory writes never reach the running context. OpenClaw solved the same problem with per-turn bootstrap refresh.

### Gaps
- I did not re-verify the default `nudge_interval` (10) in source. The value 10 appears in a user's config in #96134, and the earlier dive verified it at `41447a6`.
- I did not read `website/docs/developer-guide` for provider hook signatures.

---

## 6. hermes background runs: what cron and /heartbeat see, and how output reaches the conversation

### Takeaway
hermes now has **two background primitives with opposite context models**:
- **`/heartbeat`** (since 2026-08-05) re-enters the *current* session as a plain user turn, only when the session is idle. It sees full context, shares the cache, and has no system-prompt change.
- **cron** runs in a fresh isolated session. Its output reaches a conversation only by opt-in mirror/seed (a user-role `[Cron delivery: …]` turn), by `context_from` chaining, or by `session_search`.

A new open bug (#118863) shows the user-role mirror makes the model treat its own scheduled
message as something the human typed. That partly undercuts the earlier dive's "mirror as
user-role" recommendation.

### Cited Findings
- `/heartbeat every <interval> <prompt>` gives one heartbeat per session, minimum interval 60 s, and it "fires as a normal user turn — same conversation, same context, same prompt cache." Its rules:
  - Idle-only; missed ticks coalesce into one.
  - Queued user messages win.
  - It is "cache-safe": an ordinary user message, no system-prompt mutation, no toolset change.
  - State lives in `SessionDB.state_meta` under `heartbeat:<session_id>`, follows compression rotations, and is cleared on reset or switch.
  - It may end with `NO_REPLY`/`[SILENT]` so that nothing is sent, and it shows no typing or progress indicators.
  - A "don't-invent-work guard" in the injected prompt tells the agent to reply briefly and stop when nothing changed.

  The docs' rule of thumb: "if the recurring prompt needs the conversation's context, use `/heartbeat`. If it's a self-contained job, use cron." — [HM/website/docs/user-guide/features/heartbeat.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/heartbeat.md) (added `6518aa184e`, 2026-08-05)
- The gateway ticks the cron scheduler every 60 s and runs due jobs "in isolated agent sessions." `[SILENT]` suppresses delivery but output is still saved to `~/.hermes/cron/output/`. "Failed jobs always deliver regardless of the `[SILENT]` marker." — [HM/website/docs/user-guide/features/cron.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/cron.md)
- Continuity across runs: "Cron jobs run in isolated sessions with no memory of previous runs." `context_from` prepends another job's (or its own) most recent output, and a documented pattern is "Report only items NOT already covered in your previous run's output." — [HM/website/docs/user-guide/features/cron.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/cron.md)
- The continuable cron mirror is unchanged in shape. It is opt-in (`cron.mirror_delivery` or per-job `attach_to_session`). Thread platforms get a dedicated thread per delivery whose session is seeded with the brief. In a DM, the brief is mirrored into the DM session. Broadcasts (`all`) are never continuable. The mirror is "a labelled user turn (`[Cron delivery: <task name>]`)." — [HM/website/docs/user-guide/features/cron.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/cron.md)
- `no_agent=True` cron jobs run a script and deliver its stdout with no LLM, for watchdogs and heartbeats. — [HM/website/docs/user-guide/features/cron.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/cron.md)
- Memory in cron: "cron agents now run with memory enabled like every other agent" (ef04d846e9, resolving #9763). So the external provider's whole lifecycle (recall, per-turn retain, session-end drain) also runs for cron sessions. `skip_memory` is all-or-nothing, and #105267 (open, 2026-09-07) asks for a per-job off/tools/full policy. — [issue #105267](https://github.com/NousResearch/hermes-agent/issues/105267)
- **#118863 (open, 2026-09-22).** With the mirror on, the delivery is stored as `role=user` `"[Cron delivery: <job>]\n<text>"`. "The model therefore reads its own scheduled message as something the human typed, and answers the human as if they were an operator." The observed reply was "Noted, that's my scheduled check-in already delivered to <name>; no action needed", and the model referred to the user in the third person. A commenter traced a 4-part cascade: `gateway/mirror.py:_append_to_sqlite` passes only role and content, so `mirror=True`/`mirror_source='cron'` are discarded and `display_metadata` is always NULL; replay then cannot reconstruct provenance. — [issue #118863](https://github.com/NousResearch/hermes-agent/issues/118863)
- #113745 (closed, 2026-09-17): LLM cron output reached chat, the session mirror and another profile's Bot Chat *unredacted*. The mirror text was derived from raw content separately from the send lane, "so it would not inherit a fix at the send lane, and a transcript outlives the message." — [issue #113745](https://github.com/NousResearch/hermes-agent/issues/113745)
- #107856 (open, 2026-09-11): "Active Discord redirects miss cron context already attached to the same session." — [gh search, NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent/issues/107856)

### Inferences
- For Jarvis, the hermes history now gives a two-sided lesson on mirror role. Assistant-role mirrors broke strict-alternation providers (earlier dive). User-role mirrors with a text prefix led the model to answer as if the user had written the brief (#118863). The robust form is probably **assistant-authored content with provenance carried in text**, on providers that tolerate consecutive assistant turns (Gemini does). Alternatively, the mirror can be framed unmistakably as the agent's own earlier message.
- hermes's `/heartbeat` is the "in-thread" model that OpenClaw moved *away* from for its default heartbeat. hermes avoids the bloat by making it one prompt per session, idle-only, with no backlog and opt-in per session. That differs from Jarvis's global hourly multi-task heartbeat.

### Gaps
- No fix PR for #118863 had merged as of 2026-10-06; a contributor said a PR was planned.

---

## 7. hermes: incidents and lessons (Aug–Oct 2026)

### Takeaway
The recent hermes pain sits in **storage integrity and injection plumbing** rather than in
curation design:
- WAL orphaning silently dropped all session writes, which would also starve FTS recall.
- Gateway-mode memory was not injected.
- The mirror-role semantics went wrong (§6).
- Secrets leaked through the mirror lane (§6).
- Background-review cost needed new budgets and routing.

### Cited Findings
- #109687 (closed, 2026-09-13, v0.21.2): one short-lived CLI invocation orphaned the live gateway's `state.db` WAL generation. The gateway "keeps serving while silently dropping session writes" (state.db 1.58 GB, ~50.6k sessions / ~324.7k messages). — [issue #109687](https://github.com/NousResearch/hermes-agent/issues/109687)
- #96134 (open, 2026-08-27, v0.20.5): `USER.md`/`MEMORY.md` are not injected into the system prompt in gateway mode (Weixin) while CLI works. — [issue #96134](https://github.com/NousResearch/hermes-agent/issues/96134)
- #99524 (open, 2026-08-31): a feature request for background compaction with a splice merge, because "making users wait for a context compression op, possibly taking minutes, is painful." It references the idle-compaction work in #97390. — [issue #99524](https://github.com/NousResearch/hermes-agent/issues/99524)
- #97321 (open, 2026-08-28): a compression summary message can be inserted twice in one cycle (a race between preflight and the tool-loop tail). — [gh search result](https://github.com/NousResearch/hermes-agent/issues/97321)
- The docs warn against two agent processes sharing one Hermes home: "two writers sharing one home will compound each other's entries into state neither of them (nor you) authored." — [HM/website/docs/user-guide/features/memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)

### Inferences
- When a system's only long-term recall is FTS over one SQLite DB, silent write loss in that DB is a total memory failure that does not announce itself. Jarvis's JSONL logs plus any future index should be checked for this.

### Gaps
- I found no hermes reverts specific to memory in this window. The earlier "3-strike breaker" (#42405) still appears to be present; I did not re-verify the line.

---

## 8. Cross-system comparison: layering and budgets (October 2026)

### Takeaway
The two systems agree on the shape: a small budgeted core that is always injected, retrieval for
everything else, and background curation with provenance or approval gates. They still disagree
on **when curation happens**:
- **OpenClaw** batches it nightly, with deterministic gates and code-composed writes.
- **hermes** does it per turn, through the model under hard caps, with an optional approval stage.

They also disagree on **whether the core refreshes mid-session**: OpenClaw refreshes per turn,
hermes freezes it per session.

### Cited Findings
- **Always-injected core.**
  - OpenClaw: `USER.md` ≤4,000 chars ([user-model.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/user-model.md)); `MEMORY.md` ≤20,000 injected and ≤10,000 at dreaming promotion ([memory-budget.ts](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/extensions/memory-core/src/memory-budget.ts)); 60,000 total for all bootstrap files ([system-prompt.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/system-prompt.md)).
  - hermes: 2,200 + 1,375 chars, about 1,300 tokens ([memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)).
- **Per-turn deterministic injection.**
  - OpenClaw: triggers ≤3 at score ≥0.72; standing intents ≤3; silent-heartbeat outcome claimed once ([memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md); [heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md)).
  - hermes: external-provider prefetch only ([memory-providers.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory-providers.md)).
- **Curation cadence.**
  - OpenClaw: a nightly dreaming sweep (`0 3 * * *`) plus a pre-compaction flush ([dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md)).
  - hermes: a post-turn review fork, nudge-interval-gated, plus a weekly idle-gated skills curator ([memory.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md); [curator.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/curator.md)).
- **Background-run context.**
  - OpenClaw: heartbeat uses the same system prompt as chat, with scratch in the user message. It is optionally isolated and light. Cron omits `MEMORY.md`, and heartbeat/cron sessions never feed dreaming ([heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md); [user-model.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/user-model.md)).
  - hermes: cron is isolated but memory-enabled; `/heartbeat` runs in-session ([heartbeat.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/heartbeat.md); [issue #105267](https://github.com/NousResearch/hermes-agent/issues/105267)).

### Inferences
- Applying these to Jarvis:
  1. Exclude heartbeat-thread content from any promotion source. OpenClaw's session-kind gating cites production audits that found heartbeat noise dominating auto-captured memory.
  2. If Jarvis adds consolidation, have the model emit ops and let code compose and validate them (loss fraction, budget, hash check, pre-image), not a free rewrite.
  3. Refresh the injected core per turn, or adopt explicit session boundaries. A never-ending owner thread with a frozen snapshot is the failure hermes now documents.
  4. Put mirrored proactive sends in the transcript with unambiguous authorship. User-role-with-prefix has a documented failure (#118863).
  5. Keep one eligibility predicate and report why nothing was promoted (OpenClaw #121232/#164923).

### Gaps
- Neither project publishes per-day token or dollar costs for its background curation at default settings. hermes says only "~3–5×" savings for digest routing and gives the 600K cap; OpenClaw gives no figure.
