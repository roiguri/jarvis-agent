# Nous Research hermes-agent — Architecture Deep Dive (source-level)

**Date:** 2026-08-25. **Method:** produced by a research agent from a shallow clone of
`NousResearch/hermes-agent` @ HEAD `41447a6` plus hermes-agent.nousresearch.com docs and GitHub
issues; source preferred over docs throughout; every claim tagged VERIFIED (read directly) /
INFERRED with `path:line` or URL citations. Commissioned to answer the cross-scope-context,
memory-layer, multi-channel, and prompt/caching questions in [../PROBLEMS.md](../PROBLEMS.md), and
to verify-or-correct six claims carried from the 2026-08-06 docs-level pass; synthesis across both
reference systems lives in [../REFERENCE_ARCHITECTURES.md](../REFERENCE_ARCHITECTURES.md).

---

## Verdicts on the six prior claims

| # | Prior claim (2026-08-06, docs-level) | Verdict |
|---|---|---|
| 1 | Three-tier prompt (stable→context→volatile); ephemeral goes in user message, never system prompt; memory as frozen snapshots | **PARTLY CORRECTED.** Tiers + names VERIFIED (`agent/system_prompt.py:10-22`, parts dict `:896-900`). "Frozen snapshot" VERIFIED verbatim (`tools/memory_tool.py:11,23`). But the date/session/model/platform DO live in the system prompt's volatile tier at day granularity (`agent/system_prompt.py:877-893`) — the real invariant is *byte-stability per session/day*, not exclusion. `ephemeral_system_prompt` is a system-message suffix, not user-message content (`agent/conversation_loop.py:2388-2393`). What rides the user message's API copy is memory prefetch + plugin context + gateway notes (`agent/turn_context.py:54-86`). |
| 2 | File injection: 20k cap, 70/20 head/tail, "injection scan" | **VERIFIED with corrections.** It's *generic context-file* injection, not memory: `CONTEXT_FILE_MAX_CHARS=20_000`, head 0.7 / tail 0.2 (10% deliberately dropped) at `agent/prompt_builder.py:1503-1505`, truncation `:2279-2316`. The 20k is a **floor** under a dynamic cap (6% of window ×4 chars, ceiling 500k, `:1507-1530`). Injection scan = `_scan_context_content` (`:61-85`), replaces the whole file with a `[BLOCKED: …]` marker. A second, separate scanner blocks cron prompts (`CronPromptInjectionBlocked`, `cron/scheduler.py:5404-5424`; patterns in `tools/cronjob_tools.py`). |
| 3 | Cron in isolated sessions; cron tools disabled inside cron runs | **VERIFIED.** Fresh session `cron_{job_id}_{ts}` per run (`cron/scheduler.py:5430`). `cronjob` toolset policy-denied by default (loop prevention, gate `cron.allow_agent_scheduling`), plus `messaging` and `clarify` always denied (`cron/scheduler.py:358-389`). Nuance: memory IS enabled in cron (`skip_memory=False`, `:6093`) — the repo's own AGENTS.md:1217 says the opposite and is stale (see §6). |
| 4 | FTS5 over all session history, ~20ms, no LLM | **VERIFIED.** Corpus = `messages` table only (memory files NOT indexed). Three FTS5 tables (`hermes_state_common.py:593-712`), bm25 default rank, model-invoked `session_search` tool (`tools/session_search_tool.py:1129-1321`), no LLM in the path. ~20ms figure is the project's own docs claim (`website/docs/user-guide/features/memory.md:205`). |
| 5 | MEMORY.md ~2,200 / USER.md ~1,375 chars, "[67% full]" gauge, write-errors forcing same-turn consolidation | **VERIFIED, gauge format corrected.** Exactly 2200/1375 (`tools/memory_tool.py:178-179`). Gauge is `[{pct}% — {current:,}/{limit:,} chars]` (`:766-768`); "67%" came from a docs example. Over-cap errors instruct same-turn consolidate-and-retry (`:452-465`, `:524-536`) — plus an undocumented **3-failures-per-turn circuit breaker** that tells the model to stop and save later (`:174`, `:204-225`, issue #42405). |
| 6 | Compaction head/torso/tail: protect last ~20 msgs / ~20k tokens, summarize middle at 50% of window | **CORRECTED.** Vocabulary is head/middle/tail ("torso" NOT FOUND). Trigger default 0.50 but a raise-only **75% floor for windows <512K** (`agent/context_compressor.py:1233-1241`, `3043-3058`) — most models compact at 75%. `protect_last_n=20` exists but the compaction cut clamps the message floor to **8** (`:1214`, `:6342`); the tail is token-budgeted at `threshold × 0.20` (`:2338`) → 30K on a 200K model, 64K on 400K — "~20k" traces to a stale config comment (`cli-config.yaml.example:531`). |

---

## 1. Proactive-message → conversation path (the Jarvis gap)

**Default: full isolation, gap accepted.** A cron run executes in a fresh session
(`cron_{job_id}_{ts}`), its final response is auto-delivered by the scheduler ("do NOT use
send_message… the system handles the rest", `cron/scheduler.py:4353-4364`), and the delivery does
**not** touch any chat session transcript — "Default OFF — preserves the historical isolation
guarantee (cron deliveries live only in the cron job's own session, never the target chat's
history)" (`_cron_mirror_delivery_enabled`, `cron/scheduler.py:1600-1637`). A user reply to a
default delivery lands in a chat session that has never seen the brief — the documented "what is
Task #2?" amnesia (`:1625`). VERIFIED.

**Bridge 1 — transcript mirror (opt-in):** per-job `attach_to_session: true` (set via the
`cronjob` tool) or global `cron.mirror_delivery: true`. The clean brief is appended to the **origin
chat's** session via `gateway.mirror.mirror_to_session` — the same primitive interactive
`send_message` uses (`:1622-1627`, `1673-1741`). Two load-bearing details:

- Mirrored as a **user-role** message prefixed `[Cron delivery: {job name}]`, *not* assistant-role
  — an assistant mirror produced assistant→assistant pairs that broke strict-alternation
  providers; a user-role mirror collapses safely via consecutive-user merge, and the prefix
  preserves provenance because mirror metadata is dropped at the SQLite boundary (`:1708-1724`;
  `gateway/mirror.py:54-61`). VERIFIED.
- Mirroring is scoped to the **origin conversation only** — fan-out/broadcast/home-channel targets
  are never mirrored ("they are broadcasts, not a continuation of a conversation",
  `_target_matches_origin`, `:1640-1670`). VERIFIED.

**Bridge 2 — continuable cron (the flagship mechanism):** when mirroring is on and the gateway is
live, thread-capable platforms get a **dedicated thread per delivery**: the scheduler calls
`adapter.create_handoff_thread` to open "Hermes — {task name}" (`_open_continuable_cron_thread`,
`:1743-1777`), delivers the brief into it, then **creates the thread-keyed session row and seeds it
with the brief** (`_seed_cron_thread_session`, `:1780-1900`) — computing the exact session key the
user's future reply will resolve to (chat_type, scope_id, Discord's thread-id-as-chat-id quirk all
reproduced byte-identically). "The seed IS the feature — a continuable flat brief without its seed
is a brief the next reply can't see" (`:1608-1614`). A flat `in_channel` variant seeds the
whole-channel session instead (`_seed_cron_channel_session`, `:1903-2011`), gated on an adapter
capability bit `supports_inchannel_continuable` (`gateway/platforms/base.py:2986`). Seed failures
log at WARNING because "a silent seed failure IS the continuation-amnesia bug (Alice 2026-08-19)"
(`:1894-1900`). VERIFIED. Docs: hermes-agent.nousresearch.com/docs/user-guide/features/cron.

Crucially, the reply-continuation session is a **normal gateway chat session containing only the
brief text** — the cron run's own transcript (tool calls, reasoning) is *not* imported. VERIFIED
(nothing in the seed path carries more than `mirror_text`).

**Bridge 3 — FTS search as backstop:** cron sessions persist in the same `state.db`
(`session_id=_cron_session_id, session_db=_session_db` passed to `AIAgent`,
`cron/scheduler.py:6096-6098`), titled `cron {job_id} …` (`:85-120`), so the chat agent can pull
the full cron transcript on demand via `session_search` — though cron/automation sessions are
deliberately **demoted below interactive ones** in result ranking
(`tools/session_search_tool.py:281-296`, #19434). No automatic injection; the model must choose to
search. VERIFIED.

**Bridge 4 — per-job continuity without sessions:** `context_from: "self"` injects up to 8,000
chars of the job's own previous output into the next run's prompt ("avoid repeating what was
already reported… without touching session history", `cron/scheduler.py:4271-4334`); the per-job
**notepad** (SQLite KV, 16KB/value, 64KB/job, written via CLI, rendered into every wake-up's
prompt) carries cursors/watermarks (`cron/notepad.py:1-38`). VERIFIED.

**Related non-cron wake path:** background completions (async delegation, kanban) don't deliver
out-of-band — they inject a synthetic `MessageEvent(internal=True)` into the **existing** session
so the agent runs a real turn with full history (`gateway/wake.py:1-94`); stateless surfaces get a
self-POST with the raw session id so the wake resumes the real session. VERIFIED. And CLI→gateway
**handoff** re-binds a destination channel's session key to the existing `session_id` via
`switch_session`, moving the whole transcript across surfaces (`gateway/run.py:13610-13862`).
VERIFIED.

**Answer to the brief's core question:** the gap is *acknowledged and closed by opt-in seeding of
the reply surface with the delivered text only* — not by importing cron context, not by
prompt-injected cross-scope slices (hermes has no equivalent of Jarvis's injected log slices), with
FTS search as the manual deep-recall path. Delivery-as-user-role-message is the keystone trick.

---

## 2. Memory layer — full lifecycle

**Stores.** `~/.hermes/memories/MEMORY.md` (agent notes, cap 2,200 chars ≈800 tok) and `USER.md`
(profile, 1,375 ≈500 tok), entries `\n§\n`-delimited, atomic temp+rename writes, sidecar file locks
(`tools/memory_tool.py:64-908`). Optional external providers (mem0, honcho, etc. — plugins only,
closed set per `AGENTS.md:858-865`). Session transcripts in `state.db` are the third pillar. There
are NO per-session note files. VERIFIED.

**Writers & cadence.** (1) The model, via one `memory` tool with `add`/`replace`/`remove` + atomic
`operations` batch (`tools/memory_tool.py:1086-1174`, `586-693`). (2) An automatic **background
review fork**: every 10 user turns (config `nudge_interval`; counter resets on any memory-tool
call) the agent forks *after the reply is delivered* and asks itself "should any skill/memory be
saved?" — writes go straight to the stores, optionally staged behind `/memory approve`
(`agent/background_review.py:1-17`, `:442-451`; trigger `agent/turn_context.py:738-746`; ~30K
tok/event, skipped for cron `cron/scheduler.py:6094`). (3) User CLI (`hermes journey edit/delete`,
`memory reset`). VERIFIED. (Note: `agent/curator.py` is the *skills* curator, not memory.)

**Cap mechanics.** The cap is whole-file (`len(delimiter.join(entries))`). Over-cap `add` returns
an error carrying the full current entry list + usage and instructs: consolidate via
replace/remove "then retry this add — all in this turn" (`tools/memory_tool.py:452-465`). After
**3 consolidation failures in one turn** the tool returns a terminal "stop retrying… save in a
later turn" (`:174`, `:204-225`). Success responses deliberately omit the entry list (anti-thrash)
but include the gauge. VERIFIED.

**FTS5.** Corpus = `messages` rows only, one FTS row per message (no chunking): `messages_fts`
(unicode61, external-content, `content/tool_name/tool_calls`), a trigram table for CJK **excluding
tool rows** (tool rows ≈90% of bytes; trigram index ≈2.6× text size), optional loadable-tokenizer
CJK bigram table (`hermes_state_common.py:593-726`, `hermes_state.py:3413-3469`). bm25 default,
`sort=newest|oldest` puts time first with rank tiebreak; query sanitization + LIKE fallback ladder.
Tool = `session_search`, four shapes (discovery / scroll / read / browse) with adaptive hydration:
top hit gets ±5-message window + first/last-3 bookends, lower hits get the anchor only
(`tools/session_search_tool.py:755-933`, `hermes_state_search.py:976-1009`). Ops care: incremental
FTS merges instead of `optimize` (9-18s stalls measured on a 10GB DB,
`hermes_state_search.py:2441-2510`). VERIFIED.

**Prompt entry.** Memory blocks render in the **volatile tier** (skills index → MEMORY → USER →
provider block → timestamp; `agent/system_prompt.py:817-894`) as a frozen snapshot:
`_system_prompt_snapshot` captured at load, `format_for_system_prompt()` never shows live state
(`tools/memory_tool.py:190-264`, `706-717`); refreshed only at session start and post-compaction
`invalidate_system_prompt()` (`agent/system_prompt.py:932-941`). Mid-session writes hit disk
immediately but appear next session — cache preservation is the stated reason. No separate
injection budget: the store caps ARE the injection budget (~1,300 tok total). External-provider
recall is appended to the **API copy of the user message** inside a `<memory-context>` fence with
an explicit "NOT new user input" note and fence-forgery scrubbing
(`agent/memory_manager.py:347-362`, `163-182`; `agent/turn_context.py:55-86`). VERIFIED.

---

## 3. Sessions and history

**Session model.** Single creation entry `SessionStore.get_or_create_session` (single-flight per
key; ids `YYYYMMDD_HHMMSS_<8hex>`; recovery of accidentally-ended sessions before creating fresh —
`gateway/session.py:2598-2894`, `hermes_state_common.py:212-224`). Keys are per **conversation
lane**: `agent:<profile>:<platform>:<chat_type>[:scope][:chat][:thread][:user]`
(`gateway/session.py:1090-1211`) — groups per-user-isolated by default, threads shared. Lifetime:
`SessionResetPolicy` default **`none`** since July 2026 (docs table saying "both (default)" is
stale — `gateway/config.py:549-564` vs `docs/session-lifecycle.md:306`); daily/idle modes exist
with a 300s expiry watcher. Concurrency: unlimited active sessions by default; 128-entry agent LRU
with 1h idle TTL and memory-pressure eviction; per-session turn leases serialize turns. End reasons
form a typed taxonomy (`session_reset`/`idle`/`daily`/`compression`/…, first reason wins). VERIFIED.

**Compaction.** Head (system prompt + first-3, decaying to system-only after first compaction) /
middle (summarized) / tail (protected). Trigger: 50% of input budget, raised to **75% for windows
<512K**, with cooldown/backoff/ineffectiveness anti-thrash gates
(`agent/context_compressor.py:3043-3579`). Tail: token budget = threshold×0.20 (1.5× soft ceiling
so cuts never split a tool result), message floor clamped to 8; opt-in `tail_mode: lean` = 2.5% of
window clamped [10K, 25K]. Summary: one aux-model call → structured markdown checkpoint (Goal /
Constraints / Completed Actions / Active State / Blocked / Key Decisions / Errors & Fixes with
quoted user corrections / Relevant Files / Critical Context), **temporally anchored to past tense**
so a resumed agent doesn't redo work, **credentials force-redacted** at the boundary;
re-compaction *merges into* the previous summary rather than starting over; capped at min(5%
window, 10K tok) (`:4848-4941`, `:2345-2351`). Storage: the summary is a transcript message
(`[CONTEXT COMPACTION — REFERENCE ONLY]` + metadata flags); previous summary rehydrated by scanning
the transcript. VERIFIED.

**Searchability after compaction — explicit design guarantee:** in-place compaction archives
replaced rows as `active=0, compacted=1`; `search_messages()` includes `compacted=1` rows by
default and the FTS triggers don't key on those flags, so **compacted-away material stays fully
searchable** while live-context loads filter `active=1` (`hermes_state.py:11191-11241`,
`hermes_state_search.py:1745-1751`, `:1805`). VERIFIED.

**Persistence.** SQLite `state.db` canonical (WAL, schema v26; 50+-column `sessions`, `messages`
with `api_content` sidecar + `active`/`compacted`); the documented "JSONL fallback" doesn't exist —
overflow spools atomic per-message JSON for replay (docs correction;
`gateway/session.py:3663-3705`). Retention: **nothing deleted by default** — auto-prune (90d) and
auto-archive both opt-in and off. VERIFIED.

---

## 4. Multi-channel

**Channels.** `Platform` enum: ~24 built-ins + 22 bundled plugin platforms (**Telegram, Discord,
Slack, WhatsApp are plugins**, e.g. `plugins/platforms/telegram/adapter.py:10961-10980`) + a
`relay` meta-platform fronting N logical platforms behind one adapter with a wire-negotiated
`CapabilityDescriptor` (`gateway/relay/descriptor.py:41-95`). CLI/TUI/desktop/webui are non-adapter
"surfaces" sharing the session machinery. VERIFIED.

**No shared spine.** Platform is a hard component of the session key → one session per
channel-lane. Cross-channel continuity is explicit only: **handoff** (rebind `session_id` to the
destination key + synthetic internal turn), **mirroring** (write the sent text into the target
session), or a local viewer resuming a gateway session. The durable identity is `session_id`;
`session_key` is a rebindable per-channel routing handle. VERIFIED.

**Proactive target choice.** Grammar `origin | local | <platform> | <platform>:<chat>[:<thread>]`
(`gateway/delivery.py:230-279`); `deliver=all` expands to every platform with a configured **home
channel** at fire time; origin-less `origin` falls back to the first home channel (home channels
are config.yaml-canonical, set by `/sethome`). No recency-based "last active channel" logic exists.
Robustness: persistent dead-target registry (self-healing on next success,
`gateway/dead_targets.py`), durable delivery-obligation ledger with honest at-least-once
`RECOVERED` markers (`gateway/delivery_ledger.py:1-112`), silence-narration filter breaking
bot-to-bot loops, and a periodically rebuilt **channel directory** (adapter `list_channels()` →
session-origin fallback, + user alias overlay) that the model consults for name→id resolution
(`gateway/channel_directory.py`). VERIFIED.

**Capability abstraction — three layers:** declarative `PlatformEntry` (registry metadata:
`max_message_length`, `pii_safe`, `platform_hint`, cron-deliver env,
`gateway/platform_registry.py:62-229`); runtime `BasePlatformAdapter` flags/methods
(`supports_code_blocks`, `supports_async_delivery`, `splits_long_messages`,
`supports_inchannel_continuable`, per-chat length fns, `format_message`, fence-aware chunking —
`gateway/platforms/base.py:2890-3285`); relay `CapabilityDescriptor` on the wire. **Buttons have no
boolean flag** — base ships numbered-text-list fallbacks for `send_clarify`/`send_slash_confirm`
and button-capable adapters override (`base.py:4233-4340`). **What the model is told** is prose
only: per-platform `PLATFORM_HINTS` (markdown dialect, `MEDIA:` tag semantics, quirks — including
negative capability statements like CLI's "do NOT emit MEDIA tags";
`agent/prompt_builder.py:871-1141`) + a per-session context block (source, multi-user warning,
honest "you do NOT have Slack API access" branches, connected platforms, home channels, deliver
options; `gateway/session.py:482-748`). Length limits and buttons are handled mechanically below
the model (INFERRED from absence). Per-platform **verbosity tiers** in `gateway/display_config.py`
(Tier 1 editing+personal … Tier 4 batch) govern progress/reasoning display. VERIFIED.

---

## 5. Prompt assembly, caching, cost

**Tiers.** stable (SOUL identity + all behavioral guidance + model-specific ops guidance) →
context (live git/workspace snapshot, caller system message, AGENTS.md-style context files) →
volatile (skills index deliberately FIRST in the band, memory blocks, plugin sections, then
date-granularity timestamp + session/model/provider/platform lines)
(`agent/system_prompt.py:340-900`). Tier membership is dynamic: env/platform/profile hints slide
from stable to context when a workspace snapshot exists (`:602-606`). The whole system prompt is
built once per session, persisted, restored verbatim on continuation; rebuild triggers are new
session, post-compaction invalidation, staleness (`agent/conversation_loop.py:995-1041`).
AGENTS.md lists "reload memories or rebuild system prompts mid-conversation" as a forbidden
invariant (`AGENTS.md:1344-1357`). VERIFIED.

**Caching.** Anthropic-style: 4 `cache_control` breakpoints — static stable prefix, end of system,
last 2 messages — applied on the wire only, after all transcript mutation
(`agent/prompt_caching.py:1-8`, `170-229`, `479-499`); a 5th tools-array layout on direct
api.anthropic.com. A large per-provider policy table decides marker emission and layout (native vs
envelope) covering Anthropic/OpenRouter/Nous/Kimi/MiniMax/Qwen/LiteLLM
(`agent/agent_runtime_helpers.py:2201-2545`); TTL `5m`/`1h` with per-provider clamps.
`prompt_cache_key` derived from a **rotation-stable compression-lineage root** so compaction
doesn't cold-start the cache (`agent/prompt_cache_scope.py`), with cron's per-fire timestamp
stripped so all fires of one job share a cache scope (`agent/transports/codex.py:19-28`). Builders
register byte-exact stable prefixes of skill/cron scaffold user-messages so even user-message
scaffolds cache (`agent/prompt_cache_boundary.py`). **Gemini: no explicit context-cache API usage
anywhere** — no markers emitted; the sole Gemini strategy is prefix-ordering for implicit caching
(why skills lead the volatile band) plus `cachedContentTokenCount` accounting
(`agent/gemini_native_adapter.py:776-783`). Gateway keeps a 128-agent LRU warm specifically to
preserve prefix caches, evicted under cgroup memory pressure with the 8 MRU sessions protected
(`gateway/agent_cache_pressure.py`). VERIFIED.

**Cost governance.** Cron model precedence: per-job → `cron.model` fleet default (deliberately
beats the chat model so `/model` switches don't leak into unattended jobs) → env → global
(`cron/scheduler.py:5621-5691`). Cron cost avoidance: no background review (~30K tok/event), no
auto-titling, pre-dispatch preflight so a misconfigured job "never burns an LLM call". Aux tasks
(compression, titles, review) route to a cheap-model ladder with hardcoded fallbacks
(haiku/flash-class, `agent/auxiliary_client.py:911-977`). Budgets: background-review input-token
budget 600K (motivated by one review replaying 1.49M tokens), opt-in wall-clock run budget with an
80% wrap-up notice injected into a *tool* message to preserve alternation+cache, iteration budgets
with refunds for code-exec. **No client-side USD cap exists** (per turn/day/job) — spend control is
entirely model choice + caps + cache preservation; dollar caps are server-side (Nous) only
(INFERRED from exhaustive grep + `docs/billing-lifecycle.md`). VERIFIED.

---

## 6. What hurts (pain points)

- **Session fragmentation + replay token waste** —
  [#5563](https://github.com/NousResearch/hermes-agent/issues/5563): 15 sessions of one
  conversation consumed ~1.9M tokens where ~190K sufficed (89% waste); one 12-hour day burned a
  monthly API budget. VERIFIED (issue).
- **state.db corruption under concurrent WAL writers** (CLI + gateway + subagents): malformed
  B-trees, 18 sessions lost, session_search disabled — losing search "loses all long-term recall"
  ([#5563](https://github.com/NousResearch/hermes-agent/issues/5563),
  [#32156](https://github.com/NousResearch/hermes-agent/issues/32156)). VERIFIED (issue).
- **Memory caps too small for complex projects**; over-cap write failures loop and add noise
  (hence the in-code 3-strike breaker, #42405)
  ([#5563](https://github.com/NousResearch/hermes-agent/issues/5563),
  [#32156](https://github.com/NousResearch/hermes-agent/issues/32156)). VERIFIED.
- **Late/fragile compaction**: 50% trigger + 20-message protection too permissive; aux compression
  provider can be unavailable leaving sessions stuck; tool-output retention (50KB/call) dominates
  prompts ([#32156](https://github.com/NousResearch/hermes-agent/issues/32156)) — the 75%
  small-window floor and prune passes are visible in-code responses. VERIFIED.
- **Continuation amnesia was a live production bug class**: three named "Alice" incidents
  (2026-08-19/20) where seeds landed in the wrong session-key lane (DM-vs-thread chat_type, missing
  scope_id, origin-scan bail on populated chats) — each fixed by making the seed reproduce the
  reply's key byte-identically (`cron/scheduler.py:1802-1814`, `1922-1942`;
  `gateway/mirror.py:47-53`). Cross-channel send amnesia ("send what?") remains open:
  [#33530](https://github.com/NousResearch/hermes-agent/issues/33530). VERIFIED.
- **Strict-alternation breakage from assistant-role mirrors** (#2221/#2313) — the reason briefs
  mirror as user-role. VERIFIED (code comments).
- **Cross-session recall demand**:
  [#8457](https://github.com/NousResearch/hermes-agent/issues/8457) asks for per-thread session
  files + tiered aging compression, still `needs-decision`. VERIFIED (issue).
- **Doc drift inside the project itself**: AGENTS.md:1217-1222 still claims cron passes
  `skip_memory=True` and "cron deliveries are not mirrored" — both contradicted by shipped code
  (`cron/scheduler.py:6093`, `:1600-1637`); session-lifecycle.md's default-reset table and the
  iteration-budget docstrings (500/50 vs actual maxsize/250) are also stale. VERIFIED.
- **Environment hallucination after ~700K tokens** (model claims it's in a cloud sandbox on a
  local box) ([#5563](https://github.com/NousResearch/hermes-agent/issues/5563)). VERIFIED
  (issue).

---

## Top 10 transferable mechanisms for Jarvis

Ranked by combined relevance to (a) cross-scope context, (b) memory layer, (c) multi-channel.

1. **Seed-the-reply-surface pattern (a).** Mirror a heartbeat notification into the *user chat
   thread's own history* as a labeled user-role message (`[Heartbeat: task]…`) at delivery time.
   Jarvis already injects a notification *slice* into the system prompt; hermes shows the sturdier
   shape: put the delivered text in the transcript itself, user-role to keep alternation safe, at
   a turn boundary so caching survives (`cron/scheduler.py:1673-1741`). This makes the bridge
   durable (survives the day boundary, unlike Jarvis's today-only slice) and costs zero prompt
   budget on turns that don't need it.
2. **User-role + provenance-prefix for cross-scope imports (a).** Hermes's hard-won rule — never
   mirror as assistant, always prefix provenance because metadata dies at the storage boundary
   (`gateway/mirror.py:54-61`) — applies directly to Jarvis's LangGraph threads and to any future
   cross-thread context injection (#50).
3. **FTS5 over the shared transcript store as the deep-recall backstop (a,b).** Jarvis has NO
   memory search; hermes's `session_search` (bm25, adaptive hydration: full window + bookends for
   the top hit only, anchor-only for the rest; automation sessions demoted below interactive) is a
   complete, LLM-free design to copy over `chat_history.jsonl`/SQLite
   (`tools/session_search_tool.py`, `hermes_state_common.py:593-712`). Note the failure mode to
   avoid: it's their *only* long-term recall, and DB corruption kills it — keep Jarvis's
   prompt-injected slices as the always-on layer.
4. **Hard caps + gauge + same-turn consolidation for memory files (b).** Jarvis's
   MEMORY.md/USER.md have no size discipline. Adopt: whole-file char cap, a
   `[{pct}% — n/limit chars]` gauge in the injected block, over-cap writes rejected with the
   current entries echoed + "consolidate then retry this turn", and the 3-strike stop-retrying
   breaker (`tools/memory_tool.py:452-465`, `:174`). This converts memory rot into a
   self-maintaining loop.
5. **Frozen-snapshot memory injection (b).** Render memory once per session/turn-window and let
   tool responses show live state, keeping the prompt prefix byte-stable — directly relevant to
   Gemini, where hermes's *only* cache strategy is prefix ordering for implicit caching (stable
   content first, volatile last, date-granularity timestamps) (`tools/memory_tool.py:11-23`,
   `agent/system_prompt.py:805-816`, `849-861`). Jarvis re-reads all memory files every turn;
   ordering the assembled prompt stable→volatile and coarsening the time envelope would directly
   improve Gemini implicit-cache hits.
6. **Per-task notepad + `context_from: self` for heartbeat continuity (a,b).** Jarvis's heartbeat
   NOTES files are free-form agent markdown; hermes splits machine state (KV notepad, byte-capped
   because it's prompt-injected every run) from "your previous run's output" injection (8K cap)
   for dedupe/continuity without session history (`cron/notepad.py:1-38`,
   `cron/scheduler.py:4271-4334`). A size-capped, prompt-injected "last tick output" block would
   sharpen Jarvis's already-handled detection.
7. **Compaction summaries stored in-transcript + archived-but-searchable originals (b).** If
   Jarvis ever softens the hard 50-message trim: hermes's structured checkpoint (temporal
   anchoring to past tense; quoted user corrections; iterative merge on re-compaction) and the
   `active=0, compacted=1` flags that keep pruned turns in FTS
   (`agent/context_compressor.py:4848-4941`, `hermes_state.py:11209-11216`) are the two design
   decisions worth stealing wholesale.
8. **Channel-capability via prose hints + adapter flags, buttons via override-with-text-fallback
   (c).** For Jarvis's gateway generalization: a per-channel `platform_hint` string injected into
   the prompt (formatting dialect, media semantics, *negative* capabilities) plus mechanical
   adapter flags (`splits_long_messages`, `supports_async_delivery`) below the model, and
   interactive blocks defaulting to numbered-text fallbacks any channel can satisfy
   (`agent/prompt_builder.py:871-1141`, `gateway/platforms/base.py:4233-4340`) — a validated shape
   for Jarvis's blocks layer as channels multiply.
9. **Home-channel + dead-target + delivery-ledger triad for proactive sends (c).**
   Config-canonical default destination per channel, a self-healing dead-target registry, and a
   durable at-least-once obligation ledger with honest `RECOVERED` markers
   (`gateway/delivery.py`, `dead_targets.py`, `delivery_ledger.py`) — the minimal robustness kit
   Jarvis's Outbox will want once heartbeat can choose between Telegram and the app.
10. **Loose autonomous-silence contract shared across lanes (a).** Hermes recognizes
    `[SILENT]`/`NO_REPLY` whole-response, first-line, or last-line — but never mid-sentence — via
    one matcher shared by cron and webhook lanes so they can't drift
    (`gateway/response_filters.py:73-111`, `cron/scheduler.py:568-582`). Jarvis's `[NO_ACTION]`
    contract should adopt the tolerant matcher + the shared-single-matcher discipline (and
    hermes's rule that only *successful* runs may be silenced).

**One meta-lesson:** hermes chose per-channel session fragmentation + opt-in bridges and has spent
2026 patching amnesia bugs one session-key lane at a time; Jarvis's single owner-thread-per-channel
with prompt-injected cross-scope slices avoids that entire bug class — the highest-value imports
are the transcript-seeding pattern, FTS backstop, and memory caps, not the session model.
