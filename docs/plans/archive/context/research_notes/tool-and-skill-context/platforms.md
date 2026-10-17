# Tool and skill context cost: how agent platforms manage it (as of 2026-10)

Scope: how platforms keep tool schemas, tool descriptions and skill instructions from taking over the context window; evidence on tool count and schema size versus accuracy and cost; how tool definitions interact with prompt caching. Consumer: Jarvis (Gemini Flash, hand-rolled LangGraph, ~16 KB always-on core schemas, activatable skills that stay bound for the thread; ~38.6 KB extra schemas per heartbeat call from fitness + google_health).

Evidence labels: **[vendor]** = the platform's own docs or blog, describing its own product. **[independent]** = third-party paper or benchmark. **[community]** = secondary blog or aggregator, lower confidence.

## Anthropic API: tool search / defer_loading, Agent Skills, code execution with MCP, context editing, tool guidance, caching

### Takeaway
Anthropic's main mechanism is server-side **tool search**. Deferred tool definitions are sent with every request but kept out of the context and out of the cached prefix. When a search finds a tool, it is added inline as a `tool_reference` in message history, so the cache is preserved. Anthropic says to use tool search above about 10 tools or 10K tokens of definitions, and to keep the 3–5 most-used tools always loaded. Skills follow the same idea: about 100 tokens of metadata always, a body under 5K tokens loaded on trigger, and files loaded only when read. Modifying tool definitions invalidates the **entire** prompt cache.

