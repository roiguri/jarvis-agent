# Cognition (Devin / Windsurf) context and memory: prior positions, sibling products, and reactions to "Memory and dreaming" (Oct 2026)

Research date: 2026-10-06. The publication under study, "Memory and dreaming: how Devin learns from working with you", came out on **2026-10-05**, one day before this research. Independent reaction was therefore very thin and mostly from aggregators. Each finding is labelled **[official]** (Cognition/Devin/Windsurf's own material) or **[third-party]**.

Baseline: what the new system is (official, 2026-10-05)
- [official] Memories live in a per-user "Memory Drive": "a persistent Git repository of markdown files". A short `MEMORY.md` holds general preferences plus an index of the other files. Notes are organised by repository, project or topic. — [Devin blog](https://devin.ai/blog/memory-and-dreaming); [Devin docs](https://docs.devin.ai/product-guides/memory)
- [official] "At the start of a session, Devin receives `MEMORY.md` as context." Other notes are searched and read on demand "using the same tools it uses to navigate code, without loading the entire memory archive into its prompt." — [Devin blog](https://devin.ai/blog/memory-and-dreaming)
- [official] "Memories are not summaries of sessions. They are lessons Devin learned from working with you, written as short notes with a link back to the session where they were learned." — [Devin blog](https://devin.ai/blog/memory-and-dreaming)
- [official] Things saved: preferences, corrections together with the rule they imply, decisions with their reasoning, and environment gotchas. Things not saved: session summaries, task state such as PR numbers, information that is easy to rediscover, and secrets. — [Devin docs](https://docs.devin.ai/product-guides/memory)
- [official] Concurrency: each session gets its own git checkout. "If another session saves an update while a sync is underway, a revision check rejects the stale write so Devin can retry against the newer version." — [Devin blog](https://devin.ai/blog/memory-and-dreaming)
- [official] Dreaming runs "approximately daily when you're actively using Devin" as a background session. It consolidates overlapping notes, removes transient details and stale notes that no recent session used, adds lessons that were missed during the original work, and reorganises files and the index. "Source links and explicit preferences survive the dreaming process." Users can see dreaming history under Customize → Memory. To change memory, the user asks Devin inside a session. — [Devin docs](https://docs.devin.ai/product-guides/memory)
- [official] Memory is personal. It is not shared org instructions. Skills hold the procedure and memory supplies "context for applying it to your work." — [Devin blog](https://devin.ai/blog/memory-and-dreaming)
- [official] The post contains no eval numbers or benchmarks. My fetch of the post found none, and a fetch-model summary stated "No specific evaluations".

## 1. Cognition's earlier context-engineering positions, and whether the new system keeps or reverses them

### Takeaway
Cognition's 2025 position was context continuity: one single-threaded writer, full traces shared, long histories compressed by a dedicated model. In April 2026 it softened this to "many contributors, single-threaded writes, and clean context helps verifiers". The memory system mostly follows the April 2026 view. Memory is no longer about carrying the full trace. It is a small always-loaded index plus on-demand retrieval of distilled lessons, and one asynchronous agent (Dreaming) does the rewriting. Writes still go through one serialised path (git revision check), which continues the "writes stay single-threaded" rule.

### Cited Findings
- [official, 2025-06-12, Walden Yan] "Don't Build Multi-Agents" set out two principles: (1) "Share context, and share full agent traces, not just individual messages"; (2) "Actions carry implicit decisions, and conflicting decisions carry bad results." — [Cognition blog](https://cognition.com/blog/dont-build-multi-agents)
- [official, 2025-06-12] The default it recommends is a single-threaded linear agent. For very long tasks it proposes adding an LLM that compresses "a history of actions & conversation into key details, events, and decisions". — [Cognition blog](https://cognition.com/blog/dont-build-multi-agents)
- [third-party summary of the same post] Cognition said it fine-tuned a smaller model for this compression, and called context engineering "effectively the #1 job of engineers building AI agents." — [agentic-ai.readthedocs summary](https://agentic-ai.readthedocs.io/en/latest/ContextEngineering/devin/); see also [jxnl.co, 2025-09-11](https://jxnl.co/writing/2025/09/11/why-cognition-does-not-use-multi-agent-systems/)
- [official, 2025-06-12] The post approves of Claude Code's subagents because they do not run in parallel and only answer questions rather than write code. It cites edit-apply models (a big model writes instructions and a small model rewrites the file) as a historical case of brittle delegation. — [Cognition blog](https://cognition.com/blog/dont-build-multi-agents)
- [official, 2026-04-22, Walden Yan] "Multi-Agents: What's Actually Working" revises the 2025 stance. What works is "setups where multiple agents contribute intelligence to a task while writes stay single-threaded." Reported patterns: a clean-context review agent catches about 2 bugs per PR, 58% of them severe; a "smart friend" setup where a weaker primary model calls a stronger one, which is described as still an open problem when the primary is much weaker; and manager/child delegation. "clean context leads to a notable improvement in capabilities when using a generator-verifier loop." — [Cognition blog](https://cognition.com/blog/multi-agents-working)

### Inferences
- The 2025 post was about context within a single task. It did not address memory across sessions, and I found no 2025 Cognition statement on long-term memory in that post. The 2026 memory system therefore extends the earlier position more than it reverses it. The 2025 "dedicated compression model" idea shows up again as Dreaming, a separate offline agent that distils sessions into lessons. The difference is that it runs across sessions and nightly, not inside one session.
- The "share full traces" principle is not applied across sessions. Sessions get `MEMORY.md` plus whatever they search for, not earlier traces. This matches the April 2026 "clean context helps" finding and is a real shift of emphasis.
- "Writes stay single-threaded" survives as git with a stale-write revision check: concurrent sessions cannot silently overwrite each other. Relevant for Jarvis: this is close to Jarvis's planned "consolidator emits ops, code validates, commits to git". One difference is that Devin's sessions also write memory live, during the session.

### Gaps
- I could not confirm whether Dreaming uses a fine-tuned or smaller model, or the same model as Devin. The publication does not say.
- I found no Cognition text that explicitly links the memory post back to "Don't Build Multi-Agents". The links above are my own inference.

## 2. Devin Knowledge / Playbooks history (before Memory)

### Takeaway
Before Memory, Devin's persistent context was org-level **Knowledge**: trigger-described items that Devin retrieves when relevant, which it auto-suggests from chat feedback and a human approves. **Playbooks** held whole workflows. As of Oct 2026 Knowledge is officially deprecated and being migrated into "Skills in Plugins". Memory is the new personal, automatically written layer, positioned next to Skills.

### Cited Findings
- [official] "Devin will automatically suggest Knowledge to remember based on your feedback in chat." Users can edit, dismiss or regenerate suggestions, and Devin can also suggest updates to existing items. — [Devin docs: Knowledge](https://docs.devin.ai/product-guides/knowledge); [older onboarding page](https://docs.devin.ai/onboard-devin/knowledge)
- [official] Retrieval works by trigger: "Devin will retrieve a Knowledge item when its current work is related to the specified triggers, and all Knowledge requires a trigger description." "Devin retrieves Knowledge when relevant, not all at once or all at the beginning." The docs advise users to make triggers highly relevant to the item's contents. — [Devin docs](https://docs.devin.ai/product-guides/knowledge)
- [official] Pinning has three levels: none (retrieved only when relevant), a specific repo (always used in that repo), or all repos (applies to every session). Devin can also auto-sort selected items into folders. — [Devin docs](https://docs.devin.ai/product-guides/knowledge)
- [official] Division of labour: a playbook is for repeating a whole workflow, and Knowledge is for repeating a constraint (e.g. "never import from legacy billing"). — [Devin docs, onboarding](https://docs.devin.ai/onboard-devin/knowledge)
- [official, current docs] "Knowledge is deprecated and will be removed in a future update." It is being migrated to Skills in Plugins with "no action" required from users. — [Devin docs](https://docs.devin.ai/product-guides/knowledge)

### Inferences
- The evolution runs from human-approved, trigger-retrieved org knowledge to auto-written personal memory with nightly machine curation and git history. Human approval of each item (the suggestion flow) is replaced by after-the-fact auditability (source links, dreaming history, git). This is the trade-off the skeptics in section 4 point at.
- Retrieval also changes. Knowledge relied on trigger descriptions matched by the system. Memory relies on the agent searching files with its ordinary tools, guided by the `MEMORY.md` index.

### Gaps
- I did not find dates for when Knowledge suggestions or Playbooks launched, or for when the deprecation was announced. The deprecation text is current as of 2026-10.
- I found no Cognition-published data on the precision of Knowledge retrieval or on how often suggestions were accepted.

## 3. Windsurf Cascade Memories and Rules

### Takeaway
Windsurf (Cascade, now documented under docs.devin.ai "Devin Desktop") had auto-generated memories. They were local, workspace-scoped, unversioned and retrieved at the model's discretion. Cognition's own docs now tell users not to rely on them: write Rules/AGENTS.md instead, and migrate to Skills. The newer "Devin Local" agent does not persist memories at all. Rules have explicit activation modes and hard character caps. This official retreat from opaque auto-memories in the sibling product is useful context for why the new Devin memory is git-backed, source-linked and inspectable.

### Cited Findings
- [official] Auto-generated memories are stored locally in `~/.codeium/windsurf/memories/`, "associated with the workspace they were created in". They do not carry across workspaces and are not committed to the repo. "Cascade retrieves them when it believes they're relevant." Creating and using them does not consume credits. Users can also ask "create a memory of...". — [Devin Desktop docs: Memories & Rules](https://docs.devin.ai/desktop/cascade/memories) (docs.windsurf.com now redirects here)
- [official] "Memories apply to the legacy Cascade agent only. The Devin Local agent ... does not persist memories". For durable knowledge, the docs say to migrate to Skills. — [Devin Desktop docs](https://docs.devin.ai/desktop/cascade/memories)
- [official] "For knowledge you want Cascade to reliably reuse, write it as a Rule or add it to `AGENTS.md` in your repo rather than relying on auto-generated Memories. Rules are version-controlled, shareable with your team, and give you explicit control over activation." — [Devin Desktop docs](https://docs.devin.ai/desktop/cascade/memories)
- [official] Rule activation modes: `always_on` ("Full rule content is included in the system prompt on every message"); `model_decision` (only the description is in the prompt and the full rule is fetched when relevant); `glob` (activates when Cascade reads or edits a matching file); `manual` (not in the prompt, invoked with `@rule-name`). Caps: 6,000 characters for global rules and 12,000 characters per workspace rule file. Root AGENTS.md is always on, and subdirectory AGENTS.md files are glob-scoped automatically. Enterprise system-level rules are read-only and merged. — [Devin Desktop docs](https://docs.devin.ai/desktop/cascade/memories)
- [third-party, low quality: SEO content from a memory vendor] Users complain that Cascade loses cross-session context and that only brief auto-memories persist, that in-session summarisation drops details so the agent re-suggests patterns the user rejected, that the roughly 6k rules cap is too small for decision records, and that rules with vague descriptions are not retrieved. — [memorylake.ai](https://www.memorylake.ai/zh/blogs/windsurf-forgets-cascade-context). Treat as anecdotal: the vendor sells a competing product.
- [third-party, 2025-12] A deep dive on Windsurf memory and rules exists, but I did not fetch it. — [mer.vin](https://mer.vin/2025/12/windsurf-memory-rules-deep-dive/)

### Inferences
- Both of Cognition's products now point the same way: explicit, versioned, inspectable artifacts (Rules/AGENTS.md/Skills; git memory repo) instead of opaque auto-memories. The new Devin memory is in effect "auto-memories, but as a git repo with sources and a curator". It directly addresses the weaknesses the Windsurf docs admit to: no versioning, no portability, and unpredictable activation.
- Rule activation modes (always-on vs description-only vs glob vs manual) and hard character caps are a ready-made template for Jarvis's "capped injected core memory plus skills".

### Gaps
- I found no official Windsurf statement explaining why Devin Local dropped memories, and no quantitative data on memory quality.
- I did not verify the launch dates of Cascade Memories. From my training memory, Windsurf Memories launched in late 2024 and Cognition acquired Windsurf in July 2025, but I did not re-verify either date in this session.

## 4. Independent reactions to the new publication

### Takeaway
After roughly one day, reactions are sparse. I found no Hacker News story (Algolia search returned 0 hits for "devin memory dreaming" and nothing relevant for "agent memory repo"), and X content could not be fetched. What exists is aggregator coverage and one explainer blog. The themes: praise for git/markdown as a "boring but useful" substrate and for the open spec; worries about validating inferred memories, over-pruning, silent deletions, one-off instructions hardening into permanent preferences, and memory files as an injection vector. No eval numbers were published, so none were questioned.

### Cited Findings
- [third-party, 2026-10-05, Yash Thakker, explainx.ai] Places the spec next to CLAUDE.md/MEMORY.md persistence and Karpathy's "LLM wiki" pattern, and says it is closest to the wiki pattern but adds formal entry points and cross-tool compatibility. "Git gives memory a history, a way to merge, and permissions." Risks it lists: agents may encode confident but wrong lessons; over-pruning may lose data; an oversized `MEMORY.md` costs tokens; memory files could become an injection vector. — [explainx.ai](https://www.explainx.ai/blog/cognition-devin-dreaming-memory-agent-memory-repo-open-spec-2026)
- [third-party aggregator, 2026-10-05/06, daily.dev] Gives a reaction breakdown (45% positive / 40% mixed / 15% skeptical) that looks machine-generated and has no sourcing; **do not cite the percentages**. Reported themes: git-versioned, diffable memory as a "strong, boring but useful primitive"; requests that deletions be reviewable like PRs rather than silent; the worry that the model that wrote inferred memories will rubber-stamp them; the scope question of whether one-off instructions become permanent preferences; the central question "How are those inferred memories validated before the agent acts on them?" — [daily.dev](https://daily.dev/posts/cognition-s-devin-adds-dreaming-for-memory-cleanup-and-an-open-memory-standard-ebuhqtsfn)
- [third-party, attributed via daily.dev only; **unverified**] Harrison Chase (LangChain) is quoted as saying "memory needs an offline cleanup loop, not just better retrieval." I could not find the original post. — [daily.dev](https://daily.dev/posts/cognition-s-devin-adds-dreaming-for-memory-cleanup-and-an-open-memory-standard-ebuhqtsfn)
- [third-party news] Plain coverage only, no analysis: [KuCoin flash](https://www.kucoin.com/news/flash/devin-ai-launches-memory-and-dreaming-features-for-long-term-learning). The AlphaSignal article returned 403 and could not be read: [AlphaSignal](https://alphasignal.ai/news/cognition-s-devin-now-remembers-your-codebase-and-learns-while-you-sleep). The official X announcement returned 402, so replies could not be read: [X/@cognition](https://x.com/cognition/status/2107165034463867001)
- [third-party, comparison content] The comparison articles that turned up (findskill.ai, mindstudio.ai, nerdleveltech, Forbes 2026-05-11) cover Anthropic Claude Managed Agents "Dreaming" and ChatGPT "Dreaming" more broadly. One search snippet says Claude Dreaming "takes up to 100 past session transcripts as input" per dream operation. I did not fetch these pages, and none was confirmed to discuss Devin specifically. — [findskill.ai](https://findskill.ai/blog/claude-dreaming-vs-openai-memory-vs-mem0/); [mindstudio.ai](https://www.mindstudio.ai/blog/claude-standard-memory-vs-dreaming-managed-agents); [Forbes](https://www.forbes.com/sites/jonmarkman/2026/05/11/claudes-new-dreaming-feature-builds-self-improving-ai-agents/); [nerdleveltech](https://nerdleveltech.com/chatgpt-dreaming-v3-memory-architecture)

### Inferences
- The publication is a late entry under a now-shared "dreaming" label (Anthropic, May 2026; OpenAI, 2026). What sets it apart is the open, tool-agnostic git/markdown spec, not the consolidation idea.
- The main criticism, that inferred memories and deletions are unvalidated, maps directly onto Jarvis's planned design, where code validates the consolidator's operations and commits them to git. Jarvis's design answers the "silent deletion" and "rubber-stamp" concerns better than Devin's published description does. Devin's docs only promise that "source links and explicit preferences survive".

### Gaps
- No Hacker News thread, no Simon Willison post and no readable X threads were found as of 2026-10-06. I found no reactions from OpenClaw or Letta, and no direct comparison by an independent engineer between Devin's design and OpenClaw dreaming or Letta sleep-time compute.
- No numbers were published, so none could be disputed.
- A follow-up search in a few days would probably find more.

## 5. Open-source / reproducible components

### Takeaway
Cognition released **Agent Memory Repo**, an MIT-licensed spec (SPEC.md and README) plus an installable skill/plugin. It covers file and entry conventions only. The spec fetch found no Dreaming implementation and no write, conflict, deletion or contradiction protocol, so the consolidator's behaviour cannot be reproduced from the release.

### Cited Findings
- [official] The spec is "originally developed by Cognition and released as an open standard", hosted at GitHub `AgentMemoryRepo/agentmemoryrepo`. It supports composability: several memory repos can be cloned into one session with separate ownership and git histories. — [cognition.com/agent-memory-repo](https://cognition.com/agent-memory-repo)
- [official repo] MIT licence, about 548 stars when fetched (2026-10-06). Contents: `SPEC.md`, `README.md`, `.devin-plugin/`, `skills/agent-memory-repo/`. The README workflow is: "Clone the latest memory. Grep for what the task needs, or follow links. Update entries as the agent learns, with no human in the loop. Commit after every edit." — [GitHub](https://github.com/AgentMemoryRepo/agentmemoryrepo)
- [official spec] `MEMORY.md` is the required entry point: "Keep it short: include only what every session needs, plus links to everything else." Essentials go at the top, and the rest goes under an `## Index` heading. Entries are single-line bullets with optional metadata `[key: value; key: value]`. The recommended keys are `source` (a session link) and `added` (YYYY-MM-DD), and "Keys are open". Cross-links use `[[path]]`. The repo may hold SQL, scripts and other files. — [SPEC.md](https://raw.githubusercontent.com/AgentMemoryRepo/agentmemoryrepo/main/SPEC.md); [spec page](https://cognition.com/agent-memory-repo)
- [official spec page] Dreaming is described only conceptually: "a dedicated agent periodically identifies patterns across sessions, creates new entries, merges duplicates, and resolves contradictions." — [cognition.com/agent-memory-repo](https://cognition.com/agent-memory-repo)

### Inferences
- The format (one-line entries, `source` + `added` metadata, a short index file, cross-links) is cheap for Jarvis to adopt and suits code validation, because entries are line-addressable and each carries provenance. That makes add/merge/delete operations easy to validate. The `added` date gives decay and staleness logic a basis, which Jarvis could use for skill/memory decay.
- "Commit after every edit, no human in the loop" is the opposite of Jarvis's "consolidator emits ops, code validates, then commit". Devin trusts the agent and relies on git history for audit. Jarvis puts a gate in front of the write.

### Gaps
- The fetch summarised SPEC.md, and its summary said the spec omits write/commit rules, conflict handling, deletion and contradiction policy, and security controls. A full manual read of SPEC.md and the skill folder is advisable to confirm nothing normative was missed.
- I did not determine whether the plugin includes any dreaming prompt or script.
