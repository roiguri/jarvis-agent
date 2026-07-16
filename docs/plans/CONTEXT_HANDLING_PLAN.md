# Context Handling Roadmap

**Issue:** #33 (archive) = #18 (origin mirror) — "Reduce token spend" umbrella. Also touches archive #54 (compaction).
**Date:** 2026-07-09.
**Inputs:** 14-day telemetry baseline (`observability/usage.py`), OpenClaw docs + source dive (see [HEARTBEAT_GATING_PLAN.md §2](archive/HEARTBEAT_GATING_PLAN.md)), Nous Research `hermes-agent` docs, archive issue #54 staged analysis.
**Companion:** [HEARTBEAT_GATING_PLAN.md](archive/HEARTBEAT_GATING_PLAN.md) — Workstream 1 below; shipped & verified 2026-07-13, plan archived.

---

## Manager summary

**Problem.** Jarvis's context handling is structurally expensive and has no recall beyond exact-name file reads. Measured over 14 days: 27.1M input tokens / $1.72, of which **72% went to heartbeat ticks that decided to do nothing** (295 no-op ticks × ~66k input tokens × ~3.7 LLM calls each). Cross-turn prompt caching is effectively zero because the system prompt's first bytes are a per-minute timestamp — the 25–36% measured cache hits come only from calls *within* the same turn. Memory recall requires knowing a file's exact name; nothing is searchable. History beyond the 50-message window is simply gone unless the agent proactively saved it.

**Plan.** Seven workstreams, ordered by measured impact and risk. WS1 is done (shipped & verified 2026-07-13); WS2–4 are small `agent.py`-centric changes that compound with it; WS5–6 are capability wins borrowed from `hermes-agent`; WS7 stays parked until evidence demands it.

| # | Workstream | Attacks | Size | Expected effect |
|---|---|---|---|---|
| WS1 | Heartbeat gating + windows + self-authoring | the 84% (heartbeat spend) | 9 phases, **done 2026-07-13** | Most hours: no LLM call at all; remaining ticks carry 1–2 task blocks, not 8 — verified in production |
| WS2 | Cache-stable prompt layout | every remaining call, both scopes | small | Cross-turn cache hits on the stable prefix; measure via `cache_read_tokens` |
| WS3 | Heartbeat light context | per-tick input | small | Tick input 66k → target ≤15k |
| WS4 | Bootstrap context budget | unbounded prompt growth | small | Backstop: caps injected file copies, files intact on disk |
| WS5 | Memory & history search tool | recall capability | medium | "What did we discuss last week?" answerable without bloating context |
| WS6 | Memory size pressure | long-term prompt creep | small | USER.md/MEMORY.md stay curated instead of growing forever |
| WS7 | Conversation compaction | 50-message window loss | parked | Only if archive #54's revisit-triggers appear |

**Sequencing.** WS1 is done (all 9 phases). WS2 can land any time — it's independent and benefits everything. WS3 is now unblocked (WS1 Phase 6 shipped). WS4 whenever convenient. WS5/WS6 are a capability track, schedulable independently. WS7 waits for evidence.

**Risk posture.** Everything except WS7 is additive or a reordering; each workstream is independently shippable and revertable, verified against telemetry (`turns.jsonl` records input/output/cache-read per turn, so every claim here is checkable after deploy).

---

## Measured baseline (14 days to 2026-07-09)

From `observability.usage` over `turns.jsonl`:

- **Total:** 408 turns, 1,461 LLM calls, 2,777 tool calls, 27.1M input (34% cache-read), 682.6k output, $1.72.
- **Heartbeat scope:** 335 turns, 22.8M input, $1.44 (84% of spend), 296 `[NO_ACTION]` (88%). No-op tick ≈ 66.3k input / 3.7 LLM calls; acting tick ≈ 79.5k / 4.3.
- **User scope:** 73 turns, 4.3M input, $0.28, ~59k input per turn, 25% cache-read.
- **Cache interpretation:** the `[Current time: … HH:MM …]` envelope is the first line of every system prompt (`agent.py:336-339`) and changes per minute, so no cross-turn prefix survives; the measured 25–40% cache reads are consecutive calls *within* one turn sharing a same-minute prompt.

Absolute cost is small (~$3.7/month on `gemini-3-flash-preview`) — the point is structural: the same architecture on a stronger model, or with more heartbeat tasks, scales linearly with the waste. Latency and answer quality (less noise in context) benefit too.

