# Layered memory architectures: Letta/MemGPT, LangGraph + LangMem, Generative Agents, and academic consolidation work (as of 2026-10)

Scope: memory layers, who writes each layer, when consolidation runs, how context overflow is handled, and what evidence exists, for each system, plus how cheaply each idea maps onto Jarvis. Jarvis's stack: a hand-rolled LangGraph StateGraph on Gemini, a SQLite checkpointer with a 50-message window per thread, markdown memory with a MEMORY.md index, SOUL/USER files that are always injected, and an hourly heartbeat with a code-side due-gate.

Freshness legend: **[CURRENT]** confirmed live in Oct 2026; **[RETIRED]** or **[STALE]** means the feature still exists only in archived code or in older docs.

---

## 1. Letta / MemGPT: layers, writers, overflow, sleep-time agents, later changes

### Takeaway
The Letta codebase has gone through three distinct stages:
- **MemGPT paper (2023):** an in-context "working context", a FIFO message queue headed by a recursive summary, plus recall and archival stores the agent pages in via tools.
- **Letta V1 server (2024–mid 2026):** labeled, size-limited memory blocks. When sleep-time is enabled, a separate sleep-time agent owns block edits and runs every N turns (default 5) over messages after a cursor.
- **Letta Code (2026):** a git-backed markdown "MemFS" / "Context Repository". Root files are always in context, subdirectories load on demand behind `MEMORY.md` indexes, and background "reflection"/"dreaming" subagents edit memory in git worktrees.

The 2026 stage is structurally almost identical to Jarvis's existing markdown layout. The V1 API server is now retired.

### Cited Findings

