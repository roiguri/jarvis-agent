# Move memory writes off Jarvis's hot path

The systems that have shipped proactive personal agents in 2026 agree on a five-layer shape, and Jarvis already owns most of the storage it needs. Each layer has its own writer. A **small, capped core is injected every turn**. **Raw episodes are kept verbatim and searched on demand**. **System code compacts the working window**, and **an offline consolidator owns durable rewrites**. Background runs execute in **fresh, isolated contexts** and leave behind only their delivered text. Jarvis's markdown layout (SOUL/USER injected, a MEMORY.md index, topic files, daily logs, skills as procedural memory) is almost exactly the layout of Letta Code's MemFS, Meta Muse, OpenClaw and Instinct. What it lacks is three mechanisms around that storage. It has no compaction: the 50-message window drops old messages without a summary. It has no search: memory is reachable only by filename, and chat only by guessing a time range. It has no single, gated consolidator: the agent writes everything ad hoc on the hot path, and a 3-hourly LLM task rewrites the daily log. The candidate target architecture adds those three mechanisms and makes the hourly heartbeat stateless. Making it stateless removes the replayed thread, which measurements tie to most of a tick's ~78k input tokens. That change needs the owner to reverse at least four deliberate, documented decisions. The pending-mirror drain carries a concrete open risk. hermes-agent's open bug #118863 shows that a user-role mirror of the agent's own scheduled message can make the model answer as if the human had written it, and Jarvis's drain is user-role by design. The evidence base is uneven. Benchmark gains for "raw logs plus good retrieval" and for offline consolidation are real but mostly vendor-reported, none of them measure Gemini Flash or a single-user, low-volume assistant, and the drift and poisoning risks of consolidation are argued rather than measured.

## Five layers, five writers: where every system converges

Every system studied separates memory by **who is allowed to write it**, not only by what it holds. OpenClaw now documents this as an explicit five-tier model:

| Tier | Contents | Writer | When injected |
|---|---|---|---|
| Instructions | AGENTS.md | human only | always |
| Curated core | MEMORY.md, USER.md | nightly dreaming pass or a direct user request | at session start, within budget |
| Episodic | daily notes and transcripts | agent, flush, capture | only on recall |
| Prospective | standing intents and cron jobs | — | when a trigger fires |
| Review | dreaming reports | — | never |