---

## Research digest: what the references do

Full OpenClaw source-dive lives in [HEARTBEAT_GATING_PLAN.md §2](archive/HEARTBEAT_GATING_PLAN.md). The context-relevant mechanisms across both references, against Jarvis today:

| Mechanism | OpenClaw | hermes-agent | Jarvis today |
|---|---|---|---|
| Cache-stable prompt | Explicit stable-prefix/dynamic-suffix boundary; **timezone only, no live clock** in prompt (time via tool) | Three tiers (stable → context → volatile); ephemeral material goes in the **user message**, never system prompt; memory injected as **frozen snapshots** | Timestamp is the first line, rebuilt per call — cross-turn cache ≈ 0 |
| Injected-file budgets | 20k chars/file, 60k total, truncation warnings; file intact on disk | 20k chars, 70/20 head/tail split + injection-scan | Uncapped (SOUL, USER, daily logs, HEARTBEAT.md) |
| Heartbeat cost | Only-due-task injection, skip-if-nothing-due, `lightContext` (~100k → 2–5k), `isolatedSession` | Cron jobs in isolated sessions; **cron tools disabled inside cron runs** | Full prompt + all 8 tasks every hour (→ WS1/WS3) |
| Memory recall | `memory_search`: hybrid vector + BM25 over chunked files | Bounded in-context memory + **FTS5 full-text search over all session history** (~20ms, no LLM) | `list_memory` + `read_memory` by exact name; no search |
| Memory size control | Curated MEMORY.md + opt-in "dreaming" consolidation | **Hard caps** (MEMORY.md ~2,200 chars, USER.md ~1,375) + `[67% full]` gauge in prompt + write-errors that force same-turn consolidation | No size pressure anywhere |
| History compaction | Summarize-old near context limit + **pre-compaction memory flush** (silent save-to-memory turn); separate lighter **tool-result pruning** (head+tail kept, cache-TTL-aware) | Head/torso/tail: protect last ~20 messages / ~20k tokens, summarize the middle at 50% of window | Hard trim at 50 messages (`_add_and_trim`), older context discarded |

---

## WS1 — Heartbeat gating, windows, self-authoring

**Done — shipped 2026-07-10..13, verified in production; plan archived.** Fully specified in [HEARTBEAT_GATING_PLAN.md](archive/HEARTBEAT_GATING_PLAN.md) (9 phases, incl. the Hermes-derived create-in-heartbeat-scope guard and ack-primary delivery). Attacked the 84%; see the archived plan’s checkpoint block for the verification evidence. Every other workstream compounds with it.

---

## WS2 — Cache-stable prompt layout (#33 Lever 4, mostly a reordering)

**Why.** Gemini implicit caching is prefix-based and automatic — no API changes needed, just byte-stable leading content across calls. Today the very first line guarantees a miss every minute. Jarvis prompts (~60k tokens) are far above the implicit-caching minimum, so the only blocker is layout.

**Design** — all in `build_system_prompt` (`agent.py:320-369`):

1. **Reorder stable → volatile.** New assembly order:
   - *Stable prefix:* `[Active scope: …]` → SOUL.md → AGENTS.md → USER.md → scope framing (+ `prompts/heartbeat.md` for heartbeat) → skill list.
   - *Volatile tail:* HEARTBEAT.md / due-task blocks (heartbeat), daily log, live chat/notification slices, and **`[Current time: …]` as the last line**.