**Status / staleness**
- The `letta-ai/letta` README now says the current source lives in `letta-ai/letta-code`. The `archive` branch "contains the retired Letta V1 API server", and "active projects should use the current source". The repo's main branch holds only meta files (README, LICENSE and similar). The archive branch's last commit is 2026-08-14. — [letta-ai/letta README](https://github.com/letta-ai/letta), checked via the GitHub API on 2026-10-06. **[RETIRED]** applies to the Python server, memory-block API, `enable_sleeptime`, and archival/recall-memory tools as a server product.
- The PyPI package `letta` is now "Letta Code: stateful agents in your terminal", version 0.34.4, uploaded 2026-10-04. The last GitHub releases on letta-ai/letta are 0.16.x (0.16.8, 2026-05-14). — [PyPI letta](https://pypi.org/pypi/letta/json); [GitHub releases](https://github.com/letta-ai/letta/releases)

**MemGPT paper (arXiv 2310.08560; v1 2023-10-12, v2 2024-02-12)**
- **Main context has three parts:**
  - read-only **system instructions**;
  - a fixed-size read/write **working context** for "key facts, preferences, and other important information about the user and the persona";
  - a **FIFO queue** of messages, whose first index holds "a recursive summary of messages that have been evicted from the queue".
  — [MemGPT paper](https://arxiv.org/html/2310.08560v2)
- **External context has two stores:**
  - **recall storage**: the full message history, searchable and paginated via function calls;
  - **archival storage**: a read/write DB for arbitrary-length text with vector search.
  — [MemGPT paper](https://arxiv.org/html/2310.08560v2)
- **Overflow handling:**
  - At about 70% of the context window, the queue manager inserts a "memory pressure" system warning, so the *agent itself* can save important facts to working/archival memory.
  - At 100%, a flush evicts about 50% of the window, and a new recursive summary is generated from the old summary plus the evicted messages. Evicted messages stay in recall storage.
  — [MemGPT paper](https://arxiv.org/html/2310.08560v2)
- **Writer:** the main agent writes inline, via function calls (working-context append/replace, archival insert/search, conversation search). `request_heartbeat=true` chains function calls for multi-step retrieval. — [MemGPT paper](https://arxiv.org/html/2310.08560v2)
- **Evidence (Deep Memory Retrieval on MSC, multi-session chat):**
  - GPT-3.5 Turbo baseline: 38.7% accuracy / 0.394 ROUGE-L.
  - GPT-4 + MemGPT: 92.5% / 0.814.
  - GPT-4 Turbo + MemGPT: 93.4% / 0.827.
  - Nested key-value retrieval: baselines "hit 0 percent accuracy by 3 nesting levels", while MemGPT+GPT-4 stayed consistent.
  — [MemGPT paper](https://arxiv.org/html/2310.08560v2)

**Letta V1 memory blocks [RETIRED server, concept still used]**
- A block has a `label`, a `description` ("the main information used by the agent to determine how to read and write to that block"), a `value`, and a `limit` (character cap).
- Blocks can be `read_only: true`, for example a shared org block.
- Blocks can be attached to several agents, and an update propagates to all of them.
- Default descriptions are auto-generated for the `persona` and `human` labels.
— [Letta docs: memory blocks](https://docs.letta.com/guides/agents/memory-blocks)
- **Source constants (archive branch):**
  - `CORE_MEMORY_PERSONA_CHAR_LIMIT = 20000`, `CORE_MEMORY_HUMAN_CHAR_LIMIT = 20000`, `CORE_MEMORY_BLOCK_CHAR_LIMIT = 100000`.
  - Summarization triggers when step usage exceeds `context_window * SUMMARIZATION_TRIGGER_MULTIPLIER` (0.9), "using instead of 1.0 to avoid 'too many tokens in prompt' fallbacks".
  - A `MESSAGE_SUMMARY_WARNING_STR` warns the agent to save important info before trimming.
  — [letta/constants.py @ archive](https://github.com/letta-ai/letta/blob/archive/letta/constants.py)
- **Tool sets (archive constants):**
  - Base agent memory tools: `core_memory_append`, `core_memory_replace`, `memory`, `memory_apply_patch`; V2 adds `memory_replace`, `memory_insert`.
  - When `enable_sleeptime` is on, the **primary agent's tools shrink to** `send_message`, `conversation_search`, `archival_memory_search`, so it can no longer edit its own blocks.
  - The **sleep-time agent** gets `memory_replace`, `memory_insert`, `memory_rethink`, `memory_finish_edits`. A code comment reserves `memory_rethink`/`memory_finish_edits` "for sleep-time".
  — [letta/constants.py @ archive](https://github.com/letta-ai/letta/blob/archive/letta/constants.py)
- **Sleep-time trigger mechanics (archive source):**
  - After each primary-agent turn, the group manager bumps a `turns_counter`.
  - If `sleeptime_agent_frequency` is None, or `turns_counter % frequency == 0`, the sleep-time agent runs asynchronously on messages *after* a stored `last_processed_message_id` cursor, and the cursor then advances.
  - `server.py` creates the sleep-time group with `sleeptime_agent_frequency=5`.
  — [letta/groups/sleeptime_multi_agent_v*.py @ archive](https://github.com/letta-ai/letta/tree/archive/letta/groups); [letta/server/server.py @ archive](https://github.com/letta-ai/letta/blob/archive/letta/server/server.py)
- The sleep-time agent's persona reads: "Consolidate memories into more concise blocks / Identify patterns in user behavior / Make inferences based on the memory". — [sleeptime_memory_persona.txt @ archive](https://github.com/letta-ai/letta/blob/archive/letta/personas/examples/sleeptime_memory_persona.txt)
- **Letta blog (2025-04-21):**
  - The sleep-time agent "has exclusive tools to edit the primary agent's in-context memory blocks", so memory ops run asynchronously without slowing the conversation.
  - Higher frequency "use[s] more tokens but provide[s] agents more time to revise learned context".
  - Recommended split: a fast primary model (e.g. gpt-4o-mini) and a stronger, slower sleep-time model (gpt-4.1 / Sonnet 3.7), "since latency isn't a constraint".
  — [Letta blog: Sleep-time compute](https://www.letta.com/blog/sleep-time-compute)

**Sleep-time Compute paper (arXiv 2504.13171, 2025-04-17; Lin, Snell, Wang, Packer, Wooders, Stoica, Gonzalez)**
- The model pre-computes inferences over a *context* offline, before queries arrive.
- About 5x less test-time compute for the same accuracy on Stateful GSM-Symbolic and Stateful AIME.
- Scaling sleep-time compute adds up to +13% (GSM-Symbolic) and +18% (AIME) accuracy.
- Amortizing across several related queries on the same context gives 2.5x lower average cost per query.
- Effectiveness "correlates strongly with query predictability".
— [arXiv 2504.13171](https://arxiv.org/abs/2504.13171)

**Letta Code MemFS / Context Repositories [CURRENT, 2026]**
- Context Repositories were announced 2026-02-12. They replace "MemGPT-style memory tools or virtual filesystem operations" with git-backed local markdown files that the agent manages with its full terminal/coding toolset.
- Three memory subagents:
  - **Memory Initialization:** bootstraps a hierarchy from the codebase and historical conversations, using concurrent subagents in git worktrees.
  - **Memory Reflection:** a "background sleep-time process that periodically reviews recent conversation history" and commits what it learns.
  - **Memory Defragmentation:** runs over long-horizon use. It backs up the filesystem, then restructures memory into "15–25 focused files".
- No benchmarks were given.
— [Letta blog: Context Repositories](https://www.letta.com/blog/context-repositories)
- **Current MemFS layout:**
  - A root `MEMORY.md` index.
  - Root `.md` files (e.g. `persona.md`, `human.md`) are loaded "into the agent's system prompt on every turn".
  - Subdirectories with their own `MEMORY.md` stay "out of context until they are needed".
  - `skills/<name>/SKILL.md` holds procedural memory.
  - Older agents used a `system/` directory instead.
  - Every edit is a git commit. "Dreaming and memory doctor" subagents use git worktrees for concurrent updates.
  - There is no semantic index by default; search is an optional mod.
  — [Letta docs: MemFS](https://docs.letta.com/letta-code/memfs)
- **Reflection subagent prompt (`reflection-v2.md`, letta-code main, read 2026-10-06):**
  - Frontmatter: `tools: Bash, Edit`, `model: inherit`.
  - It reads a transcript payload, or a multi-transcript manifest whose "replay" slices exist for dedup and contradiction resolution.
  - **Phases:** Investigate, Extract (priority: mistakes/corrections, then preferences, then new facts, then contradictions, then reusable procedures), Update, and so on.
  - **Filters:**
    - lasting vs ephemeral;
    - already captured;
    - generalizable ("The raw conversation is already searchable — don't re-record it");
    - convert relative dates to absolute ones.
  - **Rules:**
    - root core files stay concise and verbose content moves to deferred memory;
    - integrate into existing files rather than fragmenting;
    - "Identity preservation: … Never rewrite them wholesale";
    - "Contradiction resolution: … fix the stale entry at the source. Do not append the new version alongside the old";
    - retired context moves to a dated `ARCHIVE.md` entry;
    - at most one skill operation per run, preferring `none` or modify over `create`.
  — [letta-code reflection-v2.md](https://github.com/letta-ai/letta-code/blob/main/src/agent/subagents/builtin/reflection-v2.md)
- **Reflection triggers (letta-code source):** `off`, `compaction-event` (fires after context compaction), or `step-count` (fires when `steps_since_last_successful_reflection` crosses a threshold). The docs describe "after a set number of completed agent steps or when the context window is compacted". There is an optional "Agent reviews before applying" mode, in which a second background conversation reviews proposed edits. — [post-turn-reflection.ts](https://github.com/letta-ai/letta-code/blob/main/src/cli/helpers/post-turn-reflection.ts); [Letta docs: sleep-time/dreaming](https://docs.letta.com/guides/agents/architectures/sleeptime)
- **Letta Code compaction:**
  - Default mode `sliding_window`, evicting 30% (`LOCAL_DEFAULT_SLIDING_WINDOW_PERCENTAGE = 0.3`) from the start of context.
  - Summary prompts are capped at 300 words (sliding) or 500 words ("all" mode).
  - Required summary sections include "High level goals", "What happened" and "Lookup hints", meaning key terms for finding detail in message history later.
  — [compaction.ts](https://github.com/letta-ai/letta-code/blob/main/src/backend/local/compaction.ts)
- **Context Doctor:** a `/doctor` skill that audits and repairs memory, the system prompt and skills, "using observed behavior as evidence", and explicitly allows "no edits". — [context-doctor SKILL.md](https://github.com/letta-ai/letta-code/blob/main/src/skills/builtin/context-doctor/SKILL.md)

### Inferences
- **Mapping to Jarvis (cheap):**
  - Jarvis already *is* a MemFS-shaped system: SOUL.md/USER.md correspond to root core files, MEMORY.md is the index, and `tools/<skill>/SKILL.md` covers procedural memory.
  - The missing pieces are a reflection job and a defrag job, not a new storage layer.
  - A Letta-style reflection pass is cheap to adopt: a scheduled heartbeat task (or gate) that reads chat_history.jsonl after a cursor, like the existing `mirror_cursor.json` pattern, and edits memory files. It maps to Letta V1's `last_processed_message_id` plus every-N-turns trigger.
  - The reflection-v2 prompt's filters and contradiction rule can be lifted almost verbatim.
- **Separation of writers:** in Letta V1 with sleep-time on, the primary agent lost block-write tools. That is the strongest precedent for "background agent owns consolidation" and for a cheaper chat model next to a stronger consolidation model. In Jarvis, that would mean a Gemini Flash-class chat model with a Pro-class nightly job. The model names are my inference; Letta's own example used OpenAI/Anthropic models.
- **Git versioning** of `/app/jarvis_memory/` would give the rollback and audit trail that Letta treats as essential for unattended background edits. It would also be cheap: a `git commit` after each consolidation run.
- **Overflow:** Jarvis's 50-message hard cap discards history without summary. A MemGPT/Letta-style compaction is a direct upgrade: a rolling summary at the head of the window that includes "lookup hints" pointing to chat_history.jsonl.

### Gaps
- I did not find the default `step-count` threshold for Letta Code reflection; the settings file was not read.
- I did not find which model Letta Code reflection uses by default beyond `model: inherit`, which is the primary agent's model.
- No published evaluation exists for Context Repositories, reflection or defrag. The Context Repositories blog presents no benchmarks.
- The Sleep-time Compute paper evaluates math-reasoning pre-computation over a fixed context, not conversational memory consolidation. Its gains do not directly measure memory-block quality.

---

## 2. LangGraph + LangMem: short-term vs long-term memory, the memory manager, and maintenance status

### Takeaway
- **LangGraph** separates thread-scoped short-term memory (checkpointer, managed by trim, delete or summarize) from a cross-thread long-term `BaseStore` (namespaced JSON documents with optional embedding search).
- **LangMem** layers on top of the store:
  - typed semantic memories: a "profile" is a single document that gets updated, and a "collection" holds many documents that are inserted, updated or deleted;
  - episodic and procedural memory (prompt optimization);
  - a hot-path vs background split, with a debounced `ReflectionExecutor`.
- LangMem is low-activity: the last PyPI release was 0.0.30 in Oct 2025, and only dependency maintenance has happened since. LangChain v1's `SummarizationMiddleware` now covers the summarization role.

### Cited Findings
- **Short-term memory:**
  - The checkpointer, keyed by `thread_id`, persists state per thread.
  - Long histories are handled with `trim_messages(strategy="last", max_tokens=…)`, deletion via `RemoveMessage`, or LangMem's `SummarizationNode` (`max_tokens`, `max_tokens_before_summary`, `max_summary_tokens`).
  — [LangGraph docs: add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- **Long-term memory:**
  - A store (`InMemoryStore`, `PostgresStore`, …) is compiled into the graph and accessed via `runtime.store.aput/asearch(namespace, …)`.
  - Semantic search needs `index={"embed": …, "dims": …}`.
  — [LangGraph docs: add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- **LangChain v1 `SummarizationMiddleware` [CURRENT]:**
  - `trigger` takes `fraction`, `tokens` or `messages`, with AND/OR combinations.
  - `keep` defaults to `("messages", 20)`; `trim_tokens_to_summarize` is 4000.
  - It replaces older messages with a summary while keeping AI/tool pairs intact.
  - `ContextEditingMiddleware`/`ClearToolUsesEdit` clears old tool outputs past a trigger (default 100,000 tokens), keeping the last 3 and using the placeholder "[cleared]".
  — [LangChain docs: built-in middleware](https://docs.langchain.com/oss/python/langchain/middleware/built-in)
- **LangMem memory types:**
  - **Semantic:** *profiles* are a "single document with strict schemas", where updates replace content. *Collections* are unbounded document sets, and the system must reconcile by "deleting/invalidating or updating/consolidating existing memories".
  - **Episodic:** successful interactions as examples, preserving "the situation, the thought process that led to success, and why that approach worked".
  - **Procedural:** behavioral rules and system instructions that evolve through feedback.
  — [LangMem conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- **Hot path vs background:** hot-path ("conscious") formation updates memory immediately but adds "perceptible latency". Background ("subconscious") formation runs after interactions and is optimized for "higher recall of extracted information". — [LangMem conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- **`create_memory_manager`:**
  - Parameters: `schemas`, `instructions`, `enable_inserts=True`, `enable_updates=True`, `enable_deletes=False` (all defaults).
  - A profile is a single schema with `enable_inserts=False`.
  — [LangMem API reference](https://langchain-ai.github.io/langmem/reference/memory/)
- **`create_memory_store_manager`:**
  - Parameters: `namespace` default `("memories", "{langgraph_user_id}")`, `query_model`, `query_limit=5`, `phases`.
  - It searches relevant memories, extracts, and then updates the store.
  — [LangMem API reference](https://langchain-ai.github.io/langmem/reference/memory/)
- **`ReflectionExecutor`:**
  - `submit(payload, after_seconds=delay)` defers processing.
  - "If new messages arrive before then: 1. Cancel pending processing task 2. Reschedule with new messages included".
  - It runs on local threads; serverless deployments should use the LangGraph Platform remote executor.
  — [LangMem: delayed processing](https://langchain-ai.github.io/langmem/guides/delayed_processing/)
- **Procedural memory:** prompt optimizers "update system instructions and behavioral rules based on conversation feedback". — [LangMem conceptual guide](https://langchain-ai.github.io/langmem/concepts/conceptual_guide/)
- **Maintenance status [STALE-ish]:**
  - PyPI releases: 0.0.28 (2025-07-09), 0.0.29 (2025-07-28), 0.0.30 (2025-10-27). No release in about 11 months.
  - GitHub is not archived (about 1.7k stars, 67 open issues+PRs). Recent commits are dependency bumps and maintenance: 2026-09-04 docs template fix, 2026-09-09 "broadly modernize dependencies", 2026-10-02 dependabot.
  — [PyPI langmem](https://pypi.org/pypi/langmem/json); [GitHub langmem](https://github.com/langchain-ai/langmem), via the GitHub API on 2026-10-06

### Inferences
- **Map onto Jarvis cheaply:**
  1. Debounced background extraction, i.e. `ReflectionExecutor`'s cancel-and-reschedule on new messages. Jarvis's trigger scheduler (APScheduler) could schedule an "extract-after-quiet-period" wake that is rescheduled on each owner message, with no LangMem dependency.
  2. The **profile vs collection** distinction maps to USER.md (profile: rewrite in place, bounded) vs topic `.md` files (collection: insert/update/delete).
  3. `enable_deletes=False` as the default is a useful conservative precedent for unattended consolidation.
- **Avoid as a dependency:** LangMem's low release cadence and its 0.0.x API make it a poor dependency. Its patterns are more valuable than its code, and its storage model (JSON in BaseStore plus embeddings) diverges from Jarvis's markdown files.
- **Summarization at the window cap:** the checkpointer window cap could gain a `SummarizationMiddleware`-like node (trigger on messages, keep about 20). The hand-rolled graph would need to implement it as a node itself, since Jarvis does not use `create_agent` middleware. That is cheap: one LLM call when the cap is hit.

### Gaps
- No published LangMem benchmark evaluation was found in primary sources.
- LangChain has not stated whether LangMem is in maintenance mode; the status is inferred from release cadence.
- The prompt-optimizer kinds (metaprompt/gradient/prompt_memory) were not confirmed from the fetched reference page.

---

## 3. Generative Agents reflection and follow-on consolidation research

### Takeaway
Generative Agents introduced the canonical pattern:
- an append-only memory stream;
- retrieval scored by recency + importance + relevance;
- importance-threshold-triggered reflection that writes cited higher-level insights back into the stream.

Ablations show reflection measurably improves believability. Follow-ons for 2025–26 split into four directions:
- note linking and evolution (A-MEM);
- OS-style tiering with heat-based eviction (MemoryOS);
- prospective/retrospective reflection (RMM);
- offline "sleep-time" consolidation for cost (LightMem).

### Cited Findings
- **Memory stream records:** each has a natural-language description, a creation timestamp and a last-access timestamp. Observations and reflections both live in the stream. — [Generative Agents, arXiv 2304.03442](https://arxiv.org/html/2304.03442)
- **Retrieval score:** `α_rec·recency + α_imp·importance + α_rel·relevance`, all α = 1, with min-max normalization.
  - Recency is an exponential decay of 0.995 per sandbox game hour since last retrieval.
  - Importance is LLM-rated 1–10 at creation.
  - Relevance is embedding cosine similarity.
  — [Generative Agents](https://arxiv.org/html/2304.03442)
- **Reflection trigger and procedure:**
  - Trigger: the sum of importance of the latest events exceeds 150, which happens "roughly two or three times a day".
  - Take the 100 most recent records, ask for the "3 most salient high-level questions", retrieve for each, then extract "5 high-level insights" with citations to evidence records.
  - Insights are stored back in the stream with pointers, so reflections can reflect on reflections.
  — [Generative Agents](https://arxiv.org/html/2304.03442)
- **Evidence (TrueSkill believability):** full architecture μ=29.89, no reflection 26.88, no reflection/planning 25.64, prior-work baseline 21.21 (d=8.16). — [Generative Agents](https://arxiv.org/html/2304.03442)
- **Failure modes observed:**
  - retrieval failures;
  - "hallucinated embellishments" consistent with world knowledge;
  - overly formal style.
  — [Generative Agents](https://arxiv.org/html/2304.03442)
- **A-MEM** (Xu et al., arXiv 2502.12110, NeurIPS 2025): Zettelkasten-style notes with contextual descriptions, keywords and tags, plus links to related notes. New memories "can trigger updates to the contextual representations and attributes of existing historical memories" (memory evolution). It is reported superior to SOTA baselines across six foundation models; the abstract gives no specifics. — [arXiv 2502.12110](https://arxiv.org/abs/2502.12110)
- **MemoryOS** (Kang et al., arXiv 2506.06326, 2025-05-30):
  - Tiers: short-term, mid-term, long-term persona.
  - Short to mid uses dialogue-chain FIFO; mid to long uses segmented pages, with heat-based eviction.
  - On LoCoMo with GPT-4o-mini: +49.11% F1 and +46.18% BLEU-1 average improvement over baselines.
  — [arXiv 2506.06326](https://arxiv.org/abs/2506.06326)
- **Reflective Memory Management (RMM)** (arXiv 2503.08026, ACL 2025; Google Research):
  - *Prospective reflection* summarizes interactions at utterance, turn and session granularity into a memory bank.
  - *Retrospective reflection* refines retrieval via online RL based on which memories the LLM actually cites.
  - More than 10% accuracy improvement over no memory management on LongMemEval.
  — [arXiv 2503.08026](https://arxiv.org/abs/2503.08026); [ACL Anthology](https://preview.aclanthology.org/setup/2025.acl-long.413)
- **LightMem** (arXiv 2510.18866, 2025-10-21, v4 2026-02-28):
  - Stages: sensory filtering/topic grouping, then short-term topic consolidation, then long-term memory with offline "sleep-time" update decoupled from inference.
  - Up to +7.7% QA accuracy (GPT) / +29.3% (Qwen).
  - Up to 38x (GPT) / 20.9x (Qwen) fewer total tokens.
  - Up to 30x / 55.5x fewer API calls.
  — [arXiv 2510.18866](https://arxiv.org/abs/2510.18866)
- **Surveys (2025–26):**
  - "Rethinking Memory in LLM based Agents" (arXiv 2505.00675) names six operations: Consolidation, Updating, Indexing, Forgetting, Retrieval, Compression. — [arXiv 2505.00675](https://arxiv.org/pdf/2505.00675)
  - "Memory in the Age of AI Agents: A Survey" (arXiv 2512.13564). — [arXiv 2512.13564](https://arxiv.org/pdf/2512.13564)
  - "Memory for Autonomous LLM Agents" (Pengfei Du, arXiv 2603.07670, 2026-03-08) frames memory as a write–manage–read loop with five mechanism families (context compression, retrieval stores, reflective self-improvement, hierarchical virtual context, policy-learned management). — [arXiv 2603.07670](https://arxiv.org/html/2603.07670)

### Inferences
- **Reflection mapping:**
  - Jarvis's daily log is an "observation stream" summary.
  - A nightly heartbeat task that reads the day's chat and notifications and writes cited insights into USER.md or topic files is a direct Generative-Agents-style reflection.
  - The cheapest trigger is time-based, via an existing heartbeat cadence and `due:` window. An importance-sum trigger would need per-event LLM scoring, which is extra cost.
- **Citations:** requiring each consolidated statement to cite its source (date and log line) follows both Generative Agents and the survey's "reflection grounding" mitigation. It costs only prompt tokens.
- **Scoring:** recency/importance/relevance scoring needs embeddings, which Jarvis has none of today. It is the least cheap idea to adopt. For a single owner with a modest file count, an index file plus descriptions, as in Letta MemFS, is a reasonable substitute.

### Gaps
- The A-MEM abstract gave no concrete dataset numbers or cost figures; the full paper was not read.
- Generative Agents does not report the cost per reflection.
- RMM's RL retrospective component needs citation signals and a reranker, so its fit for a no-embedding markdown stack is unclear.

---

## 4. Evidence and failure modes: drift, contradictions, forgetting, cost

### Takeaway
- Direct evidence that consolidation "works" exists mostly as QA-benchmark gains: DMR, LoCoMo, LongMemEval.
- Known risks are well documented:
  - repeated summarization erodes rare details (drift);
  - wrong reflections self-reinforce (memory poisoning);
  - agents copy retrieved past behavior, including errors (experience-following / error propagation);
  - contradiction handling and selective forgetting remain immature.
- Practical mitigations converge across sources: cite evidence, prefer newest/user-stated facts, fix stale entries at the source, archive rather than delete, keep version history, and review before applying.

### Cited Findings
- **Drift:** "each compression pass silently discards low-frequency details". After several cycles you get "a sanitized, generic version of history". The survey's example is a safety instruction lost after three summary passes; it is illustrative, not a measured result. — [Du 2026 survey](https://arxiv.org/html/2603.07670)
- **Contradictions:** detection is "underdeveloped". Suggested ingredients:
  - temporal versioning (prefer newest);
  - source attribution ("user statement >> agent inference");
  - flag conflicts for resolution.
  — [Du 2026 survey](https://arxiv.org/html/2603.07670)
- **Forgetting:** it is handled "crudely: hard time-based expiration, storage-limit eviction, or nothing at all". MemoryAgentBench found most systems "fail conspicuously on selective forgetting". — [Du 2026 survey](https://arxiv.org/html/2603.07670)
- **Trustworthy reflection / poisoning:**
  - Self-reflection risks "self-reinforcing error". One bad persisted reflection can affect "thousands of downstream decisions over weeks", and the risk "scales with agent lifetime".
  - Mitigation: "reflection grounding", meaning each reflection cites specific episodic evidence.
  — [Du 2026 survey](https://arxiv.org/html/2603.07670)
- **Benchmark gap:** the survey reports that models near-perfect on LoCoMo "plummet to 40–60% in MemoryArena", a gap between passive recall and decision-relevant memory use. This is secondhand via the survey; MemoryArena was not read directly. — [Du 2026 survey](https://arxiv.org/html/2603.07670)
- **Experience-following** (Xiong et al., arXiv 2505.16067, rev. 2025-10-10):
  - High input similarity to a retrieved memory leads to highly similar outputs.
  - "Inaccuracies in past experiences compound" (error propagation).
  - Some seemingly correct records give misleading value (misaligned replay).
  - The authors argue for regulating memory quality, i.e. curating and deleting, over accumulating.
  — [arXiv 2505.16067](https://arxiv.org/abs/2505.16067)
- **Cost:**
  - Offline consolidation reduces online cost: LightMem cut tokens by up to 38x and API calls by up to 30x on GPT. — [arXiv 2510.18866](https://arxiv.org/abs/2510.18866)
  - Sleep-time compute gives 2.5x lower per-query cost when amortized. — [arXiv 2504.13171](https://arxiv.org/abs/2504.13171)
  - Letta notes that higher sleep-time frequency costs more tokens. — [Letta blog](https://www.letta.com/blog/sleep-time-compute)
- **Practitioner safeguards in Letta Code** (prompt-level, unevaluated):
  - "fix the stale entry at the source" rather than append;
  - never rewrite identity files wholesale;
  - archive retired content to a dated `ARCHIVE.md`;
  - convert relative dates;
  - skip ephemeral content;
  - optional reviewer pass before applying;
  - git history for rollback;
  - periodic defrag to 15–25 files.
  — [reflection-v2.md](https://github.com/letta-ai/letta-code/blob/main/src/agent/subagents/builtin/reflection-v2.md); [Context Repositories blog](https://www.letta.com/blog/context-repositories); [Letta docs](https://docs.letta.com/guides/agents/architectures/sleeptime)
- LangMem defaults `enable_deletes=False` for its memory manager, a conservative default against destructive consolidation. — [LangMem API reference](https://langchain-ai.github.io/langmem/reference/memory/)

### Inferences
- **Guardrails for a Jarvis "dreaming" job, cheapest first:**
  1. Git-commit `/app/jarvis_memory/` before and after each run, which gives drift auditing and rollback.
  2. Require cited evidence (date and source) per added or changed fact.
  3. Treat SOUL.md as read-only to the job. That matches the existing confirmation gate and Letta's "identity preservation".
  4. Edit stale facts in place and move retired facts to an archive file rather than deleting.
  5. Keep raw logs (chat_history.jsonl) as the immutable ground truth, so summaries can always be regenerated rather than summarized-of-summaries. This is the antidote to recursive-summary drift that MemGPT's design is prone to.
  6. Optionally run a cheap second-pass "review diff" call before commit.
- **Cost:** running consolidation once a night on a stronger model, rather than per turn, aligns with both Letta's (fast primary, strong background) and LightMem's evidence.

### Gaps
- I found no controlled study measuring drift or accuracy over many consolidation cycles for a real long-lived personal assistant. The drift evidence is mostly argument and illustration.
- I found no published per-run cost figures for Letta reflection/defrag or for Generative Agents reflection.
- There is no direct evidence on Gemini-specific behavior in any of these systems.