Its stated rule is that "durable memory has exactly one primary writer: the dreaming consolidation pass. Everything else feeds it" ([OpenClaw memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)). Anthropic splits the same work across products: the model writes durable memory as files through the memory tool, Claude Code auto-memory and Managed Agents stores; the system prunes and summarizes through context editing and compaction; and a separate offline job, Managed Agents "Dreams", consolidates into a *new* store and leaves the input untouched ([Anthropic Dreams docs](https://platform.claude.com/docs/en/managed-agents/dreams)). In Letta V1 with sleep-time enabled, the primary agent **lost its block-editing tools entirely**, and a background agent ran every 5 turns over messages after a cursor ([letta constants @ archive](https://github.com/letta-ai/letta/blob/archive/letta/constants.py)). Instinct, by reverse engineering only, goes furthest. The answering agent has read-only, grep-based access to a git-tracked markdown tree, and a once-daily reconcile job makes every commit ([Supermemory blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)).

The **injected core is small and capped by the harness**, not by the model's good intentions:

- hermes injects 2,200 + 1,375 characters, about 1,300 tokens ([hermes memory docs](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)).
- OpenClaw fixes USER.md at 4,000 characters, a cap config can lower but not raise ([OpenClaw user-model.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/user-model.md)).
- Claude Code loads the first 200 lines or 25 KB of its MEMORY.md index. It measures every write against that limit and returns an error telling Claude to rewrite the index "because everything past the limit is dropped on the next load" ([Claude Code memory docs](https://code.claude.com/docs/en/memory)).

Detail lives in topic files read on demand in Claude Code, Letta MemFS ([Letta MemFS docs](https://docs.letta.com/letta-code/memfs)), Managed Agents (where only mount descriptions are injected; [Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory)) and Muse. Muse keeps a curated MEMORY.md beside dated notes, with semantic search across both, per hands-on testing ([Stark Insider](https://www.starkinsider.com/2026/09/meta-muse-vs-openclaw-personal-ai-agent.html); [Meta Help](https://www.meta.com/help/artificial-intelligence/1047255454427887/)).

**Episodic memory stays raw and searchable.** hermes runs FTS5 over every session and returns real messages, with no LLM summarization. OpenClaw runs hybrid BM25 plus vector search over daily notes and transcripts, with recency decay. Anthropic's consumer app exposes chat search as an on-demand RAG tool ([Claude support](https://support.claude.com/en/articles/11817273)). Even OpenAI's pre-2026 design, which used no retrieval and injected a fixed block on every message, included the user's messages from about 40 recent chats as raw text ([Khemani, 3P reverse engineering](https://www.shloked.com/writing/chatgpt-memory-bitter-lesson)).

**Offline consolidation has converged on the same operations:**

- merge duplicates;
- resolve contradictions newest-wins;
- rewrite relative dates as absolute;
- prune stale entries;
- keep the previous version.

The examples line up across vendors. OpenAI's canonical Dreaming example turns "going to Singapore in July" into "went to Singapore in July 2026" (vendor-via-3P: [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)). Claude Code's undocumented Auto Dream converts relative dates to absolute (3P: [claudefa.st](https://claudefa.st/blog/guide/mechanics/auto-dream)). Letta's reflection prompt says to "fix the stale entry at the source. Do not append the new version alongside the old" ([reflection-v2.md](https://github.com/letta-ai/letta-code/blob/main/src/agent/subagents/builtin/reflection-v2.md)). Instinct's reconcile commits move temporary details into workstreams, compress, generalize, redact one-time codes and issue dated corrections (RE).

**Background runs execute isolated and return only their delivered text.** The earlier [REFERENCE_ARCHITECTURES.md](../REFERENCE_ARCHITECTURES.md) recorded that OpenClaw and hermes both converged on this from opposite poles. Since then OpenClaw has made heartbeats share the ordinary system prompt and moved monitor scratch into the user message, which keeps the cached prefix byte-identical across chat and tick turns ([OpenClaw heartbeat.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/gateway/heartbeat.md)). OpenAI's dots run their idle-time "proactive research" with **read-only tools**: anything with side effects goes back through normal checks (3P-quoting-official: [DataCamp](https://www.datacamp.com/blog/openai-dots); [SEJ](https://www.searchenginejournal.com/openai-dots-read-only-proactive-research/591565/)).

Finally, **provenance is a gate, not metadata**:

- OpenClaw excludes cron, heartbeat and sub-agent sessions from producing durable memory candidates. Its production audits found "the overwhelming majority of auto-captured memories" were scaffolding restatements, heartbeat noise and recall feedback loops.
- It marks recalled content so it is never re-extracted.
- It taints every later assistant message in a turn after network-sourced tool output ([OpenClaw memory-architecture.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/memory-architecture.md)).
- Anthropic warns that with read-write memory "a successful prompt injection could write malicious content into the store. Later sessions then read that content as trusted memory" ([Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory)).

## Real divergence sits in write timing, refresh, and opacity

The systems split along four axes, and each one maps onto a Jarvis decision.

**When core memory is written.**

- *Hot path:* hermes's per-turn review fork under hard caps; the Claude consumer app's July 2026 move to topic entries written "as you chat, rather than summarizing conversations after they end" ([Claude support](https://support.claude.com/en/articles/11817273)); Muse's agent describing a rule to "write it down immediately, before I even reply" (an agent self-report, weak evidence).
- *Offline batch:* OpenClaw's nightly sweep, Instinct's 24-hour reconcile, and ChatGPT Dreaming, which now "replaces the saved-memories list as ChatGPT's standalone foundation" (3P: [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)).

Anthropic and OpenAI therefore moved their consumer apps in *opposite* directions within a month of each other: Anthropic toward incremental writes, OpenAI toward stronger batch synthesis. The batch camp pays in latency. In Instinct, a stated preference took about 23 hours 16 minutes to reach a commit, and heavy users are told to "come back tomorrow" when the compaction recap fills (RE: [Supermemory blog](https://supermemory.ai/blog/reverse-engineering-instinct-memory/)). The hot-path camp pays in drift and growth. Anthropic's own Dreams docs state the problem directly: incremental writes are "local and incremental: over many sessions a memory store accumulates duplicates, contradictions, and stale entries."

**How consolidation edits the file.** OpenClaw's biggest change since August is that **the model no longer rewrites MEMORY.md**. A tool-free completion returns add/merge/supersede *operation decisions*. Code composes the file from sourced snippets of at most 160 tokens, then checks it:

- at most 25% loss of prior entries;
- a hard 10,000-character budget;
- a content-hash check before an atomic rename;
- the pre-image stored before any change;
- an append-only fallback when any check fails.

([OpenClaw dreaming.md](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/docs/concepts/dreaming.md); [dreaming.ts](https://github.com/openclaw/openclaw/blob/cc7e664dfe34fd3b0bd53473dc10d033dc5c0e29/src/memory-host-sdk/dreaming.ts).) Anthropic writes a reviewable new store. Letta and Instinct let a sub-agent edit freely inside git. LangMem defaults `enable_deletes=False` ([LangMem API](https://langchain-ai.github.io/langmem/reference/memory/)). Mem0 abandoned in-place UPDATE/DELETE entirely in April 2026 in favour of ADD-only plus ranking at read time (vendor: [Mem0 blog](https://mem0.ai/blog/ai-memory-benchmarks-in-2026)). Destructive in-place rewriting is the one approach no system still defends.

**Whether the injected core refreshes mid-session.** hermes freezes a snapshot at session start, and its docs now admit the consequence for chat platforms. A messaging chat is "one continuous session" lasting weeks, so "the learning loop of forget → recall from memory → search past sessions almost never gets to fire" and "fresh memory entries also stay invisible to the running session" ([hermes memory docs](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/memory.md)). OpenClaw went the other way: core files "refresh per turn within budgets so long-lived sessions pick up consolidation results without restarting." Jarvis's never-ending owner thread is exactly the hermes failure case, and its per-call hot reload ([agent.py](../../../../agent.py) `build_system_prompt`) is already the OpenClaw answer. That design should be kept, not traded for a cache-friendlier frozen snapshot.

**Transparency.** dots' private notes "can't [be] view[ed], correct[ed] or delete[d]", and deleting the dot is the only reset (3P-quoting-official: [Flavio Copes](https://flaviocopes.com/openai-dots/)). Muse exposes editable MEMORY.md files, but Meta concedes it "may still remember information it learned from what you deleted" ([Meta Help](https://www.meta.com/help/artificial-intelligence/1047255454427887/)). Jarvis's plain, owner-readable markdown is a genuine advantage. Git-versioning it, as Letta Code and Instinct do, would make forgetting and rollback checkable rather than best effort.

Background-run context splits too. hermes added an in-session `/heartbeat` (August 2026) that fires as a normal user turn, idle-only, one per session, "same conversation, same context, same prompt cache". Its rule of thumb is to use `/heartbeat` when the prompt needs conversation context and cron for self-contained jobs ([hermes heartbeat.md](https://github.com/NousResearch/hermes-agent/blob/9dcab1e440cdc482320af002e44036b947d6d709/website/docs/user-guide/features/heartbeat.md)). OpenClaw's default heartbeat still runs in the main session, but its #1 community complaint was accumulated heartbeat turns inflating every request, and its remediation was `isolatedSession` plus `lightContext`. Jarvis has neither pole. It runs a *third* persistent thread for ticks that replays 50 messages of prior ticks on every call. No reference system does this.

## The evidence favors raw logs plus retrieval, but most numbers are vendor-reported

**Independent or academic evidence** is thinner than the marketing suggests, but it is consistent.

LongMemEval's own ablations are the strongest design evidence ([arXiv 2410.10813](https://arxiv.org/html/2410.10813)):

- Storing sessions as turn-level rounds helps.
- "Further compression into facts harmed overall results except for multi-session questions."
- Using extracted facts as *additional index keys* lifted recall@k by 9.4% and accuracy by 5.4%.
- Time-aware query expansion lifted temporal retrieval by 11.3%.
- Long-context models showed a 30–60% decline against oracle retrieval.

Generative Agents' ablation showed reflection measurably raising believability: TrueSkill 29.89 with reflection against 26.88 without ([arXiv 2304.03442](https://arxiv.org/html/2304.03442)). MemGPT's recursive-summary queue took GPT-4 to 92.5% on Deep Memory Retrieval against a 38.7% GPT-3.5 baseline ([arXiv 2310.08560](https://arxiv.org/html/2310.08560v2)). Zep later showed full context also scores ~94–98% on that benchmark, so it is near-saturated ([arXiv 2501.13956](https://arxiv.org/html/2501.13956)).

An independent audit of LoCoMo found 6.4% of its answer key wrong. It also found a gpt-4o-mini judge accepted 62.81% of deliberately wrong but on-topic answers, which makes LoCoMo nearly useless for separating systems ([Penfield audit](https://penfieldlabs.substack.com/p/we-audited-locomo-64-of-the-answer)). The one independent re-run of Mem0's open-source pipeline scored 32–49% R@5 on LongMemEval_S, against a vendor claim near 93%. That tester is affiliated with a competitor but published reproducible code ([dev.to](https://dev.to/everest_an/-i-benchmarked-ai-agent-memory-in-2026-and-the-numbers-tell-a-different-story-than-the-marketing-2ae4)). The same tester reproduced Zep's 63.8% exactly.

**Vendor-reported evidence** points the same way and should be weighed as such:

| Claim | Number | Label |
|---|---|---|
| Zep vs full context, LongMemEval_S, GPT-4o | 71.2% vs 60.2%; single-session-assistant *drops* 94.6 → 80.4 because extraction loses what the assistant said | vendor ([arXiv 2501.13956](https://arxiv.org/html/2501.13956)) |
| Simple session RAG + rerank, LongMemEval_S, GPT-4o | 82.4%, equal to oracle | vendor of another product ([Emergence AI](https://www.emergence.ai/blog/sota-on-longmemeval-with-rag)) |
| Mastra Observational Memory (dated observation log kept in context, no retrieval) | 84.23% (gpt-4o), 93.27% (gemini-3-pro), 94.87% (gpt-5-mini) | vendor ([Mastra](https://mastra.ai/research/observational-memory)) |
| Letta filesystem agent (grep + search over files), LoCoMo | 74.0% vs Mem0g 68.5% | vendor ([Letta](https://www.letta.com/blog/benchmarking-ai-agent-memory)) |
| Mem0 2025 paper vs its own full-context baseline, LoCoMo | ~66–68% vs ~73% | vendor, quoted by competitor ([Zep blog](https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)) |
| ChatGPT Dreaming, internal factual-recall eval | 41.5% → 82.8% | vendor-via-3P ([Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)) |
| Managed Agents Dreaming, customer test | ~6× completion rate | vendor-customer via 3P ([buildfastwithai](https://www.buildfastwithai.com/blogs/claude-managed-agents-dreaming-explained)) |
| LightMem offline "sleep-time" update | up to 38× fewer tokens, +7.7% QA (GPT) | academic, authors' own ([arXiv 2510.18866](https://arxiv.org/abs/2510.18866)) |

Read together, the table yields three conclusions that hold across labels:

- **Keeping raw turns and retrieving them well beats replacing them with extracted facts.** Extraction is best used to add index keys or a compact summary *alongside* the log.
- **Full context is not enough even well inside the window.** GPT-4o reached only 60–64% at ~115k tokens, so retrieval or compression pays before any context limit is hit.
- **Systems that keep dated history and surface recency score best on knowledge updates.** Mastra's knowledge-update category reached 85.9–96.2% depending on reader (vendor). Zep's bi-temporal invalidation, which sets an end date and never deletes, is the principled form of the same idea.

What the evidence does **not** support matters as much. No benchmark covers a single-user assistant at Jarvis's volume, about 1.4 MB of chat in three months. None uses Gemini Flash as reader or consolidator. Absolute scores across papers are not comparable, because the same system gains 8–11 points from a better reader model. The failure modes of consolidation are well described but unmeasured over many cycles:

- **summary drift**, where "each compression pass silently discards low-frequency details";
- **poisoning**, where a wrong reflection self-reinforces;
- **experience-following**, where agents copy retrieved past errors.

Sources: [Du 2026 survey](https://arxiv.org/html/2603.07670); [arXiv 2505.16067](https://arxiv.org/abs/2505.16067); the summary-failure taxonomy in [OpenAI's cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory). Graph stores (Zep/Graphiti, Mem0g, Hindsight) show their gains at 100k–10M-token scales and need a graph database. They are overkill here, but their supersede-don't-delete idea copies cheaply into markdown.

Operational incidents give the most transferable lessons:

- **Gates that disagree.** OpenClaw's ranker and applier used different contamination gates, and nightly reports read "Ranked 9–10 … Promoted 0" "for weeks… no warning anywhere" ([#121232](https://github.com/openclaw/openclaw/issues/121232)). One workspace promoted 0 of 512 entries ([#164923](https://github.com/openclaw/openclaw/issues/164923)).
- **Safeguards wired to only some paths.** The pre-compaction flush was disabled on CLI backends ([#137613](https://github.com/openclaw/openclaw/issues/137613)), and the superseded-task guard missed threshold and manual compaction ([#146118](https://github.com/openclaw/openclaw/issues/146118)).
- **Silent store loss.** hermes silently dropped every session write after a WAL orphaning. When FTS over one database is the only long-term recall, that is total amnesia that never announces itself ([#109687](https://github.com/NousResearch/hermes-agent/issues/109687)).

## Jarvis has the storage shape; the target adds compaction, search and one writer

Jarvis's current baseline, measured from prod on 2026-10-06 ([research notes, jarvis_baseline](../research_notes/layered-memory/jarvis_baseline.md)), shows where the context actually goes.

The **system prompt is small**, at about 2.5k tokens in user scope. A user call nonetheless averages **33.8k input** and a user turn **91.8k**. Heartbeat turns average **78.5k input over 3.73 calls**, and **29 of 33 ended with no action**. The heavy parts per call are:

- the replayed thread: the owner checkpoint is 100,946 bytes and the heartbeat checkpoint 69,910 bytes;
- tool results accumulated within the turn;
- bound tool schemas: core plus the persisted fitness and google_health skills put about 38.6 KB (≈9.6k tokens) on *every* heartbeat call.

These are PROBLEMS.md [A4, A5 and A10](../PROBLEMS.md).

Trimmed messages are **discarded with no summary**, and the gaps in what survives are wide:

- `chat_history.jsonl` keeps only user text and the final reply. Tool calls and results go nowhere durable.
- The heartbeat thread is absent from that log entirely.
- `get_chat_history` has no keyword or thread filter and truncates each row to 200 characters ([C2, C3](../PROBLEMS.md); [tools/core/history.py](../../../../tools/core/history.py)).

The archived plan deferred compaction because "our trim is continuous". That premise **stopped being true** when TURN_LIFECYCLE slice 1 moved trimming to turn boundaries, so the main stated blocker is gone ([CONTEXT_PLAN.md](../../archive/CONTEXT_PLAN.md)). PROBLEMS.md C4's claim that MEMORY.md is injected in both scopes is **wrong per the code**: only SOUL.md and USER.md are always-injected memory files. That correction weakens C4 for the index and should be recorded.

The candidate target maps each layer to what converged practice and Jarvis's constraints jointly suggest. This is a candidate for decision, not a plan.

**L1 — working context.** Keep per-scope assembly and per-call hot reload, the OpenClaw answer to hermes's frozen-snapshot failure. Add three things:

1. **Harness-enforced caps** on every injected file. Measure after write, nudge near the limit and error over it, as Claude Code does, closing C1.
2. **Clear old tool results** before anything else. This is the lightest touch: Anthropic's context editing keeps the last 3 tool pairs by default ([context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)), and LangChain's `ClearToolUsesEdit` does the same ([LangChain middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)). It attacks A5.
3. **Compact at turn start** instead of discarding. When the reducer would drop whole turns, write a structured rolling summary at the head of the window, re-derived from the raw episodic rows rather than summarized from the previous summary, to avoid drift.

The summary should use the cookbook's guards: contradiction checks, newer supersedes older, unverified facts marked as such, absolute dates. It should also carry Letta-style "lookup hints" that point into search ([Letta compaction.ts](https://github.com/letta-ai/letta-code/blob/main/src/backend/local/compaction.ts)). Two OpenClaw hardenings are worth copying: a no-summary fallback on timeout, and an audit that rejects summaries reviving superseded tasks. Because Jarvis would persist every trimmed message to L2 in code, the "flush before forget" needs **no LLM call**. That is cheaper and more reliable than the model-driven flushes whose coverage gaps OpenClaw keeps discovering.

**L2 — episodic store.** Keep one code-written SQLite store in `jarvis_data/`, with turn-level rows from every thread. It holds user text, assistant text, tool calls and tool results, heartbeat and wake turns, and delivered notifications, each with timestamp, thread, scope and a provenance class. An FTS5 index sits over it. A search tool takes keywords, time bounds and thread or provenance filters, and returns full text rather than 200-character stubs. Daily logs and memory files are indexed alongside it, which closes C2 and C3 and #81. Embeddings (sqlite-vec, fused by RRF) are an optional second step. Instinct's misses on synonyms and misspellings ("Italian noodles", "pazta") show what pure lexical search loses. OpenClaw's fallback, which keeps keyword hits when embeddings cannot start, shows the safe ordering. hermes's WAL incident argues for a store-integrity check in the telemetry.

**L3 — core memory.** SOUL.md (owner-confirmed) and prompts/AGENTS.md (deploy-only) stay as they are. USER.md becomes a **capped profile of directive entries, each with an observed date, superseded in place rather than appended**. OpenClaw cites PrefEval for this: "append-only preference history reliably causes models to answer from the stale value." The profile gains an explicit "act vs ask" autonomy section like Instinct's profile. MEMORY.md becomes a capped index, either injected or not; that is decision 3 below. Topic files stay on demand and become searchable. A "today so far" recap bridges the gap between a fact being stated and the nightly writer committing it. That gap is Instinct's 24-hour latency problem, and in Jarvis the existing thread window plus today's daily log largely fills it already.

**L4 — consolidation.** One nightly **stateless** consolidator, run off the heartbeat thread in a fresh context, as OpenClaw, Anthropic Dreams and Letta reflection all run theirs. Its inputs are current memory plus episodic rows since a code-owned cursor, the same pattern as `mirror_cursor.json`. The model emits **operations** (add, merge, supersede, archive); it does not write prose. Code applies them with the following checks:

- loss-fraction and size budgets;
- a content-hash check;
- a pre-image saved before the write;
- a git commit of `jarvis_memory/`.

Every entry cites its source date. SOUL.md is read-only to the job: it can only *propose* through the existing confirmation flow. A report states why nothing was promoted, by category, the lesson of OpenClaw #121232. Heartbeat and tool-ingested web content are excluded or down-ranked as promotion sources, following OpenClaw's session-kind and taint gates. The `daily-log` and `memory-index-audit` tasks fold into this job, removing A7's repeated LLM rewrites.

**L5 — background work.** Heartbeat ticks and wakes run with **fresh context**. The code gate, `due:` windows, `heartbeat_respond` ack, Outbox delivery and stamp-after-delivery stay. Continuity comes from code-owned per-task state (the D1 fix) and a hermes-style `context_from` slice of the task's last output, not from a 50-message replay. Schemas are bound only for the skills the due tasks need. Ticks are **read-only except through notify, triggers and confirmation**, the dots rule made explicit. A silent-outcome carryover, an OpenClaw pattern (one row per thread, claimed once by the next user turn), closes #106.

| Existing Jarvis piece | Becomes |
|---|---|
| `_add_and_trim` (50 messages, discard) | Turn-start compaction: tool-result clearing → structured summary + verbatim tail; trimmed rows already in L2 |
| `chat_history.jsonl` (user/final text only) | Superseded by the L2 episode store; kept or derived for human audit |
| `notifications.jsonl` + pending-mirror drain | Stays as the Outbox log; mirror role/timing per decision 2 |
| `get_chat_history` / `get_notification_history` | One search tool over L2 (keywords, time, thread, full text) |
| SOUL.md | Unchanged; consolidator may only propose via confirmation |
| USER.md | Capped directive profile with dates, supersede-in-place, autonomy section |
| MEMORY.md | Capped index, harness-checked; injected or not (decision 3) |
| Topic files, `trips/`, `fitness/`… | Unchanged storage; now searchable; consolidator edits by ops |
| `daily/daily_*.md` (3-hourly LLM rewrite) | Nightly consolidator output (or code-derived digest), indexed in L2 |
| `memory-index-audit` task | Absorbed into the consolidator |
| `heartbeat/*.md` notes | Code-owned structured state for machine facts (D1); prose only where judgement lives |
| Persistent `heartbeat` thread | Fresh context per tick/wake (decision 1) |
| Agent `write_memory` on the hot path | Kept for explicit "remember this"; otherwise feeds L2 for the nightly writer (decision 4) |
| `triggers/` (time-based) | Unchanged; event-conditioned "standing intents" are an optional later addition |
| Golden snapshots, before/after readings | Extended to the compaction summary prompt, consolidation ops and caps |

A rough cost frame, using Jarvis's own measurements: heartbeat input is about **0.86M tokens a day** (11 turns × 78.5k). A nightly Flash consolidation over one day of chat, about 10 user turns plus delivered sends, and a ≤10 KB core is plausibly an order of magnitude smaller than that. Removing the replayed heartbeat thread would recover a large share of the 0.86M. Both figures are estimates to be bracketed by readings, as the E-cluster discipline requires, not claims.

## Seven decisions, four of which reverse documented Jarvis choices

**Decision 1: make heartbeat ticks stateless.** *Reverses* "the thread keeps a mixed history of recent ticks… the noise turns dilute the in-context pattern deliberately" ([CLAUDE.md](../../../../CLAUDE.md)). The case for reversing:

- The persistent thread is the largest replayed slice in every tick (A4).
- It is the mechanism of F2, where a polluted window re-seeds itself.
- No doc records any continuity a tick relies on that code-owned state could not hold.
- Every reference system runs scheduled work isolated, or (hermes `/heartbeat`) in the *user's* thread, never in a third persistent one.

The cost is losing whatever implicit pattern-following the mixed history provides. That is testable on staging before deciding. A narrower variant drops SOUL/USER from ticks (A10). It needs the one LLM judge RESEARCH.md already accepted as necessary: does a lighter tick write blander or wronger briefings?

**Decision 2: the pending-mirror's role and timing, against hermes #118863.** Jarvis drains delivered sends into the owner thread as **one user-role block** headed `[Messages Jarvis sent you since the last turn:]`, because the reducer drops leading assistant-role messages and one message keeps the Gemini wire alternating ([pending_mirrors.py](../../../../pending_mirrors.py)). hermes stores its mirror as a user-role `"[Cron delivery: <job>]"` turn. In #118863 the model "reads its own scheduled message as something the human typed, and answers the human as if they were an operator", replying "Noted, that's my scheduled check-in already delivered" and referring to the user in the third person. A commenter traced it partly to provenance metadata being discarded on write ([#118863](https://github.com/NousResearch/hermes-agent/issues/118863), open, no fix merged).

Jarvis's header is more explicit than hermes's bare tag: it names the messages as Jarvis's own sends, addressed to "you". But the failure shape is the same: an agent utterance sitting in a user-role slot. Nobody has checked whether Jarvis exhibits it. The cheap first step is a read of the owner checkpoint and of replies after drained blocks, looking for third-person references to the owner or "noted, already delivered" acknowledgements. The options are:

- keep user-role with stronger framing;
- write the delivered text as an **assistant-role message at delivery time**, as OpenClaw commits cron results into the bound conversation, where "delivered" means both the send and the transcript commit succeeded;
- a hybrid.

The second option conflicts with the documented alternation constraint. The research notes infer that Gemini tolerates consecutive model turns, but Jarvis's code comments assert the opposite. That disagreement must be settled by a staging test, not by either source. This decision also touches the "mirror only delivered text, never the transcript" rule, which the evidence supports keeping.

**Decision 3: inject a capped MEMORY.md index.** *Reverses* "MEMORY.md tool-read rather than injected is a deliberate token trade, not an omission" ([docs/architecture/MEMORY.md](../../../architecture/MEMORY.md)). Claude Code, Letta MemFS, OpenClaw (in private sessions) and Instinct's profile-as-index all inject a capped index. Anthropic's Managed Agents injects only mount descriptions. With search added (L2), the index matters less for recall. The trade becomes about 500 injected tokens against the A6 pattern of several `read_memory` round-trips, each re-sending the whole context. Either answer is defensible. What is not defensible is leaving it uncapped.

**Decision 4: who writes core memory.** *Reverses*, at least partly, AGENTS.md's "write to memory proactively whenever something important is established", along with USER.md being agent-writable without confirmation. The options are:

- hot-path only, like hermes or Claude app topics;
- a single offline writer, like OpenClaw, Instinct or Letta V1 sleep-time;
- a hybrid.

The evidence leans toward the **hybrid**: explicit owner requests ("remember that…") are written immediately, and everything else lands in L2 for the nightly writer. hermes's top documented failure is the model *claiming* a save it never made, and OpenClaw keeps direct-request writes even with a single primary writer. A second sub-decision is whether the consolidator's ops apply unattended with git rollback, or are staged for owner review. hermes pins staged writes to the exact entry the approver saw. Unattended application with a nightly diff notification is the lighter option.

**Decision 5: compact rather than discard.** This reverses the CONTEXT_PLAN deferral, whose premise is now false. The sub-decisions are:

- the trigger (message count, tokens, or both);
- the verbatim tail size: OpenClaw keeps 20k tokens, LangChain keeps 20 messages;
- whether the summary model is the chat model.

Implicit caching is a factor. Compaction changes the prefix once per event, which argues for infrequent, chunky compactions in the spirit of Anthropic's `clear_at_least` amortization. The precise Gemini cache rules are still unverified, which is B2.

**Decision 6: the consolidator's model tier and cadence.** Letta recommends a fast primary model with a stronger background one, "since latency isn't a constraint" ([Letta blog](https://www.letta.com/blog/sleep-time-compute)). OpenClaw defaults to the agent's own model. hermes reports equal capture quality at 3–5× lower cost with a cheaper model fed a digest. Nightly on Flash is the cheap default. A Pro-class run is a reasonable upgrade if before/after readings show missed promotions.

**Decision 7: embeddings and git.** Should L2 start as FTS5-only, adding sqlite-vec later? Should `jarvis_memory/` become a git repository? Git would give every consolidator run an auditable diff and rollback, and turn "forget X" into a verifiable operation. It touches the placement principle only in that a new `.git` directory would need deny-listing like `threads.sqlite`.

**Risks and open questions.** Consolidation adds the specific risks of summary drift, context poisoning and self-reinforcing error. The mitigations are the converged guards: source citations, supersede not delete, raw logs as immutable ground truth, version history. None has been measured over many cycles on a real personal assistant.

A single writer with gates can **stall silently**. OpenClaw spent weeks promoting nothing, so promotion counts and rejection reasons need a place in the telemetry someone actually reads, which is E1. Every safeguard must be wired to **every** forgetting path: turn-start trim, failed turns, thread resets and app/Telegram media stripping. Tests should cover that matrix, because both OpenClaw's flush and its superseded-task guard missed paths.

Memory becomes an **injection surface** once web, email or GitHub content can reach the consolidator. That calls for provenance tagging at L2 write time.

The dots and Muse descriptions rest on launch coverage and agent self-reports. The Instinct architecture is one researcher's unconfirmed black-box reconstruction. The ChatGPT Dreaming figures come through secondary outlets because OpenAI's pages were not fetchable. Several pieces of Jarvis evidence are also missing:

- the cache-share rise (49% → 59%) is not yet attributed to PR #132;
- per-task heartbeat cost is unmeasurable, because `turns.jsonl` does not log due tasks;
- nobody has counted how often user turns call the history tools.

The first two belong in the "before" reading of any phase that follows.

## Conclusion

The research changes the question Jarvis should be asking. The question is not which memory product or graph to adopt, because the storage shape it built by hand is the one Letta, Muse, OpenClaw and Instinct converged on. It is **who is allowed to write, when, and with what checks**. The industry's hard-won lessons are almost all about writers. Incremental hot-path writes accumulate duplicates and stale facts. Models that rewrite memory prose destroy entries nobody asked them to touch. Gates that sit in two places stall silently. Background transcripts pollute whatever they are allowed to reach. Jarvis's biggest measured cost, the replayed heartbeat thread, and its biggest recall gap, unsearchable discarded history, both trace back to having no designated writer for the episodic layer and no clean boundary for background work.

The decision-relevant novelty is that Jarvis is unusually well placed to adopt the strict form of the converged design cheaply. Its volume is tiny. Its owner can read every file. It already has a code-side scheduler with cursors and stamp-after-success semantics, which is exactly the machinery a nightly ops-based consolidator and a code-written episode store need. Its turn-boundary trim has removed the stated blocker to compaction. The real costs are in reversing the deliberate choices: the persistent heartbeat thread, the uninjected index, and agent-owned writes. On top of that comes settling, by a staging test rather than by inference, whether the user-role mirror already shows the #118863 symptom.