### Cited Findings
- **[vendor]** Tool search lets Claude "search your tool catalog (including tool names, descriptions, argument names, and argument descriptions) and loads only the tools it needs." Two variants exist: regex (`tool_search_tool_regex_20251119`, Python `re.search` pattern, 200 chars max) and BM25 (`tool_search_tool_bm25_20251119`, natural language, 500 chars max). A search returns up to 5 tools by default, and `limit` can be 1–10,000. The maximum is 10,000 deferred tools per request. — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** On scale: "A typical multiserver setup (GitHub, Slack, Sentry, Grafana, and Splunk) can consume ~55k tokens in definitions… Tool search typically reduces this by over 85 percent, loading only the 3–5 tools Claude needs". Also: "Claude's ability to pick the right tool degrades once you exceed 30–50 available tools." — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** `defer_loading` "controls what enters the context window, not what you send in the request". Every full definition is still sent on every request. At least one tool must be non-deferred, or the API returns a 400 error. "Keep your 3–5 most frequently used tools non-deferred." — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** On caching: "the API excludes deferred tools from the system-prompt prefix. When Claude discovers a deferred tool… the API appends a `tool_reference` block inline in the conversation… The prefix is untouched, so prompt caching is preserved." Discovered tools stay usable in later turns without another search, because "The API expands `tool_reference` blocks throughout the conversation history". A deferred tool cannot carry `cache_control` (400 error). — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** When to use it: "10 or more tools", "definitions consume more than 10k tokens", "selection accuracy drops", "200+ tools". Standard tool calling fits better at "fewer than 10 tools, every tool is used in every request". Optimization tips: namespaced prefixes (`github_`, `slack_`), keywords in descriptions, and "a system prompt section describing available tool categories". — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** Custom client-side search (for example with embeddings) is supported: return a normal `tool_result` whose content is `tool_reference` blocks. Each referenced tool must be in `tools` with `defer_loading: true`. — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** Billing: tool search has no separate meter, and "the tool definitions that search loads into context count as input tokens like any other tool definition." — [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
- **[vendor]** Advanced tool use launch (2025-11-24). Five-server MCP breakdown: GitHub ~26K, Slack ~21K, Sentry ~3K, Grafana ~3K, Splunk ~2K tokens. Context fell from ~77K to ~8.7K tokens (85%). Tool selection accuracy on MCP evals: Opus 4 went from 49% to 74%, Opus 4.5 from 79.5% to 88.1%. Programmatic tool calling cut average tokens from 43,588 to 27,297 (37%). Tool use examples (`input_examples`) raised complex-parameter accuracy from 72% to 90%. — [Anthropic Engineering, "Introducing advanced tool use"](https://www.anthropic.com/engineering/advanced-tool-use)
- **[vendor]** Cache hierarchy is `tools → system → messages`. "Modifying tool definitions → Entire cache (tools, system, messages)". "Changing `tool_choice` → Messages cache". "adding tools dynamically through tool search does not break your cache". — [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)
- **[vendor]** Agent Skills, three levels: Level 1 metadata is "Always (at startup)", "~100 tokens per Skill". Level 2 SKILL.md body is loaded "When Skill is triggered", "Under 5k tokens". Level 3 resources cost "None until accessed", and scripts run with only their output entering context. Name ≤64 chars, description ≤1024 chars, and the description "must say both what the Skill does and when to use it." — [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
- **[vendor]** "Code execution with MCP" (2025-11-04): presents MCP tools as a filesystem of TypeScript modules. A `search_tools` tool takes a detail-level parameter ("name only, name and description, or the full definition with schemas"). Example: 150,000 → 2,000 tokens (98.7%). Caveat: needs "sandboxing, resource limits, and monitoring". — [Anthropic Engineering](https://www.anthropic.com/engineering/code-execution-with-mcp)
- **[vendor]** "Writing effective tools for agents" (2025-09-11): "More tools don't always lead to better outcomes". The post recommends consolidating tools, namespacing (`asana_projects_search`), and a `response_format` enum ("detailed" 206 tokens vs "concise" 72 tokens). "even small refinements to tool descriptions can yield dramatic improvements" (cites SWE-bench). Claude Code caps tool responses at 25,000 tokens by default. — [Anthropic Engineering](https://www.anthropic.com/engineering/writing-tools-for-agents)
- **[vendor]** Context editing `clear_tool_uses_20250919` (beta header `context-management-2025-06-27`). Defaults: trigger 100,000 input tokens, keep 3 tool uses. Options: `clear_at_least`, `exclude_tools`, `clear_tool_inputs`. "Each time content is cleared, the cache is invalidated at that point". `clear_at_least` exists to make each invalidation worth its cost. Server-side compaction is called "the primary strategy". — [Context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)

### Inferences
- Anthropic's design gets dynamic tool availability without cache misses because the full catalog stays fixed in the request and only *visibility* changes, with visibility added as appended history. A hand-rolled loop on another provider can only copy this if that provider gives the same guarantee. Gemini does not appear to (see the Gemini section).
- The 10-tool / 10K-token threshold is useful for Jarvis: 16 KB of core schemas is roughly 4K tokens, below the threshold. The heartbeat's extra ~38.6 KB (roughly 10K tokens at ~4 chars/token, an estimate) is right at the point where Anthropic would advise deferral.

### Gaps
- No Anthropic number on the fixed tool-use system-prompt overhead per model was retrieved this pass (the pricing page lists it, but I did not fetch it).
- The 49%→74% and 79.5%→88.1% figures are vendor-internal MCP evals that have not been independently reproduced.

## Claude Code: deferred tools via ToolSearch, skill listing, MCP loading

### Takeaway
In Claude Code, MCP tool search is **on by default**. Deferred tools appear only by name until `ToolSearch` loads their schemas. Skills cost a description each (capped at 1,536 chars) until invoked. Once invoked, a skill's body **stays in context for the rest of the session**, and after compaction it is re-attached within a budget. There is no unload.

### Cited Findings
- **[vendor]** "Scale with MCP tool search, the default". Tool search is off with a custom `ANTHROPIC_BASE_URL`, with `ENABLE_TOOL_SEARCH=false`, or with pre-Claude-4.5 models on Google Cloud. In that case Claude Code falls back to a `WaitForMcpServers` tool. When a server connects mid-turn, "Claude Code lists the server's tool names to Claude on its next request". — [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
- **[vendor]** MCP output limits: warning above 10,000 tokens, default `MAX_MCP_OUTPUT_TOKENS` 25,000. — [Claude Code MCP docs](https://code.claude.com/docs/en/mcp)
- **[vendor]** Skills: "skill descriptions are loaded into context… full skill content only loads when invoked". Combined `description` + `when_to_use` "is truncated at 1,536 characters in the skill listing". — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- **[vendor]** Lifecycle: "the rendered SKILL.md content enters the conversation as a single message and stays there across later turns." On compaction, Claude Code "re-attaches the most recent invocation of each skill after the summary, keeping the first 5,000 tokens of each. Re-attached skills share a combined budget of 25,000 tokens." — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- **[vendor]** `disable-model-invocation: true` gives "Description not in context". `allowed-tools` grants permission only for the invoking turn: "The grant clears when you send your next message, even though the skill content stays in context". — [Claude Code skills docs](https://code.claude.com/docs/en/skills)
- Observed directly in this session's harness (not a published source): deferred tools are listed only by name in a system reminder, and their schemas load through `ToolSearch` with a `select:` or keyword query.

### Inferences
- Claude Code treats skill bodies as append-only conversation content, not system-prompt mutation. That matches the cache-preserving pattern. It handles size through compaction budgets rather than per-turn unloading.

### Gaps
- No published Claude Code numbers on token savings from MCP tool search, or on any threshold that switches it on, were found in the fetched docs. Older threshold-based behaviour such as "auto at 10% of context" could not be confirmed on the current page.

## OpenAI: tool-count guidance, allowed_tools, tool search + namespaces, caching, Codex skills

### Takeaway
OpenAI gives a soft guideline of "fewer than 20 functions at the start of a turn". It offers `allowed_tools` to restrict callable tools **without changing the cached tool list**. Since GPT-5.4 it also has Responses API `tool_search` with `defer_loading` and namespaces, and it explicitly loads discovered tools "at the end of the model's context window" to keep the cache. Codex limits its skill listing to 2% of the context window.

### Cited Findings
- **[vendor]** "Aim for fewer than 20 functions available at the start of a turn at any one time, though this is just a soft suggestion." Functions are "injected into the system message", "count against the model's context limit and are billed as input tokens." — [OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
- **[vendor]** `allowed_tools` in `tool_choice` (mode `auto` or `required`) restricts the callable subset while keeping the full `tools` list, so the full list stays the cached prefix ("maximizes savings from prompt caching"). — [OpenAI function calling guide](https://developers.openai.com/api/docs/guides/function-calling)
- **[vendor]** Tool search: "gpt-5.4 and later models support `tool_search`". Deferred tools show only name and description until loaded. Namespaces: "aim to keep each namespace to fewer than 10 functions". Namespaces or MCP servers are preferred because models "have primarily been trained to search those surfaces". Hosted and client-executed (`tool_search_call` → `tool_search_output`) variants exist. — [OpenAI tool search guide](https://developers.openai.com/api/docs/guides/tools-tool-search)
- **[vendor]** Caching: "All tools are loaded at the end of the model's context window. This holds true for both hosted tool search and client-executed tool search. This allows the model's cache to be preserved from one request to another". — [OpenAI tool search guide](https://developers.openai.com/api/docs/guides/tools-tool-search)
- **[community]** OpenAI prompt caching applies above 1024 tokens, and "Both the messages array and the list of available tools can be cached". — [Portkey summary of OpenAI prompt caching](https://portkey.ai/docs/integrations/llms/openai/prompt-caching-openai) (secondary; the primary OpenAI caching page was not fetched)
- **[vendor]** Codex skills: the listing includes name, description and path, capped at "at most 2% of the model's context window, or 8,000 characters when the context window is unknown". Descriptions are shortened first, then skills are omitted with a warning. The full SKILL.md is read on selection. `allow_implicit_invocation: false` limits a skill to explicit `$skill` invocation. — [OpenAI/Codex build-skills docs](https://learn.chatgpt.com/docs/build-skills)

### Inferences
- `allowed_tools` is the clearest vendor example of separating "what's in the cached prefix" from "what's callable this turn". It is a cheap scoping lever where the provider supports it.

### Gaps
- Agents SDK handoffs as a tool-scoping mechanism were not researched this pass (no source fetched). The general pattern is that each agent has its own tools and a handoff swaps the active toolset, but this is unsourced here.
- No published OpenAI accuracy numbers on tool count were found.

## Google Gemini: tool-count guidance, function calling modes, caching

### Takeaway
Google recommends keeping the "active set to 10-20 tools maximum". Its caching model works against tools that change by turn: implicit caching (on by default, 4,096-token minimum for Gemini 3.x Flash) is **prefix-based**. Explicit caches must **contain** the tools and tool_config, and a request that uses `cachedContent` cannot pass `tools`, `tool_config` or `system_instruction`. Gemini has no vendor tool-search / defer_loading feature comparable to Anthropic's or OpenAI's (none found).

### Cited Findings
- **[vendor]** Best practices: "Keep active set to 10-20 tools maximum." Also: be clear and specific, use specific types and enums, validate calls before executing. — [Gemini function calling docs](https://ai.google.dev/gemini-api/docs/function-calling)
- **[vendor]** The current page lists modes `auto` (default), `any` ("constrained to always predict a function call"), `none`, and `validated` ("ensures function schema adherence"). It shows restricting calls with `allowed_tools` / a function-name list. (The legacy generateContent field is `allowed_function_names` under `function_calling_config` with mode ANY. The fetched page appears to show a newer `tool_choice` shape, so check which API surface Jarvis's SDK uses.) — [Gemini function calling docs](https://ai.google.dev/gemini-api/docs/function-calling)
- **[vendor]** Implicit caching: "enabled by default for all Gemini 2.5 and newer models". Minimum is 4,096 tokens for Gemini 3.5–3.8 Flash and 2,048 for 2.5 Flash. The docs advise "putting large and common contents at the beginning of your prompt" and sending "requests with similar prefix in a short amount of time". The field to monitor is `usage.total_cached_tokens`. — [Gemini caching docs](https://ai.google.dev/gemini-api/docs/caching)
- **[vendor]** Explicit caching TTL defaults to 1 hour, and storage is billed by TTL. — [Gemini generate-content caching docs](https://ai.google.dev/gemini-api/docs/generate-content/caching)
- **[vendor, via developer forum]** Error text: "CachedContent can not be used with GenerateContent request setting system_instruction, tools or tool_config". Tools must be baked into the cache. — [Google AI developer forum](https://discuss.ai.google.dev/t/structured-output-support-with-cached-content/41525)
- **[vendor, Vertex]** Implicit cache hits get a 90% discount on Gemini 2.5+ (75% on 2.0). There are no storage costs for implicit caching. — [Vertex AI context cache overview](https://cloud.google.com/vertex-ai/generative-ai/docs/context-cache/context-cache-overview) (the Gemini Developer API rate for 3.x Flash was not confirmed)
- **[vendor]** Gemini CLI: per-MCP-server `includeTools` (allowlist) and `excludeTools` (exclude wins). Tool schemas are sanitized (`$schema` and `additionalProperties` removed), and names are truncated at 63 chars. The fetched page has no tool search or deferral. — [Gemini CLI MCP docs](https://geminicli.com/docs/tools/mcp-server/)

### Inferences
- For Jarvis on Gemini Flash, binding a different tool set per turn changes the request's tool block. Under prefix-based implicit caching this probably reduces the cached prefix to whatever comes before the change. Where Gemini serialises tools relative to the system instruction is **not documented** (gap). So "keep the tool list stable; scope with mode ANY/`allowed_function_names` or prompt text" is the cache-safe option, and "rebind a smaller set" saves tokens at the cost of cache hits.
- Jarvis's heartbeat thread at 16 KB core + 38.6 KB skills is likely well past Google's 10–20-tool guidance if those KBs map to many tools. That should be confirmed against the actual tool count.

### Gaps
- No Google statement on whether function declarations count as input tokens (almost certainly yes, since all prompt content is billed, but no source was found) or on where tools sit in the cache prefix.
- No Google accuracy-versus-tool-count data was found.

## OpenClaw, hermes-agent, Letta

### Takeaway
Both OpenClaw and hermes-agent use the progressive-disclosure skill index (name + description, roughly 24–100 tokens per skill) with on-demand body reads. Hermes says outright that skill content is injected as a **user message**, not a system-prompt edit, specifically to keep the cache. OpenClaw snapshots the eligible skill list per session. Neither describes an "unload" mechanism. Disabling is a config action.

### Cited Findings
- **[vendor]** OpenClaw: eligible skills are rendered as "a compact XML block into the system prompt". The cost is "~97 characters + your `name`, `description`, and `location` field lengths… ≈ 24 tokens per skill", plus a base overhead of 195 chars when at least one skill exists. Over budget, it keeps skill identities and shortens descriptions. Tools `skills_search({query,limit?})` and `skills_read({name})` load full SKILL.md content. — [OpenClaw skills docs](https://docs.openclaw.ai/tools/skills)
- **[vendor]** OpenClaw "snapshots eligible skills when a session starts and reuses that list until a refresh trigger" (SKILL.md change, Gateway restart, node connect). Per-agent `skills` allowlist "is the final set". `disable-model-invocation: true` keeps a skill "out of… the model's normal prompt". — [OpenClaw skills docs](https://docs.openclaw.ai/tools/skills)
- **[community]** OpenClaw gating at session start checks required binaries, env vars, config and OS. Skills declare required tools, and agents have tool allowlists. — [ampere.sh blog](https://www.ampere.sh/blog/openclaw-skills-skillmd-loading-precedence)
- **[vendor]** hermes-agent levels: `skills_list()` (~3k tokens of `{name, description, category}`), `skill_view(name)` (full content), `skill_view(name, path)` (reference file). Skill invocation produces "a fresh user message at invocation time…no system prompt mutation", which keeps the prompt cache. Skills can depend on toolsets through `requires_toolsets` / `fallback_for_toolsets`. — [hermes-agent skills docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- **[community]** Hermes cron jobs "can attach zero, one, or multiple skills, and they run in fresh agent sessions rather than inheriting the current chat". A weekly Curator prunes the skill library. — [search-result summary of Hermes docs/guides](https://hermes-agent.nousresearch.com/docs/getting-started/learning-path) (not verified on a primary page this pass)

### Inferences
- The Hermes cron design (fresh session per job, skills attached per job) is the closest analogue to Jarvis's heartbeat. It suggests scoping a skill to a heartbeat *task run* instead of to the long-lived heartbeat thread.

### Gaps
- Letta tool rules: the fetch reached an unrelated page, so there are no sourced details here. From memory (unverified), Letta has InitToolRule, TerminalToolRule, ChildToolRule, ConditionalToolRule and similar rules that constrain which tool may come next. Whether they filter the schemas exposed per step was not confirmed.
- OpenClaw tool profiles and allow/deny lists were not confirmed on a primary page.

## "Tools as code" (Cloudflare Code Mode etc.) and MCP tool overload mitigations

### Takeaway
Cloudflare and Anthropic both argue that turning tool schemas into code APIs (TypeScript modules discovered on demand) beats direct tool calls for large catalogs. Anthropic reports 98.7% token reduction in its example. The cost is a sandbox and operational complexity, which Jarvis may not need at its scale.

### Cited Findings
- **[vendor]** Cloudflare Code Mode (2025-09-26): "LLMs are better at writing code to call MCP, than at calling MCP directly", because models have seen far more real TypeScript than synthetic tool-call examples. The Agents SDK converts MCP schemas into TS interfaces with docs and runs the code in V8 isolates through the Worker Loader API. The sandbox has no internet and reaches the outside only through MCP bindings. — [Cloudflare blog](https://blog.cloudflare.com/code-mode/)
- **[vendor]** Anthropic's code-execution-with-MCP figures (150K → 2K tokens) and caveats, as above. — [Anthropic Engineering](https://www.anthropic.com/engineering/code-execution-with-mcp)
- **[vendor]** Programmatic tool calling: 37% token reduction, knowledge retrieval 25.6% → 28.5%, GIA 46.5% → 51.2%. — [Anthropic advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)

### Gaps
- Specific MCP-client tool caps (for example Cursor's reported ~40-tool limit) and GitHub/VS Code tool-overload issue threads were not sourced this pass.

## Research evidence: tool count / schema size vs accuracy and cost

### Takeaway
Independent studies agree with the vendors: retrieving or shortlisting a small relevant subset beats exposing everything. Reported gains: tool-selection accuracy roughly ×3 (RAG-MCP), up to 35–40% accuracy and 70% latency on small local models (Less-is-More), and adaptive shortlists of about 7 tools matching 50-tool coverage (BoR). Vendor thresholds where degradation starts: Anthropic 30–50 tools, OpenAI <20, Google 10–20.

### Cited Findings
- **[independent]** RAG-MCP (Gan & Sun, 2025-05-06): semantic retrieval of relevant MCP servers before calling the LLM. Prompt tokens cut by "over 50%", and tool-selection accuracy 43.13% vs 13.62% baseline. Includes an "MCP stress test" (curve details not extracted). — [arXiv 2505.03275](https://arxiv.org/abs/2505.03275)
- **[independent]** "Less is More: Optimizing Function Calling for LLM Execution on Edge Devices" (2024-11): fine-tuning-free dynamic tool selection. Up to 70% lower execution time, up to 40% lower power, and 35–40% better success/tool accuracy. Llama3.1-8B q4 fails with the full toolset even though it fits in 16K context, and succeeds when given 19 tools. — [arXiv 2411.15399](https://arxiv.org/pdf/2411.15399) (numbers taken from search-result summaries of the paper; abstract not fetched directly)
- **[independent]** "How Many Tools Should an LLM Agent See? A Chance-Corrected Answer" (Repantis et al., 2026-05-23, rev. 2026-06-07): on BFCL (370 tools), a learned adaptive policy reached 90.3% vs 90.8% coverage while showing 7 tools instead of 50. On ToolBench (3,251 tools), adaptive depth reached 16.7% on hard queries that fixed depth missed. Downstream with Claude Sonnet 4.6, adaptive vs fixed 5-tool lists scored 93.1% vs 87.1%. — [arXiv 2605.24660](https://arxiv.org/abs/2605.24660)
- **[vendor]** Anthropic: selection "degrades once you exceed 30–50 available tools". Tool search accuracy +25 points (Opus 4) and +8.6 points (Opus 4.5). — [Tool search docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool); [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use)
- **[vendor]** Description quality: `input_examples` raised accuracy 72% → 90%. Description edits contributed to Anthropic's SWE-bench SOTA claims. — [advanced tool use](https://www.anthropic.com/engineering/advanced-tool-use); [writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)

### Inferences
- The gains are largest for weaker or smaller models and very large catalogs. Gemini Flash with a few dozen tools sits in the middle, so expect a smaller but real effect. Jarvis would need its own eval to put a number on it.

### Gaps
- BFCL's own published notes on multi-tool / many-tool categories and ToolLLM's retriever numbers (ToolBench, 16k APIs) were not fetched.
- No independent study isolating **schema size (bytes per tool)** from **tool count** was found.

## Patterns for deactivating/unloading tools and their pitfalls

### Takeaway
No major platform documents true per-turn *unloading* of already-loaded tools or skills. The common pattern is **append-only loading** (tool_reference / skill body as a message), with size managed by compaction or context editing, because removing or changing tool definitions invalidates the cache. Where scoping by turn exists, it restricts *callability* while keeping the definitions (OpenAI `allowed_tools`, Gemini ANY + allowed names), or it starts fresh sessions per job (Hermes cron).

### Cited Findings
- **[vendor]** Anthropic: modifying tool definitions invalidates the whole cache. Changing `tool_choice` invalidates only the messages cache. Tool search adds tools without breaking the cache. — [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)
- **[vendor]** Anthropic context editing clears old tool *results* (not definitions) and invalidates the cache at the clear point, so `clear_at_least` is used to batch clears. — [Context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing)
- **[vendor]** Claude Code: skill content "stays there across later turns". It is re-attached after compaction within a 5K-per-skill / 25K-total budget, and permissions are turn-scoped while content is session-scoped. — [Claude Code skills](https://code.claude.com/docs/en/skills)
- **[vendor]** OpenAI: `allowed_tools` keeps the full list for caching and restricts the callable subset. Tools discovered by search are appended at the end of context. — [function calling](https://developers.openai.com/api/docs/guides/function-calling); [tool search](https://developers.openai.com/api/docs/guides/tools-tool-search)
- **[vendor]** Hermes: skills load as a user message, with no system-prompt mutation. Cron runs in fresh sessions. — [hermes-agent skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
- **[vendor]** Gemini explicit caches freeze tools/tool_config into the cache object, so a changed tool set means a new cache. — [Google AI forum](https://discuss.ai.google.dev/t/structured-output-support-with-cached-content/41525)

### Inferences
- Pitfalls with Jarvis-style "activate and stay bound":
  - Tool sets differ by thread and by activation, which fragments Gemini implicit cache prefixes.
  - Any deactivation or rebinding changes the tool block mid-thread and costs cache hits.
  - If a skill is unloaded while history still holds its earlier calls, the model can see calls to tools that no longer exist. Anthropic avoids this by keeping definitions present, and Jarvis would need to do the same or strip them.
  - A tool that is gone from the schema but still mentioned in prompts or history invites hallucinated calls. Claude Code mitigates this by keeping names visible and loading schemas through search.
- For the heartbeat, the cheapest documented-pattern equivalents are:
  - Scope skills per due task within a tick, as Hermes cron does with fresh sessions.
  - Keep a stable superset bound and restrict with mode ANY + allowed function names, which keeps the cache but not the token count.
  - Build a client-side "load tool" step that appends definitions. Gemini has no `tool_reference` equivalent, so in practice this changes the tool block, and the cache benefit Anthropic and OpenAI get is not available on Gemini.

### Gaps
- No published "skill decay" (time- or turn-based auto-deactivation) mechanism was found on any surveyed platform. Its existence elsewhere is unconfirmed.
- No vendor data quantifies "model forgets a deferred tool exists". Anthropic's advice to add a system-prompt section listing tool categories is the documented mitigation.
