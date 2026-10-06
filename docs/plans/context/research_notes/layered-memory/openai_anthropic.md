# OpenAI and Anthropic: long-term memory and context management (as of 2026-10)

Research date: 2026-10-06. Source-quality legend used throughout:
- **[DOC]** = vendor documentation fetched directly during this research (verified as of 2026-10-06).
- **[VENDOR-via-3P]** = a vendor statement, but read through a secondary source because the primary page returned HTTP 403 to the fetcher (openai.com/index/*, help.openai.com). Treat the wording as likely accurate but not personally verified.
- **[3P]** = third-party analysis or reverse engineering (not vendor-confirmed).

A caveat that applies to the whole file: OpenAI's own pages (openai.com, help.openai.com) were not fetchable, so every OpenAI ChatGPT-memory claim below comes from secondary reporting or reverse engineering. Anthropic's docs were fetched directly.

---

## 1. OpenAI ChatGPT memory: saved memories vs reference chat history, injected blocks, updates, failure modes

### Takeaway
ChatGPT memory has had three generations: (1) April 2024: an explicit "saved memories" list (the model writes a fact through a tool when asked or when it judges it important); (2) April 2025: "reference chat history", a background layer added on top of saved memories; (3) **June 4, 2026: "Dreaming"**, a background synthesis process that now replaces the saved-memories list as the foundation and produces a user-visible, editable "memory summary". Reverse engineering of the 2025 system found **no on-demand RAG**. Every turn got a fixed, pre-assembled block: interaction metadata, the user's messages from about 40 recent chats, saved memories, and periodically regenerated AI-written "user knowledge" paragraphs. The documented failure modes are staleness (plans that never happened kept as facts), the "memory full" cap, and opacity.

### Cited Findings
- Two user-facing controls: "saved memories" (details you've directly told ChatGPT to remember) and "reference chat history" (ChatGPT references past conversations to learn interests and preferences). Each can be turned off separately in Settings. Temporary Chat neither uses nor updates memory. [VENDOR-via-3P] — [OpenAI Memory FAQ (search snippet)](https://help-lb.openai.com/en/articles/8590148); [OpenAI "Memory and new controls"](https://openai.com/blog/memory-and-new-controls-for-chatgpt)
- June 2025: free users got a "lightweight version that provides short-term continuity", and Plus/Pro got "longer-term understanding". [VENDOR-via-3P] — [OpenAI Memory FAQ (search snippet)](https://help-lb.openai.com/en/articles/8590148)
- **Reverse-engineered injected context (Sept 8, 2025), four components, all injected on every message:** [3P] — [Shlok Khemani, "ChatGPT Memory and the Bitter Lesson"](https://www.shloked.com/writing/chatgpt-memory-bitter-lesson)
  1. *Interaction Metadata*: device and browser, usage and activity patterns, auto-generated and used implicitly (e.g., giving iPhone-specific help without being asked).
  2. *Recent Conversation Content*: timestamped history of the **~40 most recent conversations, user messages only (assistant replies excluded)**.
  3. *Model Set Context*: the user-controllable saved memories. These win when they conflict with other memory types.
  4. *User Knowledge Memories*: AI-generated summaries "periodically synthesized" from chat history. The author's example was ~10 dense paragraphs that **included outdated information (planned trips that never happened)**, and updates appeared periodic rather than continuous.
  - Key finding: "OpenAI just includes everything with every message". No selective retrieval or vector search. The author reads this as a "bitter lesson" bet that stronger models tolerate irrelevant context.
- **Oct 15, 2025: automatic memory management** for Plus/Pro on the web. ChatGPT can "automatically manage, sort, and prioritise saved memories", which addressed "memory full" errors. It reorders by recency and importance, de-emphasizes outdated items, and users can sort by recency and re-prioritize what is "top of mind". [3P news] — [TechRadar](https://www.techradar.com/ai-platforms-assistants/chatgpt/chatgpt-is-smarter-now-that-its-learned-to-forget-a-huge-memory-upgrade-is-coming); [TestingCatalog](https://www.testingcatalog.com/openai-prepares-automatic-memory-management-for-chatgpt.md)
- **June 4, 2026: Dreaming** (details in section 2). It "replaces the saved-memories list as ChatGPT's standalone foundation rather than supplementing it". The same source labels the April 2025 reference-chat-history system "Dreaming V0", which "OpenAI acknowledged was insufficient standalone". [3P summarizing OpenAI] — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)
- User controls after Dreaming: a **"Memory Summary" page** grouped by category (work, hobbies, travel) where users can correct details, dismiss items, give include/exclude instructions per topic, or chat about it. [3P] — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide); users "can access and manually edit ChatGPT's memory summaries" after dreaming — [XDA](https://www.xda-developers.com/chatgpt-can-now-remember-you-better-by-dreaming-about-you-while-it-sleeps/)

### Inferences
- The pre-2026 design is "always-inject a bounded, pre-built profile plus recent-chat digest", and it is **not** retrieval-based. For a single-user Jarvis this supports a small injected core profile plus a recent-activity digest over per-turn RAG for identity and preferences.
- Injecting only *user* messages from recent chats is a cheap, deliberate token-saving choice worth copying for a "recent episodes" digest.
- OpenAI moved from "model writes discrete facts" toward "an offline process synthesizes the profile". Their stated reasons (staleness, correctness, scale) match the failures in Khemani's example. Model-written fact lists alone go stale.

### Gaps
- No primary OpenAI text fetched (403). The exact current injected format after Dreaming (is the memory summary injected verbatim, and how large is it?) was not found in any source.
- No published size cap for saved memories or the summary after the Oct 2025 change.
- It is unclear whether the "40 recent chats, user-only" block survives under Dreaming.

---

## 2. Does OpenAI have "dreaming"/sleep/offline consolidation?

### Takeaway
**Yes, under that exact name.** ChatGPT "Dreaming" launched **June 4, 2026**. It is a background process that reads across a user's conversation history and rewrites the synthesized memory, including time-aware revision of stale facts. It is a ChatGPT product feature. I found **no evidence** of an equivalent "dreaming" primitive in the OpenAI Agents SDK, the Responses API, or Codex (searches surfaced none).

### Cited Findings
- Official framing (quoted by multiple outlets): "a more capable and scalable system for synthesizing memory, developed to tackle the staleness, correctness, and scalability challenges that we observe when memory is applied to the hundreds of millions of users and multi-year time horizons in ChatGPT." [VENDOR-via-3P] — [OpenAI, "Dreaming: Better memory for a more helpful ChatGPT"](https://openai.com/index/chatgpt-memory-dreaming) (403 when fetched; quote via [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide) and search snippets)
- Mechanism: a background process that "reads across years of your conversations and rewrites what ChatGPT remembers about you, without you asking it to save anything". [3P] — [aicatchup](https://aicatchup.com/news/chatgpt-dreaming-memory-architecture) (via search snippet); synthesizes memory "without explicit 'remember this' requests" — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)
- Time-aware rewriting, OpenAI's canonical example: "the user is going to Singapore in July" becomes "the user went to Singapore in July 2026" after the trip. [VENDOR-via-3P] — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide)
- Vendor-stated evals (internal, not independently verified): factual recall 41.5% to 82.8%; preference adherence 71.3%; time-sensitive accuracy 75.1%. [VENDOR-via-3P] — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide); XDA calls the 41.5% figure a "2024" baseline — [XDA](https://www.xda-developers.com/chatgpt-can-now-remember-you-better-by-dreaming-about-you-while-it-sleeps/)
- Rollout: started June 4, 2026 with US Plus/Pro, then Free/Go "over coming weeks". [3P] — [Digital Applied](https://www.digitalapplied.com/blog/chatgpt-memory-dreaming-v3-openai-2026-guide). **Conflict:** XDA says "rolling out to all users, including the Free tier" — [XDA](https://www.xda-developers.com/chatgpt-can-now-remember-you-better-by-dreaming-about-you-while-it-sleeps/).
- Several secondary sites call it "Dreaming V3". [3P] — [DEV Community](https://dev.to/akaranjkar08/openai-dreaming-v3-chatgpt-now-learns-while-you-sleep-4cd2), [mer.vin](https://mer.vin/2026/06/openai-ships-dreaming-v3-memory-architecture-for-chatgpt-at-scale/). I could not confirm whether "V3" is OpenAI's own label.

### Inferences
- The behavior most relevant to Jarvis is **time-anchored rewriting**: converting relative and future-tense facts into absolute, past-tense ones once their date passes. Anthropic's Claude Code Auto Dream does the same (relative dates to absolute dates, per 3P), so both vendors have converged on it.
- Cadence is unpublished, which suggests a batch job (not per-turn). For Jarvis, a nightly heartbeat-style consolidation task fits this pattern.

### Gaps
- Cadence, trigger conditions, inputs (all history or only deltas?), and output format of Dreaming: not found in any accessible source.
- Whether Codex (merged into the ChatGPT desktop app July 9, 2026, per [Wikipedia](https://en.wikipedia.org/wiki/OpenAI_Codex_(AI_agent))) uses Dreaming or has its own memory: not found.

---

## 3. OpenAI Agents SDK / Responses API: sessions, conversation state, compaction, trimming

### Takeaway
OpenAI provides (a) **client-side sessions** in the Agents SDK (a pluggable store behind a four-method protocol, with item-count trimming and a merge callback), (b) **server-side state** via `previous_response_id` and the Conversations API, and (c) **compaction** in two forms: an explicit stateless `/responses/compact` endpoint, and automatic server-side compaction triggered by `compact_threshold`. The compaction output is an **encrypted, opaque item**, not readable text. The official cookbook frames the choice as trimming vs summarization and names *context poisoning* and *summary drift* as the main failure modes.

### Cited Findings
- Session protocol: `get_items(limit)`, `add_items(items)`, `pop_item()`, `clear_session()`. Built-ins: `SQLiteSession`, `OpenAIConversationsSession` (server-managed history via the Conversations API), `OpenAIResponsesCompactionSession` (wraps another session to auto-compact). Extensions include AsyncSQLite, Redis, SQLAlchemy, MongoDB, Dapr, `AdvancedSQLiteSession` ("branching and analytics"), and `EncryptedSession`. [DOC] — [Agents SDK: Sessions](https://openai.github.io/openai-agents-python/sessions/)
- Trimming: `RunConfig(session_settings=SessionSettings(limit=50))` retrieves only the last N items. `session_input_callback` "receives two lists: history and new_input, returning the final list for model input." [DOC] — [Agents SDK: Sessions](https://openai.github.io/openai-agents-python/sessions/)
- `OpenAIResponsesCompactionSession`: `should_trigger_compaction` (default is a size threshold), `compaction_mode` = `"auto"` | `"previous_response_id"` | `"input"`, and `run_compaction()` for manual compaction between turns in low-latency streaming. It auto-compacts after each turn by default. [DOC] — [Agents SDK: Sessions](https://openai.github.io/openai-agents-python/sessions/)
- Responses API compaction: `/responses/compact` takes a full context window and returns "a new compacted context window you can pass to your next `/responses` call". The docs say "do not prune `/responses/compact` output. The returned window is the canonical next context window." Server-side mode: set `context_management` with `compact_threshold`, and "when the rendered token count crosses the configured threshold, the server runs server-side compaction". [DOC] — [OpenAI Compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- The compaction item "is encrypted and opaque", it "carries forward key prior state and reasoning into the next run using fewer tokens", and it is "not intended to be human-interpretable". After a compaction item you may "drop items that came before the most recent compaction item". With `previous_response_id` chaining, "do not manually prune." [DOC] — [OpenAI Compaction guide](https://developers.openai.com/api/docs/guides/compaction)
- Cookbook "Context Engineering: Trimming vs. Summarization" (session memory): [DOC] — [OpenAI Cookbook: session_memory](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
  - Trimming keeps the last N *user turns* verbatim. It is deterministic, costs no extra calls, and "abruptly loses long-range information".
  - Summarization injects structured synthetic messages above the recent turns. Parameters are `context_limit` and `keep_last_n_turns`, with the invariant `keep_last_n_turns ≤ context_limit`.
  - Summary prompt guidance: structured sections; **contradiction checking** (flag conflicts between user claims and system facts); **temporal ordering, where newer supersedes older**; **mark unverified facts** instead of inferring; ~200 words; tailor to the use case.
  - Failure modes: "**Context poisoning**: inaccurate information enters summaries and propagates forward", and "**summary drift**: gradual distortion when details are reinterpreted or compressed across multiple summarization cycles".
  - Engineering: atomic lock-protected state; release the lock during the slow summarization call, then re-check conditions before applying; keep model-visible messages separate from metadata.
- The newer OpenAI "Agents API" (hosted Codex harness) reportedly "automatically compacts earlier context as a session approaches its context limit". [3P] — [aicybr](https://aicybr.com/blog/openai-agents-api-codex-harness-hosted-sandboxes)

### Inferences
- OpenAI's compaction is optimized for continuity *within* a task (opaque, reasoning-preserving). It is not a memory artifact you can inspect or reuse. A Gemini/LangGraph build cannot use it anyway, but the separation is instructive: compaction keeps the working context going, and it is not long-term memory.
- The cookbook's "re-check after the slow call" lock pattern maps directly to a LangGraph checkpointer where a heartbeat and a user turn could race on the same thread.
- Summary drift argues against recursive summary-of-summary. Re-summarize from the raw episodic log where possible.

### Gaps
- Default `compact_threshold` values and the exact `should_trigger_compaction` default: not captured.
- No OpenAI-published long-term (cross-session) memory primitive in the Agents SDK was found. Sessions are conversation history only.

---

## 4. Anthropic: memory tool, Claude app memory, context editing, compaction, Claude Code memory, dreaming

### Takeaway
Anthropic splits the work cleanly. **The model writes durable memory as files** (API memory tool; Claude Code auto memory; Managed Agents memory stores mounted as directories). **The system handles in-session pruning and summarization** (context editing clears tool results and thinking; server-side compaction summarizes). **A separate offline job consolidates** (Managed Agents "Dreams", GA as research preview; Claude Code "Auto Dream", undocumented and flag-gated). The consumer Claude app moved on **July 10, 2026** from a 24-hour batch "memory synthesis" to individual topic entries that Claude reads and updates during chats, plus on-demand RAG chat search.

### Cited Findings

**Memory tool (API)** [DOC] — [Memory tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)
- `{"type": "memory_20250818", "name": "memory"}`. It is client-side: Claude requests operations and your app executes them against storage mapped to the `/memories` prefix. Commands: `view` (directory listing 2 levels deep with sizes, or file with line numbers and optional `view_range`; text views truncate past 16,000 chars), `create`, `str_replace`, `insert`, `delete`, `rename`. Available on all Claude 4+ models.
- The API auto-adds this system prompt text: "IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE. MEMORY PROTOCOL: 1. Use the `view` command … to check for earlier progress. 2. … record status / progress / thoughts etc in your memory. ASSUME INTERRUPTION: Your context window might be reset at any moment…"
- Framed as "just-in-time context retrieval": nothing is pre-loaded, and the agent views or reads on demand.
- Anti-bloat guidance: optional prompt "keep its content up-to-date, coherent and organized … rename or delete files that are no longer relevant. Do not create new files unless necessary". Track and cap file sizes, cap `view` output, "**Periodically delete memory files that haven't been accessed in a long time**" (expiry is the app's job). Must defend against path traversal (`../`, URL-encoded).
- With compaction: "compaction keeps the active context small … and memory preserves the information that must survive summarization."
- Multi-session pattern: an initializer session creates a progress log and feature checklist, each later session reads them first, and progress is updated at session end. Mark items complete only after end-to-end verification.

**Context editing** [DOC] — [Context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- Beta `context-management-2025-06-27`. `clear_tool_uses_20250919`: `trigger` (default 100,000 input tokens, or N tool uses), `keep` (default 3 tool use/result pairs), `clear_at_least` (minimum tokens to clear, so a clear justifies the cache invalidation), `exclude_tools`, `clear_tool_inputs` (default false).
- `clear_thinking_20251015` with `keep` N thinking turns or `"all"`. It must come first in `edits` when combined.
- Runs server-side before Claude sees the prompt. "Your client maintains the full unmodified history." The response reports `applied_edits` with tokens cleared.
- With the memory tool: "Claude receives automatic warnings before content is cleared, allowing it to save important information to memory files."
- Clearing tool results invalidates cached prefixes, so `clear_at_least` exists to amortize that cost.

**Compaction (API)** [DOC] — [Compaction overview](https://platform.claude.com/docs/en/build-with-claude/compaction)
- "Compaction replaces the older turns of a conversation with a summary that Claude writes on the server … because response quality degrades as a conversation grows."
- Three options: **on demand** (beta `compact-2026-09-04`, top-level `compaction` param; you request the summary and put the returned block first in `messages`; supports keeping recent turns verbatim and **running in the background** while the conversation continues), **at a token threshold** (the API compacts inside the request that crosses the trigger; "the API drops what came before the block"; `pause_after_compaction` lets you re-insert recent turns), or **your own client summarizer**. Recommendation: "Use on-demand compaction wherever it is available."
- You can supply your own summarization prompt "when the default summary drops something a later turn needs", and "compact again" handles an already-compacted conversation.
- Earlier threshold beta `compact-2026-01-12`, with a minimum trigger of 50,000 tokens, launched on Opus/Sonnet 4.6. [3P] — [aitraining2u summary](https://www.aitraining2u.com/ccar-f/context-editing-compaction-and-memory.html) (may be superseded by the threshold page's current values).

**Claude Code memory** [DOC] — [Claude Code: How Claude remembers your project](https://code.claude.com/docs/en/memory)
- Two systems, "both loaded at the start of every conversation": CLAUDE.md (written by you; instructions and rules; project, user, or org scope) and **auto memory** (written by Claude; "learnings and patterns"; per-repo; loads the "first 200 lines or 25KB").
- Auto memory lives at `~/.claude/projects/<project>/memory/`: a `MEMORY.md` index ("one line per memory, loaded into every session") plus one topic file per memory, with a `type` in frontmatter (four kinds). **Topic files are not loaded at startup.** "Claude reads them on demand using its standard file tools."
- Size enforcement is done by the harness, not trusted to the model: after Claude writes `MEMORY.md`, Claude Code measures it against the 200-line/25KB limits. Near the limit it reminds Claude to "keep one line per entry, move detail into topic files, and merge or drop stale entries". Over the limit, the write succeeds but returns an error telling Claude to rewrite the index, "because everything past the limit is dropped on the next load."
- Memory files are excluded from transcript retention cleanup and persist until edited or deleted. CLAUDE.md target is under 200 lines ("Longer files consume more context and reduce adherence"). Subdirectory CLAUDE.md and path-scoped rules load on demand.
- Compaction interplay: "Project-root CLAUDE.md survives compaction: after `/compact`, Claude re-reads it from disk and re-injects it." Instructions given only in conversation are lost after compaction, so durable rules must go to files.
- Auto memory is not loaded into subagents (except forks). Subagents can have their own memory dir.

**Claude Code "Auto Dream" / `/dream`** [3P, not in official docs]
- Undocumented. It surfaced in **March 2026** via the reverse-engineered feature flag `tengu_onyx_plover`. It runs automatically "after roughly 24 hours and at least five sessions of new activity", and `/dream` triggers it manually. A background subagent reviews recent session transcripts and memory files, then merges duplicates, resolves contradictions, **converts relative dates to absolute**, and prunes the index to stay under the 200-line limit (a "four-phase cycle"). — [implicator.ai](https://www.implicator.ai/anthropic-adds-auto-dream-to-claude-code-fixing-memory-decay-between-sessions); [claudefa.st](https://claudefa.st/blog/guide/mechanics/auto-dream); [MindStudio](https://www.mindstudio.ai/blog/what-is-claude-code-autodream-memory-consolidation)
- The official memory docs page (fetched 2026-10-06) does **not** mention dream or consolidation. Status: flag-gated/experimental.

**Managed Agents memory stores + Dreams** [DOC]
- Memory stores (beta `agent-memory-2026-07-22`): a store is "a workspace-scoped collection of text documents", **mounted as a directory** at `/mnt/memory/<slug>/`. The agent uses ordinary file tools. A short description of each mount (name, path, access mode, description, instructions) is **auto-added to the system prompt**, while contents are read on demand. Each memory is ≤100 kB (~25k tokens) and a store holds ≤10,000 memories: "Structure memory as many small focused files, not a few large ones." Every change creates an immutable **memory version** (audit trail, 30-day retention, redaction). Up to 8 stores per session, with `read_only` or `read_write` access. Writes use optimistic concurrency (`content_sha256` precondition). — [Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory)
- Explicit warning: with `read_write`, "a successful prompt injection could write malicious content into the store. Later sessions then read that content as trusted memory. Use `read_only` for reference material." — [Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory)
- **Dreams** (research preview, beta `dreaming-2026-04-21`; announced May 6, 2026 per [SiliconANGLE](https://siliconangle.com/2026/05/06/anthropic-letting-claude-agents-dream-dont-sleep-job/)): "Agents write to their memory stores as they work, but these writes are local and incremental: over many sessions a memory store accumulates duplicates, contradictions, and stale entries." A dream is an **async job** with inputs of one memory store plus **1–100 session transcripts**. It produces a **new output store** ("duplicates merged, stale or contradicted entries replaced with the latest value, and new insights surfaced"). "The input store is never modified, so you can review the output and discard it." It takes "minutes to a few hours", accepts optional `instructions` (≤4,096 chars) for synthesis focus, and line-level directives "generally produce no change". Billed at token rates, with cost roughly linear in transcript volume. The caller decides when to run it (no built-in schedule in the API). — [Dreams docs](https://platform.claude.com/docs/en/managed-agents/dreams)
- Harvey reported ~6x completion-rate improvement with Dreaming in internal tests. [3P, vendor-customer claim] — [search summary of launch coverage](https://www.buildfastwithai.com/blogs/claude-managed-agents-dreaming-explained)

**Claude app (consumer) memory** [DOC + 3P]
- Current: "Claude saves memory as a set of individual topics as you chat, rather than summarizing conversations after they end." The legacy system updated a synthesis "every 24 hours". Projects get separate memory spaces with a project summary. Incognito chats are not saved. Certain sensitive categories (government IDs, criminal history, financial account numbers, immigration status) are never saved. Users view and edit topics in Settings > Memory. **Chat search is RAG exposed as tool calls**, on demand (paid plans). [DOC] — [support.claude.com: How does Claude's memory work](https://support.claude.com/en/articles/11817273)
- Topics rollout **July 10, 2026**, replacing the daily memory summary. Health and beliefs topics are excluded unless "Include sensitive topics in memory" is on. Memory extended to Cowork Aug 25, 2026. [3P] — [search summaries of TechCrunch / leadwithai](https://www.leadwithai.co/article/claude-memory-updates-live-chat-cowork)
- The legacy 24-hour synthesis excluded project chats and "provides context for every new standalone conversation". [DOC, legacy text via search snippet] — [support.claude.com](https://support.claude.com/en/articles/11817273)

### Inferences
- Anthropic's division of responsibility: **model-driven** writes (memory tool, auto memory, memory stores), **system-driven** pruning and summarization (context editing, compaction, harness size checks), and a **separate offline consolidator** (Dreams/Auto Dream). The consolidator writes a *new* version for review instead of mutating in place.
- Both Anthropic products that load memory at startup use **a small capped index injected, details read on demand** (MEMORY.md ≤200 lines/25KB; mount descriptions only in Managed Agents). Jarvis's current MEMORY.md-not-injected design is stricter than Claude Code's, which injects the capped index.
- Harness-enforced caps (measure after write, nudge, hard-error over the limit) are a concrete, cheap mechanism for "small capped core memory".
- Both vendors abandoned (or are abandoning) the "batch-synthesized profile only" approach in consumer apps: Anthropic went to incremental topics in July 2026, and OpenAI went the other way to a stronger batch synthesizer. That is a real divergence. Anthropic pairs incremental in-chat writes with an offline dream for agents. OpenAI relies on offline synthesis.

### Gaps
- Default summarization prompt text for Anthropic server-side compaction: not captured.
- Exact Claude app topic-memory injection format and size: not documented.
- Auto Dream's prompt and phases come only from reverse engineering, and its status in current Claude Code versions is unconfirmed.

---

## 5. Published design rationale and lessons about what fails

### Takeaway
Anthropic's position (Sept 2025) is that context is a finite "attention budget" subject to **context rot**. Pre-load only a small high-priority set, retrieve the rest just in time, and use three long-horizon tools: compaction (tune for recall first, then precision; tool-result clearing as the lightest touch), structured note-taking, and sub-agents returning distilled 1–2k-token summaries. OpenAI's cookbook adds the specific failure modes of summarization (poisoning, drift) and the guards against them (contradiction checks, temporal ordering, marking unverified facts). Both vendors' 2026 dreaming launches state the failures of incremental memory outright: staleness, contradictions, duplicates, growth.

### Cited Findings
- "As the number of tokens in the context window increases, the model's ability to accurately recall information from that context decreases" (context rot). Attention is spread across n² token relationships. [DOC] — [Anthropic, Effective context engineering for AI agents (Sept 29, 2025)](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Hybrid strategy: pre-load high-priority info (e.g., CLAUDE.md) and fetch the rest just in time via tools, mirroring how humans use "external organization and indexing systems". [DOC] — [same](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Compaction: "Start by maximizing recall to ensure your compaction prompt captures every relevant piece of information from the trace, then iterate to improve precision." Tool result clearing is the lightest-touch form. [DOC] — [same](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Structured note-taking: the Pokémon agent "maintains precise tallies across thousands of game steps" and reads its notes after context resets. Sub-agents return condensed 1,000–2,000-token summaries. [DOC] — [same](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Incremental-write failure: "writes are local and incremental: over many sessions a memory store accumulates duplicates, contradictions, and stale entries." [DOC] — [Anthropic Dreams docs](https://platform.claude.com/docs/en/managed-agents/dreams)
- Summarization failure modes are context poisoning and summary drift. Guards: contradiction checking, temporal ordering (newer supersedes older), hallucination control (mark unverified). [DOC] — [OpenAI Cookbook](https://developers.openai.com/cookbook/examples/agents_sdk/session_memory)
- Memory as an injection surface: "a successful prompt injection could write malicious content into the store. Later sessions then read that content as trusted memory." [DOC] — [Managed Agents memory](https://platform.claude.com/docs/en/managed-agents/memory). (Embrace The Red has documented ChatGPT memory-injection attacks; not fetched in this pass.)
- Instructions given only in conversation don't survive compaction, so durable rules must live in files that are re-injected after compaction. [DOC] — [Claude Code memory](https://code.claude.com/docs/en/memory)
- Staleness in practice: ChatGPT's synthesized "user knowledge" kept trips that never happened. [3P] — [Khemani](https://www.shloked.com/writing/chatgpt-memory-bitter-lesson)

### Inferences (transferable to Jarvis on Gemini/LangGraph)
- **Working context:** prefer clearing old tool results (cheap, deterministic) before summarizing. When summarizing, keep the last N turns verbatim, use a structured summary prompt with contradiction checks and absolute dates, and avoid summary-of-summary drift by re-deriving from the raw log.
- **Durable rules must be files re-injected each turn** (as Jarvis already does with SOUL/AGENTS/USER). Anything said only in chat is lost to compaction unless the model writes it down. Anthropic's "warn before clearing so the model can save to memory" hook is worth copying.
- **Core memory:** a small capped index or profile, injected every turn, with harness-enforced line/byte limits and nudges (Claude Code's pattern). Details go in on-demand files.
- **Episodic store:** both vendors expose past-chat access. OpenAI injects a recent-chat digest of user messages, and Anthropic exposes chat search as an on-demand RAG tool. A hybrid fits a single user: a tiny recent digest injected, plus a search tool for older material.
- **Offline consolidation:** a scheduled, stateless job over (current memory + recent transcripts) → **a new candidate version, never in-place mutation**, with merge, dedupe, newest-wins contradiction resolution, relative-to-absolute date rewriting, and stale pruning. Make it reviewable and reversible (Anthropic keeps the input store untouched, and memory versions give rollback). This maps onto Jarvis's heartbeat as a nightly task.
- **Security:** background or tool-processing paths that ingest untrusted content (web, email) should get read-only memory access, or their writes should be gated by the consolidation and review step.

### Gaps
- No quantitative published comparison (either vendor) of injected-profile vs retrieval-based memory quality.
- Anthropic's "Effective harnesses for long-running agents" post was referenced but not fetched.
- OpenAI has published no engineering post on Dreaming's internals (none was found accessible). Internals remain unknown.