2. **Move the clock to the tail.** Everything after the first changed byte is uncached; with the clock last, a minute rollover invalidates one line instead of the whole prompt. (OpenClaw goes further — timezone-only in prompt, time via tool — but Jarvis's reminder/heartbeat reasoning leans on the clock; tail placement keeps it with near-zero cache cost.)
3. **Keep hot-reload.** Unlike Hermes's frozen snapshots, per-turn re-reads are fine: SOUL/AGENTS/USER change rarely, and when they do the cache miss is deserved. The skill list changes on activation — also fine, it invalidates only from that point down.

**Ordering note.** The current order (time first, identity, then per-scope content) was chosen for prompt readability; nothing in `prompts/AGENTS.md` depends on section position. Verify with one manual read-through of an assembled prompt after reordering.

**Verify.** `cache_read_tokens / input_tokens` in `turns.jsonl` before/after, per scope. Expect the intra-turn-only ~34% to rise substantially on consecutive same-scope turns; heartbeat (hourly, stable prefix) should show near-full prefix hits once WS1 Phase 5 shrinks the variable tail.

**Risk.** Low — content is unchanged, only order. One-commit revert.

---

## WS3 — Heartbeat light context (OpenClaw `lightContext` analog)

**Why.** A tick doesn't need Jarvis's full conversational identity to check whether a class ended. OpenClaw measured ~100k → 2–5k for the same idea; our no-op tick is 66k.

**Design.** In `build_system_prompt("heartbeat", …)`, drop from the heartbeat prompt: full SOUL.md (replace with a 3–5 line identity digest — voice matters for briefing text), yesterday's daily log (the agent can `read_memory` it when a task actually needs history), and USER.md (same escape hatch). Keep: terse framing, `prompts/heartbeat.md`, due-task blocks (post-WS1-Phase 5), today's chat slice (cheap, and what prevents duplicate briefings), skill list.

**Order of operations.** Land after WS1 Phase 6, not before — while ticks still run hourly and carry all tasks, the daily log and USER.md plausibly earn their keep; after windows, the remaining ticks are focused single-task runs.

**Verify.** Per-tick `input_tokens` in `turns.jsonl` (target ≤15k), plus a week of watching briefing quality — the failure mode is blander/wronger notification text, and the daily log (written by the heartbeat) losing user-context richness. The daily-log task instruction in `heartbeat.py` already tells the agent to call `get_chat_history`, so the log keeps its inputs.

**Risk.** Medium-low. Quality regression is possible and subjective; revert is one commit.

---

## WS4 — Bootstrap context budget (#33 Lever 3)

**Why.** SOUL.md, USER.md, HEARTBEAT.md, and daily logs are injected whole with no cap (`load_or_blank`, `agent.py:189-196`). One verbose daily log inflates every turn that day. Both references cap injected copies while leaving files intact on disk.

**Design.**

1. `load_or_blank(path, max_chars: int | None = None)` — when over budget, keep 70% head + 20% tail (Hermes split) with a `[... truncated N chars — file intact on disk ...]` marker between.
2. Budgets (generous — these are backstops, not diets): SOUL/USER/AGENTS 20k chars each; daily log 10k; HEARTBEAT.md 10k; live slices already capped (20×240 / 60×240 chars — unchanged).
3. Log a warning when truncation fires, so growth is noticed rather than silent (OpenClaw's `bootstrapPromptTruncationWarning`).

**Verify.** Seed an oversized scratch daily log, confirm marker + warning; confirm normal files pass untouched byte-for-byte (prefix stability for WS2).

**Risk.** Very low.

---

## WS5 — Memory & history search (capability, from hermes-agent)

**Why.** Recall today requires knowing the exact filename via MEMORY.md; chat history older than the 50-message window is unreachable except by time-window (`get_chat_history(since=…)`). "What did we decide about X last month?" has no path. Hermes solves this with plain FTS5 over raw history — no embeddings, no new infra — and it's the reason its in-context memory can stay tiny.

**Design.** New core tool `search_memory(query, days=90)` in `tools/core/`:

- **v1: linear scan, no index.** 90 days of JSONL + the memory dir is a few MB; a regex/substring scan is milliseconds at this scale. Sources: `chat_history.jsonl`, `notifications.jsonl` (both already Jarvis-readable via history tools), and `/app/jarvis_memory/**/*.md|txt` (through the existing `_get_safe_path` sandbox). Returns top ~10 hits: source, date/filename, ±1 line of context, per-hit truncation (reuse history.py's per-entry caps).
- **v2 (only if v1 latency or ranking disappoints): SQLite FTS5** index at `/app/jarvis_data/search/index.sqlite` (tool-opaque state per the placement principle), rebuilt incrementally by mtime.
- Docstring guidance: search first, then `read_memory` the specific file — mirrors the existing list→read pattern.
- `prompts/AGENTS.md` gets one line: when the user references something not in context, search before saying you don't remember.

**Verify.** Ask Jarvis about a topic from >50 messages ago; confirm it searches, finds, answers. Check `tool_calls.jsonl` that the tool is actually used.

**Risk.** Low. Additive tool; sandbox already enforced. Watch that the model doesn't over-call it on every turn (docstring wording matters).

---

## WS6 — Memory size pressure (from hermes-agent)

**Why.** Nothing pushes back when USER.md or MEMORY.md grows; every added line is a permanent per-turn tax on both scopes (USER.md is in every prompt). Hermes's cap + gauge + error-driven consolidation keeps the agent itself responsible for curation, continuously, instead of a weekly audit task.

**Design.**

1. Caps in `tools/core/memory.py` for the two always-injected files: USER.md 6k chars, MEMORY.md 8k (roughly 3–4× current sizes — check actuals before fixing values).
2. `write_memory` to a capped file that would exceed its cap → **error** returning the current content and the overage, instructing the agent to consolidate and retry. No silent truncation; SOUL.md exempt (user-curated, confirmation-gated already).
3. Gauge line appended to the injected copy: `[USER.md: 4,120/6,000 chars]` — Hermes shows this nudges pruning before the hard stop. **Caveat:** the gauge is volatile-ish; to protect WS2, only update its rendered value when the file itself changed (it's derived from content, so this is automatic — content change already breaks cache there).
4. Extend the weekly `memory-index-audit` heartbeat task instruction to also distill daily-log material into MEMORY.md and prune stale entries (#33 Lever 5) — the audit becomes the slow loop, write-time pressure the fast loop.

**Verify.** Script a `write_memory` that busts the cap → error with current content; confirm gauge renders; confirm SOUL.md unaffected.

**Risk.** Low-medium: a too-tight cap could make the agent thrash (consolidate every write). Start generous; telemetry + `tool_calls.jsonl` will show retry loops.

---

## WS7 — Conversation compaction (parked; archive #54 Stage 3)

**Decision: do not build yet.** Archive #54 already gates this on evidence — duplicate briefings recurring, the 50-cap evicting useful context in long sessions, "Jarvis forgot what we discussed" complaints. None observed; WS1–WS6 shrink the problem it would solve (WS5 in particular gives a recall path that doesn't need a bigger window).

**When triggered, the research updates #54's sketch in two ways:**

1. **Do tool-result pruning first, not summarization** (OpenClaw session-pruning). In `_add_and_trim` (`agent.py:73-85`), before counting toward the 50-cap, replace the *content* of ToolMessages older than the last ~3 exchanges with head+tail excerpts (`[tool result pruned]`). Jarvis averages ~7 tool calls per turn-pair (2,777 / 408); old tool payloads are the bulk of window weight. This is a 20-line reducer change, no LLM summarizer, no reentry-guard problem — and it may defer real compaction indefinitely.
2. **If summarization is still needed**, adopt the flush-then-summarize order (OpenClaw `memoryFlush`): a silent "save anything durable to memory files" turn before the lossy summary, then Hermes's head/torso/tail split (protect the last ~20 messages, summarize the middle into one SystemMessage) — matching #54's sketch, which stays valid.

Stage 4 (thread unification) remains parked per #54 — and WS1's structured tick-ack + WS2's per-scope stable prefixes reduce the coordination pressure that motivated it.

---

## Cleanups (fold into whichever workstream touches the file first)

- **Stale comments** in `agent.py:108-109` and `agent.py:427-429` claim `active_skills` doesn't filter the bound tool set — contradicted by shipped `_visible`/`get_tools` gating (`registry.py:79-91`). Fix wording.
- **Deterministic Arbox poll** (gating plan §0/§7): after WS1+WS3, `crossfit-sync`'s hourly LLM-driven Arbox check becomes the single biggest remaining heartbeat spender. Move the poll to a deterministic tool run that escalates to the LLM only when the fetched schedule *differs* from `last_known_schedule`. Separate issue when picked up.
- **Issue hygiene**: origin mirror #18 and archive #33 are the same umbrella — cross-link this doc from both; #54's Stage 3 sketch superseded by WS7 above (comment on the issue rather than editing it).

---

## Sequencing & measurement

```
WS1 P1–P4 ──► WS1 P5 ──► WS1 P6 ──► WS3 ──► (deterministic Arbox poll)
     WS2 ────────────────────────────────►  (independent, any time — land early)
     WS4 ────────────────────────────────►  (independent, any time)
     WS5 ──► WS6 ────────────────────────►  (capability track, independent)
     WS7 ····································  (parked, evidence-gated)
```

Every workstream's effect is checkable in `turns.jsonl` (per-turn input/output/cache-read by scope) — run `summarize_usage(group_by="scope")` over a comparable window before and after each landing. The telemetry layer (archive #56) was built exactly for this; use it, and record the before/after in the PR description.
