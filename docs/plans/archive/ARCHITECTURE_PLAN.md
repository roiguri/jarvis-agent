# Jarvis ↔ OpenClaw — Architecture Review & Refactor Plan

## Context

A hard, honest comparison between Jarvis (current) and OpenClaw (the reference architecture you want to learn from), with the explicit constraint: **Jarvis stays sandboxed**. Unlike OpenClaw, Jarvis must not get a terminal, exec, or arbitrary code execution. The goal is to import OpenClaw's *organizational* patterns (gateway abstraction, scoped/dynamically-loaded tools, file-driven identity) without importing its *capability surface* (host execution, plugin marketplace).

**Concrete near-term targets driving this:** email channel, WhatsApp channel, and a custom app. The custom app is *not* a chat channel — it has a UI (memory panel, etc.) that needs to read agent state directly. That distinction matters: it's a separate read API, deferred to its own milestone after the chat-channel architecture lands.

**How to use this plan:** each phase is self-contained. Read only the phase you're executing.

- **Phase 1** — Decouple gateway (chat channels). Pure refactor. Touches every tool's gateway access.
- **Phase 2** — Tool registry, scoped loading, **same-turn skill activation via `StateGraph`** (replaces `create_agent`). Solves the "cooking shouldn't see gym tools" problem.
- **Phase 3** — Memory architecture cleanup + file-driven identity prompt. Two halves; do both because they share the workspace surface.

Phases must run in order: Phase 2 depends on Phase 1's tool-context injection; Phase 3 depends on Phase 2's tool registry.

Background sections that don't change behavior but inform the phases (skim before Phase 2 if you haven't recently):
- **Appendix: How OpenClaw Orchestrates** — Pi core, plugin hooks, queue modes
- **Concurrency Model** — three flows for heartbeat ↔ user, and what to do when heartbeat fires mid-conversation
- **Checkpointing — What It's For** — role of `threads.sqlite` vs `chat_history.jsonl`
- **Context Lifecycle** — when `active_skills` resets, when memory persists
- **Voice / Phone Channel** — what voice forces us to plan for now (streaming-aware Channel ABC)
- **Runtime Alternatives Considered** — why Option A (drop `create_agent`, use `StateGraph`) wins
- **Tool Data Location** — where tool-owned DBs live and why
- **Backups & Disaster Recovery** — what we lose today and the proposed safety net

---

## Prerequisites for the Implementing Agent

Each phase will be picked up by a separate agent session. Before starting *any* phase, the implementing agent should:

### 1. Read these files first (whichever phase you're on)
- [CLAUDE.md](../../../CLAUDE.md) — repo-level conventions, deployment workflow, hard constraints (especially: never read `/app/secrets/.env`).
- [DEVELOPMENT.md](../../../DEVELOPMENT.md) — architecture reference. Update it alongside any phase that changes structure.
- This document, end to end. Each phase has its own "Reference reading" subsection naming the additional files specific to that phase.

### 2. Understand the runtime stack (versions in [requirements.txt](../../../requirements.txt))
- **LLM:** Google Gemini via `langchain-google-genai`. Model: `gemini-3-flash-preview` ([agent.py:120](../../../agent.py#L120)). Temperature 0.2.
- **Agent framework:** LangChain agents + LangGraph. Currently uses `create_agent` (LangChain prebuilt) over a custom `JarvisState` and `PruningSqliteSaver`. Phase 2 drops the prebuilt for a hand-rolled `StateGraph`.
- **Channel:** python-telegram-bot (PTB) v20+, async API.
- **Scheduler:** APScheduler 3.x with `IntervalTrigger` (heartbeat, hourly) and `DateTrigger` (one-shot reminders).
- **Persistence:** SQLite (LangGraph checkpointer + soon fitness DB). No vector store. No external DB server.

### 3. Deployment workflow (from CLAUDE.md, repeated for emphasis)
After every code change:
```
pct exec 106 -- systemctl restart jarvis.service
pct exec 106 -- journalctl -u jarvis -n 100
```
Then send a Telegram message to verify behavior. Restart-test-iterate is the inner loop. Do NOT skip the Telegram verification — type checking and unit tests don't catch LLM-behavior regressions.

### 4. Hard rules
- Never read `/app/secrets/.env`. If you need to know which keys exist, read the code that uses them.
- Don't break the memory sandbox (`_get_safe_path` in [tools/memory_tools.py](../../../tools/memory_tools.py)). The agent must not be able to escape `/app/jarvis_memory/` via memory tools.
- Don't introduce `os.system`, `subprocess` with shell=True, or any code-exec capability for the agent. Sandbox boundary is a hard line.
- Each phase ends with the verification protocol in its "Verification" subsection. Don't skip; don't claim done without running it.

### 5. When in doubt, ask the user
Before making any non-obvious choice that isn't covered in this plan, surface it. The plan was written deliberately to leave certain things open (e.g. exact AGENTS.md final wording, USER.md seed content, fitness.sqlite migration ordering). Those are user decisions, not implementer decisions.

### 6. External references you'll consult repeatedly
OpenClaw documentation:
- Channels overview: https://docs.openclaw.ai/channels/index.md
- Channel routing: https://docs.openclaw.ai/channels/channel-routing.md
- Telegram channel: https://docs.openclaw.ai/channels/telegram.md
- Agent runtime: https://docs.openclaw.ai/concepts/agent.md
- Agent loop: https://docs.openclaw.ai/concepts/agent-loop.md
- Memory: https://docs.openclaw.ai/concepts/memory
- Skills: https://docs.openclaw.ai/tools/skills.md
- Exec approvals (confirmation pattern): https://docs.openclaw.ai/tools/exec-approvals.md
- Default AGENTS.md (full template): https://docs.openclaw.ai/reference/AGENTS.default.md
- Templates: SOUL.md, AGENTS.md, USER.md, IDENTITY.md, TOOLS.md, HEARTBEAT.md, BOOTSTRAP.md under https://docs.openclaw.ai/reference/templates/

LangGraph / LangChain (versions track requirements.txt; URLs may shift — search if a link 404s):
- LangGraph `StateGraph` reference: https://langchain-ai.github.io/langgraph/reference/graphs/
- LangGraph prebuilt `create_agent`: https://langchain-ai.github.io/langgraph/agents/agents/
- LangGraph checkpointer: https://langchain-ai.github.io/langgraph/concepts/persistence/
- LangChain `bind_tools` on chat models: https://python.langchain.com/docs/how_to/tool_calling/
- LangGraph streaming (`astream_events`): https://langchain-ai.github.io/langgraph/how-tos/streaming/

Treat OpenClaw URLs as authoritative for *patterns* (their docs change less often). Treat LangGraph URLs as starting points; if behavior doesn't match, check the installed version's source.

---

## The Honest Verdict

You're right. Jarvis is a Telegram-shaped monolith with a thin LangGraph wrapper. The good ideas (SOUL.md, MEMORY.md, heartbeat, memory sandbox) sit on top of a structure that fights you the moment you try to add a second channel, scope a tool, or reuse the agent for anything but "answer Roi on Telegram."

Concrete failures in the current code:

1. **No gateway interface.** [gateway/telegram_gateway.py](../../../gateway/telegram_gateway.py) *is* the gateway. Tools `from gateway.telegram_gateway import get_gateway` directly ([tools/memory_tools.py:35](../../../tools/memory_tools.py#L35), [tools/media_tools.py:405](../../../tools/media_tools.py#L405), and 6 other sites). A second channel means duplicating `ConfirmationManager`, `InboundRouter`, media download, and patching every tool.

2. **All 39 tools always loaded.** [tools/__init__.py:58-108](../../../tools/__init__.py#L58-L108) is one flat list passed to `create_agent()` once at module load ([agent.py:237-243](../../../agent.py#L237-L243)). When you discuss cooking, the LLM still sees Sonarr/Radarr/Arbox/heartbeat tools. Heartbeat sees `delete_sonarr_series_with_files`. Every turn pays for every tool's prompt overhead.

3. **Single global `agent_executor` singleton** ([agent.py:237-243](../../../agent.py#L237-L243)). Same prompt, same tools, same model for every thread. No way to vary per context.

4. **System prompt is hardcoded text** in [agent.py:127-228](../../../agent.py#L127-L228) (~100 lines) duplicated against tool docstrings. Time context is jammed into the user message in [main.py:60-62](../../../main.py#L60-L62) instead of into the prompt envelope.

5. **`ConfirmationManager` is Telegram PTB types** ([gateway/telegram_gateway.py:391-541](../../../gateway/telegram_gateway.py#L391-L541)). It also has a leak: `_cleanup_expired` only fires inside `handle_callback`; un-clicked confirmations live forever in `_pending`.

6. **Module-level singletons everywhere**: `_gateway_instance`, `agent_executor`, `_scheduler`. No DI, no test seams.

7. **Telegram-specific concerns leak into routing**: media-group batching, authorization (`ALLOWED_USER_ID`), and dispatch all live in the same router class.

8. **Tool docstrings duplicated in `_BASE_SYSTEM_PROMPT`**. Source of truth is split.

9. **Hardcoded internal Jellyfin address** (`jellyfin.local:8096`) in [gateway/notifier.py:16](../../../gateway/notifier.py#L16).

What works and should be preserved: `_get_safe_path` memory sandbox, SOUL.md / MEMORY.md split, heartbeat thread isolation, `InboundMessage` dataclass.

---

## OpenClaw Patterns We Are Importing

| OpenClaw pattern | What it solves | Jarvis adaptation |
|---|---|---|
| **Gateway as coordinator + channel plugins** ([channels overview](https://docs.openclaw.ai/channels/index.md)) | Channels are interchangeable; replies route back to source | Extract `Channel` ABC; Telegram becomes one implementation; email/WhatsApp slot in. |
| **Bootstrap files: SOUL/AGENTS/IDENTITY/USER/TOOLS.md auto-injected at session start** ([agent runtime](https://docs.openclaw.ai/concepts/agent.md)) | System prompt is assembled from files, not hardcoded | Already have SOUL.md and MEMORY.md. Add AGENTS.md (operating rules) and USER.md (Roi profile). Delete `_BASE_SYSTEM_PROMPT`. |
| **Compact skill list in prompt + on-demand activation** ([skills](https://docs.openclaw.ai/tools/skills.md)) | LLM only sees skill *names* until invoked; full schema loaded later | Two-tier tools: core always-on, skills advertised compactly and activated per conversation via `activate_skill(...)`. **This is the answer to your "cooking conversation shouldn't see gym tools" concern.** *Extended hierarchically:* a skill may have sub-skills (`media/radarr`), advertised one level deeper with the same names-first/schemas-on-demand rule — the first intentional divergence from OpenClaw's flat skill model (see [RUNTIME.md](../../architecture/RUNTIME.md) "Sub-skills / nested namespaces"). |
| **Per-agent allowlists, not auto-load** ([skills](https://docs.openclaw.ai/tools/skills.md)) | "A non-empty allowlist replaces, rather than merges with, default skills" | Tool registry with `scopes=["user","heartbeat"]`; user gets full surface, heartbeat gets a narrow whitelist. |
| **Confirmation as policy + allowlist + user approve** ([exec approvals](https://docs.openclaw.ai/tools/exec-approvals.md)) | Confirmation is cross-cutting | `Confirmation` ABC; tools opt in via `@destructive` decorator; channel implements UI. |
| **Channel-agnostic message schema** | Routing back to source is automatic | `InboundMessage` already exists; make it the *only* type tools/agent see. |

---

## Appendix: How OpenClaw Orchestrates (general knowledge, for context)

You asked what OpenClaw's orchestration layer looks like. Brief tour, not used in the plan but useful to understand the gap between their gold-standard runtime and ours:

- **`runEmbeddedPiAgent`** is the agent loop entry point. Pipeline: validation → session resolution → model + skills loading → embedded execution → lifecycle event emission.
- **Streaming-first**: assistant deltas, tool start/update/end, and reasoning streams are independent event channels. `subscribeEmbeddedPiSession` bridges Pi events into OpenClaw's stream surfaces (assistant, tool, lifecycle).
- **Plugin hooks** at well-defined points: `before_model_resolve`, `before_prompt_build`, `before_agent_reply`, `before_tool_call`, `after_tool_call`. Plugins inject context, override decisions, or generate synthetic replies without monkey-patching the loop.
- **Sessions = JSONL transcripts** at `~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl` with a file-based, process-aware write lock. Stable session IDs across restarts. Auto-compaction via lifecycle events. **This is OpenClaw's "checkpointer."** Our LangGraph SQLite checkpointer fills the same role; OpenClaw's choice is simpler (JSONL + lock) but functionally equivalent.
- **Per-session + global queue lanes** for serialization. Channels pick a queue mode:
  - `collect` — hold inbound messages until turn ends, then start a new turn with all collected.
  - `steer` — inject inbound messages *into the current run* between LLM calls (after tool execution, before next LLM call). Most aggressive.
  - `followup` — hold messages until the turn ends, then start a new turn.
- **Default AGENTS.md sections** worth borrowing for our AGENTS.md template: First Run Setup, Safety Defaults, Session Start (mandatory read of SOUL.md / USER.md / memory), Soul (Identity), Shared Spaces, Memory System, Tools and Skills, Backup recommendation.

Why we don't need most of this:
- We're not multi-tenant; no plugin hooks SDK needed.
- We don't need real-time streaming (Telegram doesn't really stream).
- One user means we don't need shared-space safety; standard auth is fine.
- Queue modes (steer/collect/followup): see "Concurrency model" below — for one user we don't actually have the conflict OpenClaw is solving.

What *is* worth borrowing: the AGENTS.md section structure, the "session = JSONL transcript with write lock" pattern (matches our `chat_history.jsonl` + checkpointer pair), and the `before_prompt_build` hook concept (we already get this for free via the prompt builder in Phase 3).

---

## Concurrency Model (heartbeat ↔ user conversations — refined per your feedback)

You correctly pushed back: there's a real difference between "heartbeat sends a one-shot reminder" and "heartbeat starts a real conversation." Example you gave: heartbeat wakes up, sees a workout tonight, kicks off a goal-setting chat ("what are you focusing on tonight?"). That's not a notification — it's a conversation that needs to thread with whatever Roi says back.

There are three distinct flows. Each needs a different treatment:

### Flow 1 — Heartbeat one-shot notification (low-richness)
Examples: "your CrossFit class is in 30 min," "you have a haircut tomorrow."

- Heartbeat scope generates the message → sends via `channel.send()` → appends a one-line note to today's daily log: `[21:00 heartbeat→user] Reminder: CrossFit at 22:00.`
- If the user replies, their reply enters the user thread normally. The user-scope agent has today's daily log auto-loaded (Phase 3 Half B), so it sees the note and connects the dots.
- No state merge needed. This is what I originally called Option A.

### Flow 2 — Heartbeat-initiated conversation (high-richness — your workout-goals example)
Heartbeat opens a real dialog. The user-scope agent must *fully* see the heartbeat's outbound, not just a one-line summary.

**Mechanism: heartbeat-initiated outbound is injected into the user thread's checkpoint as an `AIMessage`.**

```python
async def heartbeat_open_conversation(text: str):
    # 1. Send to user via the channel where they expect Jarvis (default: Telegram)
    await default_channel.send(USER_CHAT_ID, text)
    # 2. Inject as AIMessage into user thread's LangGraph state
    cfg = {"configurable": {"thread_id": USER_THREAD_ID}}
    state = checkpointer.get(cfg)
    state.values["messages"].append(AIMessage(content=text))
    checkpointer.put(cfg, state.values, ...)
    # 3. Optionally append a one-line note to today's daily log for the audit trail
```

When Roi replies via Telegram, the reply lands in the user thread normally. The agent sees:
```
AIMessage: "Hey, you've got CrossFit at 22:00. What are you focusing on tonight?"   ← injected by heartbeat
HumanMessage: "shoulders"                                                            ← Roi's reply
```
Continuity is automatic. No daily-log archeology needed.

**Heartbeat decides which flow:** the heartbeat agent's prompt includes both `send_notification(text)` (Flow 1) and `start_conversation(text)` (Flow 2) as tools. It picks based on the task ("haircut reminder" → notification; "workout goals chat" → conversation).

### Flow 3 — Heartbeat fires mid-conversation
Roi is actively chatting with Jarvis. Heartbeat tick fires. What now?

Three options, configurable per heartbeat task:

| Mode | Behavior | Use when |
|---|---|---|
| `skip` | If user thread has activity in last N minutes (default 5), heartbeat tick is no-op. Re-runs next cycle. | Most heartbeat tasks. Avoids interrupting a live conversation. |
| `defer` | Append the heartbeat's intended message to today's daily log; do NOT send. User-scope agent sees it next turn and decides whether to surface it. | Soft suggestions ("you might want to log yesterday's run"). |
| `interrupt` | Send anyway, inject into user thread (Flow 2). Comes through as a "by the way" mid-conversation. | Time-critical ("class starts in 5 minutes"). |

Default per task is set in HEARTBEAT.md alongside the task definition. Heartbeat reads `last_user_activity_at` (cheapest source: max timestamp in `chat_history.jsonl` for that thread, or a small `last_activity.json` file the gateway updates) before each task.

### Where actual races CAN happen

1. **Both threads writing to the same memory file** (heartbeat updating `daily_<today>.md` while user thread, mid-tool-call, also writes to it). OpenClaw's solution: file-based, process-aware write lock. **Our fix:** add an `flock`-based lock around `write_memory`'s file write in [tools/memory_tools.py](../../../tools/memory_tools.py). Phase 3 Half A.

2. **Heartbeat injecting into user thread's checkpoint while a user turn is mid-flight.** LangGraph's checkpointer is SQLite — concurrent writes would race. Mitigation: serialize injections through an asyncio `Lock` keyed by thread_id; or only inject when the user thread is "idle" (no in-flight run). Phase 2 implementation detail.

OpenClaw solves these via per-session queue lanes + steer mode. We don't need that machinery for one user, but we need the equivalent of a thread-level lock around checkpoint writes. Cheap.

---

## Checkpointing — What It's For (your question)

LangGraph's checkpointer ([agent.py:87-102](../../../agent.py#L87-L102), `PruningSqliteSaver` over `threads.sqlite`) stores the agent's *current* state per thread:
- Message list (conversation history within the sliding window)
- Custom state fields (after Phase 2: `active_skills`)
- Tool-call results in flight (so a crash mid-loop can resume)

**Without it:** every user message starts a brand-new agent with no memory of the prior conversation. Jarvis would forget what you said 30 seconds ago.

**With it:** the LangGraph state persists between turns and across restarts. The user can pick up a conversation hours later.

Our `PruningSqliteSaver` keeps exactly *one* checkpoint per thread (the latest), which is why `threads.sqlite` doesn't grow unbounded despite long history. Smart engineering, worth preserving.

**OpenClaw equivalent:** JSONL session transcripts at `~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl` with file write locks. Functionally identical role — different storage choice. Both are mandatory for any agent that retains state between user messages.

**chat_history.jsonl is NOT a replacement.** It's an append-only audit log used by `get_chat_history` for queries ("what did the user say last Tuesday?"). It doesn't capture LangGraph state (active_skills, in-flight tool calls, message reducer behavior). We keep both, with clear roles:
- `threads.sqlite` (checkpointer): "what does the agent currently know" — single source of truth for next-turn state.
- `chat_history.jsonl` (audit log): "what was said when" — queryable history, 90-day retention.

The 5.8MB WAL is just SQLite housekeeping (uncheckpointed writes). Not urgent; Phase 3 Half A will add a periodic `PRAGMA wal_checkpoint(TRUNCATE)`.

---

## Context Lifecycle (your question: when does the agent reset?)

Currently: never automatically. Same `agent_executor` lives forever; conversation state lives forever (single pruned checkpoint per thread); tool surface is fixed.

After Phase 2 (`active_skills` is per-thread state):

| Trigger | What resets |
|---|---|
| **Service restart** | Process state (`agent_executor` if it were a singleton; we're moving to per-turn rebuild). LangGraph checkpoint **survives** (it's in SQLite). active_skills survives. |
| **Per turn** | Agent graph is rebuilt (cheap; `StateGraph.compile()` is in-memory). Tool bindings reflect current `active_skills`. Messages are added to the existing checkpoint. |
| **Sliding window** | When messages exceed `MAX_MESSAGES=50`, oldest are dropped via `_add_and_trim`. Reducer ensures we don't break mid-tool-call. |
| **Explicit reset (proposed)** | User says `/reset` or "start over" → clear messages and active_skills, keep memory files. |
| **Idle timeout (proposed)** | If the thread has had no message in N hours (start with N=12), next message auto-clears `active_skills` (but keeps message history). Rationale: yesterday's "I activated media to find a show" shouldn't pollute today's tool surface. |
| **Heartbeat thread** | One thread for all heartbeat turns; messages accumulate per the sliding window. State carries between heartbeat ticks (which is a feature: heartbeat can remember "I asked Roi yesterday whether..."). |

**Recommendation:** ship Phase 2 with no idle timeout, just per-turn rebuild. Add the idle timeout in a small follow-up if `active_skills` actually grows stale in practice. KISS.

---

## Runtime Alternatives Considered (Phase 2 Prerequisite)

You pushed back — correctly — on the "next-turn activation is acceptable" hand-wave. To get **same-turn** activation, we need to know what the runtime allows. Here's the honest evaluation.

### The actual constraint

LangGraph's high-level helper `create_agent(model, tools=[...])` calls `model.bind_tools(tools)` once at construction. The compiled graph then loops `LLM → ToolNode → LLM → ToolNode → END` with that fixed tool set. To swap tools mid-loop, the LLM call has to re-bind — which `create_agent`'s prebuilt graph doesn't do.

But — and this matters — that's a property of `create_agent`, not LangGraph. **The lower-level `StateGraph` API lets you control the LLM node directly**, which means re-binding per call.

### Option A — Drop `create_agent`, use `StateGraph` directly *(chosen)*

You asked for more detail on construction. Full sketch with the decisions called out:

```python
# agents/graph.py
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode  # we can reuse this — it's separate from create_agent

def make_llm_node(llm, registry, prompt_builder):
    def llm_node(state: JarvisState):
        # Decision 1: where do scope and active_skills live?
        # Answer: in JarvisState. Set once when the thread is first created;
        # active_skills evolves over the conversation.
        scope = state.get("scope", "user")
        active = state.get("active_skills", set())

        # Decision 2: rebuild the system prompt every LLM call?
        # Answer: yes. It's cheap (just string concat from a few files), and it lets
        # AGENTS.md/SOUL.md edits hot-take effect without restart for the user thread,
        # though we still recommend restart for AGENTS.md.
        system_prompt = prompt_builder.build(scope, active)

        # Decision 3: rebind tools every LLM call?
        # Answer: yes. This is the whole point of dropping create_agent.
        # The cost is one bind_tools() call (in-memory, microseconds).
        bound_tools = registry.get_tools(scope=scope, active_skills=active)
        bound_llm = llm.bind_tools(bound_tools)

        messages = [SystemMessage(content=system_prompt)] + state["messages"]
        response = bound_llm.invoke(messages)
        return {"messages": [response]}
    return llm_node

def make_tool_node(registry):
    # Custom tool node so we can mutate state.active_skills from within activate_skill.
    # The prebuilt ToolNode doesn't support arbitrary state updates; ours does.
    def tool_node(state: JarvisState):
        last = state["messages"][-1]
        new_messages = []
        new_active = set(state.get("active_skills", set()))
        for tc in last.tool_calls:
            tool = registry.find(tc["name"], scope=state["scope"], active_skills=new_active)
            if tool is None:
                new_messages.append(ToolMessage(
                    content=f"Tool {tc['name']} not available. Activate the skill first.",
                    tool_call_id=tc["id"],
                ))
                continue
            result = tool.invoke(tc["args"])
            # Decision 4: how does activate_skill mutate state?
            # Answer: activate_skill returns a tuple/dict signaling activation.
            # The tool_node inspects and updates new_active. Tools that don't
            # touch active_skills just return strings.
            if isinstance(result, dict) and "_activate" in result:
                new_active |= set(result["_activate"])
                content = result.get("content", "")
            elif isinstance(result, dict) and "_deactivate" in result:
                new_active -= set(result["_deactivate"])
                content = result.get("content", "")
            else:
                content = result
            new_messages.append(ToolMessage(content=content, tool_call_id=tc["id"]))
        return {"messages": new_messages, "active_skills": new_active}
    return tool_node

def build_graph(llm, registry, prompt_builder, checkpointer):
    g = StateGraph(JarvisState)
    g.add_node("llm", make_llm_node(llm, registry, prompt_builder))
    g.add_node("tools", make_tool_node(registry))
    g.add_conditional_edges(
        "llm",
        lambda s: "tools" if getattr(s["messages"][-1], "tool_calls", None) else END,
    )
    g.add_edge("tools", "llm")
    g.set_entry_point("llm")
    return g.compile(checkpointer=checkpointer)
```

**Construction decisions to make in implementation:**

1. **One graph compiled once at startup, OR rebuild per turn?** Rebuild *bindings* per turn (LLM + tools), but compile the *graph* once at startup. Compilation is cheap-ish; bindings are even cheaper. Single `compiled_graph` module-level (replaces `agent_executor`), invoked per turn with thread config. `JarvisState["scope"]` and `JarvisState["active_skills"]` are read inside nodes.

2. **State schema additions:** add `scope: str`, `active_skills: set[str]` to JarvisState. Reducers: scope is set on first-turn (no reducer; just stays); active_skills uses set-union semantics (a tool can add to it, a tool can remove from it, otherwise it persists).

3. **System prompt as SystemMessage prepended in node, NOT compiled into graph.** Lets us edit AGENTS.md/SOUL.md and have it take effect on the next turn without restart. (Still recommend restart for AGENTS.md to flush prompt cache.)

4. **Backward compat for `ask_jarvis()`:** keep the function signature, internally route to the new graph.

**Pros:**
- Same-turn activation works naturally. `activate_skill` updates state → next `llm_node` invocation re-binds → new tools immediately available.
- ~100–150 lines total. No framework migration.
- Keep checkpointer, JarvisState, reducer, all current memory infra.
- Full control over the loop. Adding hooks (confirmation routing, telemetry, mid-loop streaming) is straightforward.

**Cons:**
- We lose `create_agent`'s automatic streaming and prebuilt tool error handling. We can copy the patterns we need from LangGraph's source (it's small).
- Slightly more code to maintain. The trade is that this code does what we need; `create_agent` doesn't.

### Option B — Stay on `create_agent`, use a dispatch meta-tool

Always-bound tools: core + a single `call_skill(namespace, tool_name, args_json)` dispatcher. The LLM passes `args_json` as a string; the dispatcher parses, validates against the real tool's schema, and calls it.

**Pros:** No graph rewrite.
**Cons:** LLM has to format JSON inside JSON. No tool-schema autocomplete from the model's tool-call training. Worse reliability with Gemini specifically (we've observed it struggle with nested-JSON tool args). Rejected.

### Option C — Migrate to PydanticAI

Type-safe agent framework, modern, decent docs.
**Pros:** Cleaner ergonomics. Built-in dependency injection.
**Cons:** Same compile-time tool binding limitation as `create_agent`. Migration cost: rewrite every tool, lose LangGraph checkpointer and our `PruningSqliteSaver`, lose `_strip_media_blobs` reducer. **No structural advantage over Option A.** Rejected.

### Option D — Drop frameworks, raw Anthropic/Gemini SDK loop

Pure Python: call LLM with `tools=[...]`, parse tool calls, execute, append, repeat.
**Pros:** Maximum control.
**Cons:** Re-implement checkpointing, message reducers, state schema, and conversation persistence. Months of work for what `StateGraph` gives us in days. Rejected unless LangGraph itself becomes a problem (it isn't yet).

### Decision

**Option A.** It solves the actual constraint (mid-loop tool re-binding) without abandoning what already works. Phase 2 is rewritten below to use `StateGraph` directly.

This is not "we don't want to write the orchestration." It's ~100 lines and it's the right move.

---

## OpenClaw Patterns We Are NOT Importing — and Why

### 1. Plugin SDK / manifest format / hot-loading

**How OpenClaw does it:** Plugins live in `extensions/*` with a manifest, an SDK split between channel-plugins and provider-plugins, hooks for lifecycle events ([plugin docs](https://docs.openclaw.ai/plugins/architecture.md)). Bundled and community plugins coexist, can be installed at runtime, and advertise prefixes (`telegram:123`) so the gateway can route to them.

**Why Jarvis doesn't need this:** OpenClaw is a multi-tenant platform where third parties write plugins. Jarvis is one repo with one author and one user. A plugin manifest, hot-loading, and an SDK boundary add infrastructure (manifest schema, version compatibility, sandbox boundaries) we never recover the cost of. **In-process registration via `@tool_register` and direct imports is sufficient.** If Jarvis ever opens to other users, revisit.

### 2. Vector / embedding memory (`memory_search`)

**How OpenClaw does it:** A `memory_search` tool uses hybrid search (vector similarity + keyword) when an embedding provider is configured. Backends include SQLite, QMD, Honcho, LanceDB ([memory docs](https://docs.openclaw.ai/concepts/memory)). Optional "dreaming" process consolidates short-term signals into durable memory.

**Why Jarvis doesn't need this:** ~50 markdown memory files at most. `grep` is faster than embedding lookup at this scale, and embedding adds: an embedding provider dependency, a vector store, indexing on every write. Markdown + `read_memory`/`list_memory` covers the use cases. **Revisit only if Jarvis grows past ~500 memory files or you want semantic recall across daily logs.**

### 3. Host execution tools (read/exec/edit/write/browser)

**How OpenClaw does it:** Built-in tools that read files anywhere on the host, execute shell commands, edit code, control a browser ([agent runtime](https://docs.openclaw.ai/concepts/agent.md)). Subject to a layered approval system (policy + allowlist + user confirmation, [exec approvals](https://docs.openclaw.ai/tools/exec-approvals.md)). This is what makes OpenClaw a general-purpose assistant.

**Why Jarvis explicitly rejects this:** **Your stated constraint** — Jarvis must stay narrow and controlled. An LLM with shell access on a Proxmox host running personal services is a different risk class. Keep memory-tool sandboxing (`_get_safe_path`) intact. The cost is that Jarvis can't do general "look at this file and fix it" tasks; that's the right trade.

### 4. Multi-agent routing hierarchy (peer → guild → team → default)

**How OpenClaw does it:** Routes messages to agents via priority chain: exact peer match → parent peer → guild+roles → guild → team → account → channel → default agent ([channel routing](https://docs.openclaw.ai/channels/channel-routing.md)). Different agents per workspace, per Discord guild, per team.

**Why Jarvis doesn't need this:** One user (Roi). One identity. Routing logic is overhead with zero payoff. **The user/heartbeat split we DO need is just two scopes, not a routing hierarchy.** If Jarvis ever serves a household, revisit — but the structure would still be much simpler than OpenClaw's.

### 5. Pi agent core / custom runtime

**How OpenClaw does it:** OpenClaw runs on a "Pi agent core" — its own runtime for models, tools, and prompt pipeline ([agent runtime](https://docs.openclaw.ai/concepts/agent.md)). Custom orchestration, custom streaming, custom tool dispatch.

**Why Jarvis stays on LangGraph (but drops `create_agent`):** Replacing the runtime entirely would be a 6-month detour. But the prebuilt `create_agent` helper *does* block our same-turn skill activation. We're keeping LangGraph and dropping down to its lower-level `StateGraph` API (see Runtime Alternatives Considered). This gets us full control over the agent loop without rewriting checkpointer, state, or message handling. ~100 lines of replacement code.

### 6. Plugin-based channels with manifest registration

**How OpenClaw does it:** Channels (Telegram, WhatsApp, Discord, Signal, iMessage, etc.) are plugins under `extensions/*` with manifest, lifecycle hooks, and prefix advertising ([building channel plugins](https://docs.openclaw.ai/plugins/sdk-channel-plugins.md)).

**Why Jarvis uses subdirectories instead:** Same answer as #1 — single repo, single author. We get 95% of the value (modular, swappable, testable) by using `gateway/<channel>/` subdirectories with a `Channel` ABC, without the manifest/SDK boilerplate. If we ever open Jarvis to community channels, the ABC is already the seam.

---

## The Plan — Three Phases

### Phase 1 — Decouple the gateway

> **STATUS: ✅ COMPLETE** (branch `refactor/gateway`, commits `3eeb5bf` cutover + `bbb9a18` notifier decouple/relocate). Executed as a single clean cutover (not the staged wave plan below — the staged interfaces-behind-monolith approach was abandoned mid-way because it left the abstraction decorative; see commit history). Live-tested: full webhook suite + Confirm/Cancel + gate. Notifier additionally decoupled from agent/tools and moved to `gateway/webhook/`. Pre-existing `ask_jarvis_once` list-content bug fixed in passing. The prose below is retained as the original design rationale.

**Scope:** chat channels only. Read API for the custom app is **deferred** to its own milestone (see "Deferred Milestones" below). The chat architecture has to land first.

**Goal:** A tool or agent never imports `telegram_gateway`. Adding email/WhatsApp becomes a new file under `gateway/<channel>/`.

#### Reference reading (read these before designing the refactor)

Existing code:
- [gateway/telegram_gateway.py](../../../gateway/telegram_gateway.py) — full file. Currently 541 lines that need to be split. Note: `InboundMessage` dataclass at line 50, `ConfirmationManager` class at lines 391-541, `TelegramInboundRouter` with media-group batching at 188-385.
- [gateway/notifier.py](../../../gateway/notifier.py) — Sonarr/Radarr notification batching. Stays gateway-internal but moves under `gateway/telegram/`.
- [gateway/webhook_server.py](../../../gateway/webhook_server.py) — currently a separate webhook handler; becomes its own `Channel` subclass.
- [main.py](../../../main.py) — see [main.py:101-138](../../../main.py#L101-L138) for the current Telegram wiring; this is what `factory.create_channels()` will replace.
- Every `get_gateway()` callsite. Find them: `grep -rn "get_gateway\|from gateway.telegram_gateway" /app/jarvis_code --include="*.py"`. There are ~10. Each must lose the direct import.

OpenClaw references:
- Channels overview — https://docs.openclaw.ai/channels/index.md (the abstraction we're mirroring)
- Channel routing — https://docs.openclaw.ai/channels/channel-routing.md (deterministic-not-model-driven routing back to source channel)
- Telegram channel specifics — https://docs.openclaw.ai/channels/telegram.md
- Exec approvals — https://docs.openclaw.ai/tools/exec-approvals.md (the confirmation abstraction; layered policy + allowlist + user approve)

Library docs:
- python-telegram-bot v20 application/handlers — https://docs.python-telegram-bot.org/en/stable/telegram.ext.application.html. Specifically `MessageHandler`, `CallbackQueryHandler`, `Application.run_polling()`.

Open questions to confirm with user before implementing:
- Should the deprecated `get_gateway()` shim live for one cycle (preserves backward compat during the move) or be deleted in a single PR?
- Is the email channel's auth model "single allowlisted email address" (mirror Telegram's `ALLOWED_USER_ID`) or richer? Default to single-allowlist unless told otherwise.

**New structure:**
```
gateway/
├── base.py              # Channel ABC, Confirmation ABC, InboundMessage (moved here)
├── factory.py           # Loads enabled channels from config/env
├── confirmation/
│   ├── base.py          # Confirmation ABC: await request_confirmation()
│   └── manager.py       # In-memory store + TTL cleanup background task
├── telegram/
│   ├── channel.py       # TelegramChannel(Channel)
│   ├── router.py        # PTB handlers, auth, media-group batching
│   ├── confirmation.py  # TelegramConfirmation — InlineKeyboard UI
│   └── notifier.py      # (moved from gateway/notifier.py)
└── webhook/             # webhook_server.py moves here as a Channel
```

**Channel ABC:** the authoritative contract is in [docs/architecture/GATEWAY.md](../../architecture/GATEWAY.md#L151) — GATEWAY.md is the source of truth for Phase 1 contracts (it is code-accurate and internally consistent). Adopt it in full, including the owner-addressing and streaming-aware methods:

```python
class Channel(ABC):
    name: str                          # "telegram", "email", "whatsapp", "webhook"
    supports_streaming: bool = False   # True iff send_stream is meaningful (voice + TTS)

    @abstractmethod
    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None: ...
    @abstractmethod
    async def send_media(self, chat_id: str, kind: str, payload: bytes, caption: str | None = None) -> None: ...
    @abstractmethod
    async def send_to_owner(self, text: str) -> None: ...
    @abstractmethod
    async def send_to_owner_media(self, kind: str, payload: bytes, caption: str | None = None) -> None: ...
    @abstractmethod
    def authorize(self, raw_user_id: str) -> bool: ...
    @abstractmethod
    async def start(self, on_message: Callable[[InboundMessage], Awaitable[str | None]]) -> None: ...

    async def send_stream(self, chat_id: str, chunks: AsyncIterator[str]) -> None:
        """Default: collect chunks, then send once. Streaming channels override."""
        full = "".join([c async for c in chunks])
        await self.send(chat_id, full)
```

`send_to_owner` / `send_to_owner_media` are the decoupling seam for proactive sends: heartbeat and the notifier never learn `ALLOWED_USER_ID` or any `chat_id`. The channel reads its own owner-config env at construction. `default_user_channel()` in `factory.py` returns the channel proactive sends target. **`on_message` returns the reply text (`str | None`); the channel router posts it back** (Plane 1 → Plane 2 in GATEWAY.md) — the handler does not send directly.

**Channel-specific notes (research before implementing each):**
- **Email** — async, high latency. Use IMAP IDLE for inbound or polling. Outbound via SMTP. `chat_id` = sender address. Threading via `In-Reply-To` headers. Don't pretend it's real-time.
- **WhatsApp** — Cloud API requires public webhook + Meta Business verification. Templates required for outbound after the 24h window. Plan for that constraint.
- **Custom app** — uses the read API (deferred) for state queries. If it sends messages, it's just another `Channel` (HTTP POST inbound, SSE/WebSocket outbound).

**Confirmation refactor:** the authoritative contract is the `Confirmation` / `ConfirmationUI` split in [GATEWAY.md](../../architecture/GATEWAY.md#L189). **Preserve the existing sync model — do not switch to async-bool.** The current tools call `request_confirmation_sync(...)` from a sync worker thread and get a status string back *immediately* (fire-and-forget; the outcome is posted later). Switching to `await ... -> bool` would force every tool async and block the worker thread on user input — a strictly worse model and not a transparent shim.

- `Confirmation` ABC: `request_confirmation_sync(description, action_fn, result_ok_text=..., result_cancel_text=...) -> str` — called from a sync tool worker thread, returns a status string ("Awaiting your approval…") immediately.
- `InMemoryConfirmationStore` owns bookkeeping, TTL eviction, and outcome dispatch. It is channel-agnostic.
- `ConfirmationUI` ABC (`send_prompt`, `edit_outcome`) is the only channel-specific half. `TelegramConfirmationUI` keeps the InlineKeyboard logic.
- Tools take a `confirmation: Confirmation` injected via tool context, not `get_gateway().confirmation_manager`. The sync entry point is preserved so tool callsites change only their import/injection, not their call shape (keeps the deprecated-shim rollback viable).
- Background task drives `_cleanup_expired` every 60s instead of relying on the callback path (fixes the un-clicked-confirmation leak).
- Remove `_notify_agent_of_outcome` reaching into `ask_jarvis` directly — the store posts the outcome via `channel.send_to_owner(...)`; the agent picks it up next turn.

**Inbound flow:**
- Each channel produces `InboundMessage` and calls a single `on_message` handler, which returns reply text the channel posts back.
- **`InboundMessage.thread_id` stays `f"telegram_{user_id}"` in Phase 1.** The `:` separator change (`telegram:<user_id>`) is a **Phase 2** concern, deliberately deferred per GATEWAY.md because it is coupled to the LangGraph checkpointer-key and `chat_history.jsonl` record migration. Changing it in Phase 1 would silently orphan every existing checkpoint and break history filtering. Phase 1 is a pure structural move with zero state-format change.
- `process_inbound_message` in [main.py:50-83](../../../main.py#L50-L83) is the single entry point all channels call.

**Files to change:**
- Create: `gateway/base.py`, `gateway/factory.py`, `gateway/confirmation/{base,manager}.py`, `gateway/telegram/{channel,router,confirmation}.py`.
- Move: `gateway/telegram_gateway.py` → split across `telegram/`. `gateway/notifier.py` → `gateway/telegram/notifier.py`. `gateway/webhook_server.py` → `gateway/webhook/`.
- Edit: every tool that calls `get_gateway()` for confirmation ([tools/memory_tools.py:36,121](../../../tools/memory_tools.py#L36), [tools/media_tools.py:405,776](../../../tools/media_tools.py#L405)) to receive `confirmation` via tool context instead.
- **Edit: [heartbeat.py](../../../heartbeat.py) proactive-send callsites — [heartbeat.py:111](../../../heartbeat.py#L111) and [heartbeat.py:140](../../../heartbeat.py#L140) both call `get_gateway().send_message(...)`.** Replace with `default_user_channel().send_to_owner(...)`. These are *not* confirmation callsites; missing them fails the done-criterion below.
- **Edit: [gateway/notifier.py:7](../../../gateway/notifier.py#L7)** imports the concrete `TelegramGateway` class and pushes poster images. Rewire to `default_user_channel().send_to_owner_media(...)` (or accept an injected channel) when it moves under `gateway/telegram/`.
- Edit: [main.py:18](../../../main.py#L18) (`from gateway.telegram_gateway import ...`) and the Telegram wiring at [main.py:101-138](../../../main.py#L101-L138) to use `factory.create_channels()` and start each.
- Replace: hardcoded internal Jellyfin address in [gateway/notifier.py:16](../../../gateway/notifier.py#L16) → env var (e.g. `JELLYFIN_INTERNAL_URL`).
- **Telegram media-cache move** (`/app/jarvis_memory/media/` → `gateway/telegram/media_cache/`) and relocating `save_media_file`/`trim_media` out of `tools/history_tools.py`: **Phase 3, not Phase 1** (per GATEWAY.md "Layering note"). Phase 1 preserves the existing `save_media_file_fn` injection point unchanged. The line-770 "Phase 1 finish-up" note elsewhere in this doc is superseded by this.

**Verification (per CLAUDE.md instructions):**
1. `pct exec 106 -- systemctl restart jarvis.service`, then `pct exec 106 -- journalctl -u jarvis -n 50` — no startup errors.
2. Send Telegram message — same UX. Test text, photo, voice memo.
3. Trigger destructive confirmation (e.g. `delete_memory`) — InlineKeyboard appears, Confirm → action runs, Cancel → action skipped.
4. Wait for a heartbeat tick (or trigger manually) — verify scheduler still fires, daily log written.
5. `grep -r "from gateway.telegram_gateway" --include="*.py"` — only matches inside `gateway/telegram/`.

**Done criterion:** zero `from gateway.telegram_gateway import` outside `gateway/telegram/`. `Confirmation` ABC exists. New channels can be added by dropping a file under `gateway/<name>/`.

**Actual verification status (honest):**
- ✅ #1 startup clean (restarted directly via `systemctl` — this host *is* the container; no `pct`).
- ✅ #3 destructive confirmation: Confirm runs action, Cancel skips, SOUL.md-style gate intact, chatty acknowledgement restored — verified live by the owner.
- ✅ #5 grep gate: **stronger than required** — zero `telegram_gateway`/`get_gateway` refs *anywhere*, AND zero `agent`/`tools`/`main` imports inside `gateway/` (notifier also decoupled).
- ✅ Notifier path: full `scripts/test_webhooks.py` suite — system alerts, unknown events, photo + deterministic notifications all send, no crash.
- ⚠️ #2 partial: text + the confirmation reply path verified live; **photo/voice/album not each explicitly re-exercised** (router logic ported unchanged, static-verified only).
- ⚠️ #4 heartbeat tick / daily-log write **not explicitly triggered post-cutover** (the proactive send path *is* exercised by the confirmation-outcome reply, which uses the same `default_user_channel().send_to_owner`).

Done criterion itself: **met.** Residual ⚠️ items are recommended spot-checks before merge, not blockers.

---

### Phase 2 — Tool registry, scoped loading, and SAME-TURN dynamic skill activation

This is the phase that solves your "cooking shouldn't see gym tools" problem, with same-turn activation (no "ask again next message" awkwardness). Read it carefully — Phase 2 also includes the LangGraph migration from `create_agent` to `StateGraph` (see Runtime Alternatives section).

**Goal:** The LLM's tool surface in any given turn is small and topical. Cooking conversations don't carry Sonarr/Radarr/Arbox tool schemas in the prompt. When the LLM realizes it needs a skill, it activates and uses it in the same turn.

#### Reference reading (read these before designing the refactor)

Existing code:
- [agent.py](../../../agent.py) — full file. Specifically: `JarvisState` (lines 74-75), `_add_and_trim` reducer (60-72), `_strip_media_blobs` (24-57), `PruningSqliteSaver` (87-102), the `create_agent` call (237-243), `_BASE_SYSTEM_PROMPT` (140-225), `ask_jarvis()` (245-369). The whole file is being restructured; understand all of it.
- [tools/__init__.py](../../../tools/__init__.py) — flat list `jarvis_tools` (lines 58-108). This goes away in favor of `registry.get_tools(...)`.
- All tool files under [tools/](../../../tools/) — each tool's docstring will become its registry entry. Read enough to understand the shape; don't memorize every signature.
- [main.py:50-83](../../../main.py#L50-L83) — `process_inbound_message`, the entry point that calls `ask_jarvis`. Will call the new `build_graph(...)` instead.
- [heartbeat.py:54-116](../../../heartbeat.py#L54-L116) — heartbeat runner. Same call site pattern as user thread.

OpenClaw references:
- Skills (the pattern we're mirroring) — https://docs.openclaw.ai/tools/skills.md (per-agent allowlists, "compact XML list of available skills" injected into system prompt, allowlist replaces defaults)
- Agent loop — https://docs.openclaw.ai/concepts/agent-loop.md (their orchestration; we won't copy it but the model is informative — `before_tool_call` / `after_tool_call` hooks especially)
- Agent runtime — https://docs.openclaw.ai/concepts/agent.md (bootstrap files, session state model)

LangGraph references:
- `StateGraph` API — https://langchain-ai.github.io/langgraph/reference/graphs/ (this is what we use instead of `create_agent`)
- LangGraph's prebuilt `ToolNode` — https://langchain-ai.github.io/langgraph/reference/agents/ (we may reuse it; or write our own to mutate `active_skills` from inside)
- `bind_tools` on chat models — https://python.langchain.com/docs/how_to/tool_calling/ (the per-call tool re-binding that makes same-turn activation work)
- Persistence / checkpointer — https://langchain-ai.github.io/langgraph/concepts/persistence/ (we keep `PruningSqliteSaver`; understand how `state.update()` flows through it)
- Streaming via `astream_events` — https://langchain-ai.github.io/langgraph/how-tos/streaming/ (needed for the future voice channel; Phase 2 should add the streaming entry point even if no channel uses it yet)

Open questions to confirm with user before implementing:
- Initial namespace boundaries — proposed: `media`, `fitness`, `home` (placeholder). Anything else (e.g. `notifications`)?
- For heartbeat threads, should *all* registered skills be auto-active, or is heartbeat scope blank-by-default and the heartbeat task's `HEARTBEAT.md` description tells the agent which to activate? (Current plan: blank-by-default, heartbeat decides per-task. Confirm.)
- Idle timeout for `active_skills` (e.g. clear after 12h of thread inactivity) — ship in this phase or leave as a follow-up?

**Two-tier tool model (mirrors OpenClaw's "built-ins always available + skills allowlisted" pattern):**

#### Tier 1 — Core tools (always bound)

A small set, ~8 tools, used in nearly every conversation:
- `read_memory`, `write_memory`, `list_memory`, `delete_memory`
- `web_search`
- `get_chat_history`
- `manage_reminder`
- `activate_skill`  ← the meta-tool that exposes Tier 2

Full schemas always in the prompt. Cheap.

#### Tier 2 — Skills (advertised compactly, activated on demand)

Grouped by namespace. Each namespace is a "skill":
- `media` — Sonarr/Radarr/Jellyseerr/notifications
- `fitness` — Arbox attendance, running/WOD logs
- `home` — placeholder for future home-automation tools
- (extensible)

In the system prompt, only a compact list appears:
```
## Available skills (call activate_skill to load tools for this conversation):
- media: TV/movie search, library management, download tracking
- fitness: gym attendance, workout logs, running sessions
- home: (no tools active)

## Currently active in this conversation: none
```

**The full schemas of Tier 2 tools are NOT in the prompt** until activated. That's the entire point: the LLM doesn't pay tokens for tools it isn't using.

#### How activation works (same-turn, via StateGraph)

The agent loop is rewritten as a `StateGraph` (see Runtime Alternatives, Option A). The LLM node re-binds tools every invocation based on the current `state["active_skills"]`:

```
Turn N starts. state["active_skills"] = set()
  llm_node:  bound_llm = llm.bind_tools(core_tools)
             response = bound_llm.invoke(messages)
             → response.tool_calls = [activate_skill(namespaces=["media"])]
  tool_node: activate_skill writes state["active_skills"] = {"media"}
             returns ToolMessage("Activated: media")
  llm_node:  bound_llm = llm.bind_tools(core_tools + media_tools)  ← rebound!
             response = bound_llm.invoke(messages)
             → response.tool_calls = [search_sonarr(query="Severance")]
  tool_node: runs search_sonarr
  llm_node:  produces final answer
Turn N ends.
```

The LLM activates, then immediately uses the new tools — all within the same user message. UX is: "Queue Severance season 2" → Jarvis silently activates media skill → searches → confirms → done. The user never sees a delay or a re-ask.

`active_skills` is persisted in `JarvisState` via the checkpointer, so activations carry across turns within a thread (the LLM doesn't need to re-activate every message). It can call `deactivate_skill` if it wants to shrink the surface, or scopes can auto-decay activations after N idle turns (deferred).

#### Why this is safe to commit to

- `StateGraph` is the standard LangGraph API; we're not using anything experimental. `create_agent` is itself implemented on top of it.
- Mid-loop tool re-binding is the same pattern used by Anthropic's tool-use cookbook examples. Not exotic.
- The custom loop is ~100 lines, replacing `create_agent`'s ~5 lines. Trade is real but small.
- All existing infrastructure (checkpointer, message reducer, JarvisState) carries over unchanged.

#### Scope — what it actually is (refined per your feedback)

Earlier draft of this plan made scope a *restrictive* concept ("heartbeat can't call delete_memory"). You correctly pointed out that heartbeat may legitimately need destructive tools — e.g., a heartbeat task "review your memory files and remove any stale ones" needs `delete_memory` plus the ability to start a confirmation conversation with you via the channel. Locking heartbeat out of destructive tools breaks that use case.

**Revised scope model: scope is informational, not restrictive.**

What scope DOES affect:
- **Which prompt is built.** User scope: SOUL + AGENTS + USER + MEMORY index + today's daily log. Heartbeat scope: same plus HEARTBEAT.md and last 2 daily logs, plus a behavioral framing ("you're running on a scheduled tick — be brief, only act if needed, respond `[NO_ACTION]` if nothing's due").
- **Default active_skills set on a new thread.** User threads start with no skills active. Heartbeat threads start with skills relevant to its tasks pre-activated (e.g. if HEARTBEAT.md has a `mobility_check` task, `fitness` is auto-active).
- **Behavioral expectations encoded in AGENTS.md sections** (e.g. heartbeat-only sections that explain the `[NO_ACTION]` convention).

What scope does NOT affect:
- Tool reachability. **Both scopes can call any tool the agent has activated.** Including destructive tools, including user/admin-flavored tools.
- Confirmation requirements. `@destructive` always routes through ConfirmationManager regardless of who triggered. If heartbeat calls `delete_memory("running_playbook.md")`, the ConfirmationManager sends a Telegram InlineKeyboard to Roi, waits for click, then proceeds (or skips on cancel/timeout).

**Consequence for your "review memory files" example:** A heartbeat task can call `list_memory()`, reason about staleness, call `delete_memory("foo.md")` → confirmation prompt fires on Telegram → Roi taps confirm → file deleted. The heartbeat thread holds the conversation context throughout (it's a heartbeat-initiated conversation per Flow 2 above).

**Tool registration becomes simpler:**
```python
@tool_register(
    namespace="media.sonarr",
    destructive=True,           # auto-wrapped with @destructive (uses Confirmation ABC)
)
def delete_sonarr_series_with_files(title: str) -> str: ...
```
No `scopes=` field. Tools advertise themselves to all scopes; activation is the gate.

If we ever need true restriction (e.g. "don't ever let heartbeat trigger media downloads"), add it as a deny-list per scope at the registry level, not as a default field on every tool.

#### New structure
```
tools/
├── registry.py          # @tool_register decorator; namespace+scope+destructive metadata
├── decorators.py        # @destructive wraps with Confirmation
├── core/                # Tier 1 — always bound
│   ├── memory.py
│   ├── search.py
│   ├── history.py
│   ├── scheduling.py
│   └── activate_skill.py
├── media/               # Tier 2 — namespace="media.*"
│   ├── sonarr.py
│   ├── radarr.py
│   └── jellyseerr.py
├── fitness/             # Tier 2 — namespace="fitness.*"
│   └── arbox.py
└── ...
```

#### System prompt assembly

Replace the hardcoded tool descriptions in [agent.py:140-225](../../../agent.py#L140-L225) with two generated sections:
1. Full schemas for core tools (LangGraph injects these from `tools=[...]` automatically).
2. A compact `## Available skills` block built by `registry.compact_skill_list(scope, active_skills)`.

Tool docstrings become the single source of truth. No manual sync.

#### Files to change

- Create: `tools/registry.py`, `tools/decorators.py`.
- Reorganize: split `tools/*.py` into `core/`, `media/`, `fitness/` (preserve git blame via `git mv`).
- Edit: [agent.py](../../../agent.py) — `build_agent_for_turn(scope, thread_id)` reads `active_skills` from checkpointer state; constructs the agent with `core + active_namespace_tools`.
- Edit: `JarvisState` — add `active_skills: set[str]`.
- Edit: [tools/__init__.py](../../../tools/__init__.py) — replace flat list with `import_all()` triggering registration side-effects.
- Edit: [main.py](../../../main.py), [heartbeat.py](../../../heartbeat.py) — call `build_agent_for_turn(scope, thread_id)` per turn.

#### Verification

1. `pct exec 106 -- systemctl restart jarvis.service` — startup logs show "registered N core tools, M skill tools across K namespaces."
2. Telegram chat: "What's on my mind today?" — Jarvis answers without activating any skill (only core tools used). Inspect logged prompt: no media/fitness tool schemas present.
3. "Queue Severance season 2" — Jarvis calls `activate_skill(["media"])`, then in next message Roi re-asks or Jarvis prompts for confirmation; verify queueing works.
4. Trigger heartbeat — log scope=heartbeat tool list; verify `media.*` absent even after attempted activation.
5. Inspect logged prompt size before/after: token count should drop substantially when no skill is active.

**Done criterion:** `_BASE_SYSTEM_PROMPT` no longer enumerates tools (compact list is generated). `jarvis_tools` flat list deleted. Discussing cooking does not put media/fitness tool schemas into the prompt.

---

### Phase 3 — Memory architecture cleanup + file-driven identity

This phase has two coupled goals because they share the same surface (`/app/jarvis_memory/`). Read both halves before starting.

**Half A goal:** Bring order to the current memory mess — there are seven distinct memory layers on disk, some undocumented, some redundant. Define each layer's role explicitly and clean up what doesn't belong.

**Half B goal:** Move the system prompt out of code and into files the agent can read at runtime. Replace `_BASE_SYSTEM_PROMPT` with a builder that assembles SOUL.md + AGENTS.md + USER.md + dynamic context per scope.

#### Reference reading (read these before designing the refactor)

Existing state to inspect on the running container (use `pct exec 106 -- ls -la /app/jarvis_memory/` etc.):
- `/app/jarvis_memory/SOUL.md` — current persona content (small, ~1KB).
- `/app/jarvis_memory/MEMORY.md` — current index (agent-maintained, ~1.5KB).
- `/app/jarvis_memory/HEARTBEAT.md` — heartbeat task list.
- `/app/jarvis_memory/user_preferences.txt`, `people_and_connections.txt`, `running_playbook.md`, `fitness.md`, `active_projects.md` — free-form memory; some content folds into USER.md.
- `/app/jarvis_memory/daily/daily_*.md` — recent daily logs; sample one to see the format.
- `/app/jarvis_memory/heartbeat/*.md` — per-task heartbeat state.
- `/app/jarvis_memory/scheduled_events.json` — owned by scheduling; relocates in Half A.
- `/app/jarvis_memory/fitness.sqlite` — relocates in Half A (see Tool Data Location section).
- `/app/jarvis_memory/media/*.{ogg,jpg,mp4}` — Telegram media blobs; relocate to `gateway/telegram/media_cache/`.

Existing code:
- [agent.py:127-228](../../../agent.py#L127-L228) — `_load_soul_md()` and `_BASE_SYSTEM_PROMPT`. The 87 lines of hardcoded text get split into AGENTS.md (rules) + the prompt builder (dynamic context).
- [tools/memory_tools.py](../../../tools/memory_tools.py) — `_get_safe_path` sandbox (lines 1-30 area), `write_memory` confirmation flow (35-46), `delete_memory` flow (121-134). Add deny-list for `threads.sqlite*`. Add `flock`-based write lock.
- [tools/heartbeat_tools.py](../../../tools/heartbeat_tools.py) and [heartbeat.py](../../../heartbeat.py) — both reference `scheduled_events.json` path; update to new location in `/app/jarvis_data/scheduling/`.
- [tools/fitness_tools.py:9](../../../tools/fitness_tools.py#L9) — DB path; updates to new location.
- [tools/history_tools.py:10](../../../tools/history_tools.py#L10) — `MEDIA_STORAGE` constant; updates for the media relocation.

OpenClaw references:
- Memory concepts — https://docs.openclaw.ai/concepts/memory (the layered model we're loosely mirroring)
- Default AGENTS.md (full) — https://docs.openclaw.ai/reference/AGENTS.default.md (the structure our AGENTS.md template borrows from)
- AGENTS.md template — https://docs.openclaw.ai/reference/templates/AGENTS.md
- SOUL.md template — https://docs.openclaw.ai/reference/templates/SOUL.md
- IDENTITY.md template — https://docs.openclaw.ai/reference/templates/IDENTITY.md (we don't use this, but useful for understanding why OpenClaw splits it from SOUL)
- TOOLS.md template — https://docs.openclaw.ai/reference/templates/TOOLS.md (we don't ship a separate TOOLS.md; tool docstrings + registry compact list cover the same ground)
- HEARTBEAT.md template — https://docs.openclaw.ai/reference/templates/HEARTBEAT.md
- Agent runtime (bootstrap file injection) — https://docs.openclaw.ai/concepts/agent.md ("blank files are skipped, large files are trimmed and truncated with a marker so prompts stay lean")

Open questions to confirm with user before implementing:
- USER.md seed content — what facts about Roi belong there? Suggested seed: timezone (Israel), preferred address, current fitness/running program, work context, communication style. **Confirm with user before populating.**
- Should `write_memory` block writes to `AGENTS.md` outright, or allow with a confirmation prompt that says "this is a dev-controlled file; please confirm"? Default: outright block.
- Daily logs in user-scope prompt — load only today, or today+yesterday? Heartbeat scope — last 2 or last 3? Defaults proposed in Half B; confirm.
- For the `flock` write lock: per-file granularity (one lock per file) or whole-directory lock? Per-file is finer; one lock for the whole memory dir is simpler. Default: per-file.

#### Half A — Memory architecture audit

What's actually in `/app/jarvis_memory/` right now (verified by listing the directory, not by reading docs):

| Layer | Storage | Examples | Issue |
|---|---|---|---|
| **Identity (file)** | markdown | SOUL.md (1KB), MEMORY.md (1.5KB), HEARTBEAT.md (2KB) | OK; SOUL write needs confirmation, all three protected from delete. |
| **Long-term knowledge (file)** | free-form .md/.txt | active_projects.md, fitness.md, running_playbook.md, user_preferences.txt, people_and_connections.txt | No naming convention. MEMORY.md should index but enforcement is "agent prompt." |
| **Episodic — daily** | markdown | daily/daily_2026-05-08.md, etc. | OK, written by heartbeat. |
| **Episodic — heartbeat tasks** | markdown | heartbeat/attendance_sync.md, heartbeat/crossfit_check.md, heartbeat/haircut_reminder.md, heartbeat/mobility_check.md | OK; one file per heartbeat task. |
| **Domain DB (UNDOCUMENTED in CLAUDE.md)** | sqlite | fitness.sqlite (28KB) — used by [tools/fitness_tools.py:9](../../../tools/fitness_tools.py#L9) | **Mess:** separate DB for fitness only, parallel to `threads.sqlite`. CLAUDE.md doesn't mention it. Why is fitness alone special? |
| **Conversation state (LangGraph)** | sqlite | threads.sqlite (9MB + 5.8MB WAL) — `PruningSqliteSaver` keeps 1 row per thread | OK conceptually but the WAL has grown big — likely needs a VACUUM. |
| **Activity log (append-only)** | jsonl | chat_history.jsonl (456KB), notifications.jsonl (8KB) — 90-day retention | OK; serves a different purpose than checkpointer (audit trail vs LangGraph state). Both are needed. |
| **Telegram media blobs** | binary | media/audio_*.ogg, media/image_*.jpg, media/video_*.mp4 (40+ files) — used by [tools/history_tools.py:10](../../../tools/history_tools.py#L10) | **Mess:** these are Telegram-specific (filenames embed Telegram file IDs). They live under "memory" but are a gateway artifact. With multi-channel, this becomes incoherent. |
| **Operational state** | JSON | scheduled_events.json (300 bytes — APScheduler reminders) | OK; protected. |

OpenClaw equivalents (from research):

| OpenClaw | Jarvis equivalent today | Gap |
|---|---|---|
| SOUL.md | SOUL.md | match |
| MEMORY.md (long-term, loaded at session start) | MEMORY.md as index, free-form .md files | OK but no enforced index discipline |
| memory/YYYY-MM-DD.md (daily, today+yesterday auto-loaded) | daily/daily_YYYY-MM-DD.md (heartbeat-only) | Daily logs aren't loaded into user-scope context. They are visible only to heartbeat. **Fix.** |
| AGENTS.md | _BASE_SYSTEM_PROMPT in code | extract in Half B |
| USER.md | scattered across user_preferences.txt + memory entries | consolidate |
| memory_search (vector + keyword) | grep via list_memory + read_memory | reject — not needed at this scale |
| (no equivalent) | fitness.sqlite | unique to Jarvis. Decide: delete and consolidate into markdown, OR document as an explicit "domain DB" layer with a stated rule for when a domain warrants its own DB |
| (no equivalent) | media/ Telegram blobs | belongs under `gateway/telegram/` cache, not `jarvis_memory/`. **Move in Phase 3** (paired with relocating `save_media_file`/`trim_media`); Phase 1 leaves the injection point unchanged. |

**Decisions for Half A:**

1. **fitness.sqlite — relocate to `/app/jarvis_data/fitness/`** (per the "Tool Data Location" section below). It's a deterministic structured logging surface for workouts; the DB has no value without the fitness tools. Path resolution: `os.environ.get("FITNESS_DB_PATH", "/app/jarvis_data/fitness/fitness.sqlite")`. Update [tools/fitness_tools.py:9](../../../tools/fitness_tools.py#L9). Document as "tool-owned persistence" in CLAUDE.md.

2. **Telegram media blobs — relocate to gateway.** Move `/app/jarvis_memory/media/` → `/app/jarvis_code/gateway/telegram/media_cache/`. Update [tools/history_tools.py:10](../../../tools/history_tools.py#L10). Rationale: filenames embed Telegram file IDs (`audio_AwACAgQAAxk...`), making them gateway artifacts, not agent memory. This also future-proofs multi-channel: WhatsApp/email media goes under their own gateway dirs. **Note on OpenClaw:** their docs are surprisingly vague on cross-channel media storage (only confirms "media and reactions vary by channel"). They don't define a unified storage location — implying channel-specific is the de-facto pattern. Our move aligns.

3. **Daily logs — make user-scope-visible.** Heartbeat writes them. Today's daily log auto-loaded into user-scope prompt (mirrors OpenClaw's "today + yesterday auto-load"). Heartbeat scope still gets last 2 days. Why this matters for your concurrency question (Concurrency Model section above): when heartbeat sends Roi a Telegram message, it appends a one-line note to today's daily log. Next user turn, the user-scope agent has the context.

4. **Memory file write locks.** Add `flock`-based lock around `write_memory`'s actual write step in [tools/memory_tools.py](../../../tools/memory_tools.py). Prevents heartbeat-vs-user races on the same file. OpenClaw's pattern. ~10 lines.

5. **`scheduled_events.json` — relocate to `/app/jarvis_data/scheduling/`.** Same principle as fitness.sqlite: tool-owned opaque state (not agent-edited markdown), shouldn't sit in `jarvis_memory/`. Update [tools/heartbeat_tools.py](../../../tools/heartbeat_tools.py) and [heartbeat.py](../../../heartbeat.py). Once relocated, the file is physically outside the memory tools' surface — no deny-list needed for it (the deny-list still applies to `threads.sqlite*` which can't be relocated since LangGraph owns the path).

   **The principle, stated cleanly:** agent-readable markdown state stays in `/app/jarvis_memory/` (the agent reasons about it via memory tools). Tool-opaque state (binary DBs, code-managed JSON the agent never reads directly) lives in `/app/jarvis_data/<tool>/`. The `jarvis_memory/` deny-list narrows to just files the runtime owns and can't relocate (`threads.sqlite`, `threads.sqlite-wal`, `threads.sqlite-shm`).

5. **MEMORY.md index discipline.** Add a heartbeat task `memory_index_audit.md` that periodically validates MEMORY.md lists every `.md`/`.txt` under `jarvis_memory/`, flags drift. Lightweight; runs in the existing heartbeat loop.

6. **threads.sqlite WAL.** A 5.8MB WAL on a 9MB DB suggests SQLite isn't aggressively checkpointing (separate concept from LangGraph checkpointer). Add `PRAGMA wal_checkpoint(TRUNCATE)` periodically via a heartbeat task. Cosmetic, not urgent.

7. **Consolidate free-form files.** `user_preferences.txt` and parts of `people_and_connections.txt` consolidate into USER.md (Half B). `active_projects.md` stays as-is — it's mutable project state, not user identity. `running_playbook.md` and `fitness.md` stay — they're domain knowledge documents, not user identity.

**Updated layer map after Half A:**

| Layer | Location | Visible to memory tools? | Examples |
|---|---|---|---|
| Identity | `/app/jarvis_memory/` | yes (read all; write SOUL needs confirmation; AGENTS dev-only) | SOUL.md, AGENTS.md, USER.md, MEMORY.md |
| Long-term knowledge | `/app/jarvis_memory/` | yes | active_projects.md, running_playbook.md, fitness.md |
| Episodic — daily | `/app/jarvis_memory/daily/` | yes | daily_YYYY-MM-DD.md |
| Episodic — heartbeat tasks | `/app/jarvis_memory/heartbeat/` | yes | <task>.md |
| Operational state (agent-readable) | `/app/jarvis_memory/` | yes | HEARTBEAT.md |
| Conversation state | `/app/jarvis_memory/` | **NO** (deny-listed; managed by checkpointer) | threads.sqlite (+wal, +shm) |
| Activity log | `/app/jarvis_memory/` | **NO direct file access; queried via tools** | chat_history.jsonl, notifications.jsonl |
| Tool-owned persistence — scheduling | `/app/jarvis_data/scheduling/` | **NO** (outside memory surface) | scheduled_events.json |
| Tool-owned persistence — fitness | `/app/jarvis_data/fitness/` (env-overridable) | **NO** (outside memory surface) | fitness.sqlite |
| Gateway artifacts (relocated) | `/app/jarvis_code/gateway/telegram/media_cache/` | **NO** (outside memory surface) | audio/image/video Telegram blobs |

#### Half B — File-driven identity

You asked: "what files Jarvis can access, what is loaded automatically and when, and what is hidden and only changes by the user." Explicit table:

| File | Loaded automatically into prompt | Agent can read via tools | Agent can write via tools | Edits require user confirmation |
|---|---|---|---|---|
| `SOUL.md` | every turn (both scopes) | yes | yes | **yes** (already enforced) |
| `AGENTS.md` | every turn (both scopes) | yes | **no** (dev-only file; tool blocks writes) | n/a (deploy-only) |
| `USER.md` | every turn (both scopes) | yes | yes | no (agent updates freely) |
| `MEMORY.md` | every turn — but only the index, not contents of files it lists | yes | yes | no |
| `daily/daily_<today>.md` | user scope: today; heartbeat scope: last 2 days | yes | yes (mainly heartbeat) | no |
| `HEARTBEAT.md` | heartbeat scope only | yes (both scopes) | yes | no |
| `scheduled_events.json` (now at `/app/jarvis_data/scheduling/`) | not loaded into prompt | no (managed by scheduler tools — outside memory surface) | no | n/a |
| `active_projects.md`, `running_playbook.md`, `fitness.md`, `people_and_connections.txt`, etc. | NOT auto-loaded; agent reads via `read_memory` when needed | yes | yes | no |
| `chat_history.jsonl`, `notifications.jsonl` | not loaded; queried via `get_chat_history` / `get_notification_history` | yes (read-only) | append-only via gateway | n/a |
| `threads.sqlite` (LangGraph state) | implicitly used by checkpointer | no (filtered out of `list_memory`) | no | n/a |

**Two complementary access patterns:**

1. **Auto-loaded files (small, always-present context):** SOUL, AGENTS, USER, MEMORY index, today's daily log, [heartbeat-only] HEARTBEAT.md. These set the agent's mental state every turn. Total budget: a few thousand tokens.

2. **On-demand files (queried when relevant):** all the free-form memory files, jsonl logs, older daily logs, individual heartbeat task files. Agent uses `read_memory`, `list_memory`, `get_chat_history` when it needs specifics. MEMORY.md serves as the discoverability layer — the index that tells the agent what exists.

**Hidden / dev-controlled / restricted writes:**
- `AGENTS.md` — `write_memory` blocks writes to it (returns "AGENTS.md is dev-controlled; edit via repo + restart"). Agent can read but not modify.
- `SOUL.md` — write goes through `ConfirmationManager` (already implemented at [tools/memory_tools.py:35-46](../../../tools/memory_tools.py#L35-L46)).
- `MEMORY.md`, `HEARTBEAT.md`, `scheduled_events.json` — deletes blocked (already enforced).

**SOUL vs AGENTS split:** SOUL.md is user-curated identity (persona, tone, voice — short). AGENTS.md holds developer-controlled operating rules (tool usage protocols, memory architecture instructions, heartbeat protocol — long). Restart required for AGENTS.md changes is acceptable.

#### AGENTS.md template (borrowed from OpenClaw's default structure)

OpenClaw's default AGENTS.md has these sections; we adopt the structure for Jarvis:

```markdown
# AGENTS.md — Jarvis Operating Rules

## Session Start
On every turn, you have already been given: SOUL.md, USER.md, MEMORY.md (index),
today's daily log. Read them. Don't ask the user for facts that are in these files.

## Safety Defaults
- Never read /app/secrets/.env or anything outside /app/jarvis_memory/.
- Never expose API keys, tokens, or paths under /app/secrets/.
- Destructive actions (delete_memory, delete_*_with_files) MUST go through ConfirmationManager.

## Memory System
- Long-term durable facts live in MEMORY.md and the files it indexes.
- Episodic notes live in daily/daily_YYYY-MM-DD.md (heartbeat writes during scheduled checks).
- When you learn something durable, write it. When something becomes obsolete, delete it.
- Keep MEMORY.md in sync; the heartbeat audit task will flag drift.

## Tools and Skills
- Core tools (memory, search, history, scheduling) are always available.
- Domain skills (media, fitness, ...) must be activated via activate_skill before use.
- Skills are listed at the bottom of this prompt under "Available skills."
- Activate eagerly when you suspect you'll need a namespace; deactivate to keep the surface clean.

## Heartbeat (heartbeat scope only)
- HEARTBEAT.md lists active scheduled tasks. For each, check its state file in heartbeat/.
- Decide if due, act, update state file, append a one-line note to today's daily log.
- If nothing needs attention: respond with [NO_ACTION].

## Conversation Continuity
You are a fresh process every restart, but conversation state persists via the
checkpointer (threads.sqlite). active_skills carries across turns. If a previous
turn activated a skill you no longer need, deactivate it.

## Time
Always reason about and display times in Israel local time. The current time is
provided in the prompt envelope.
```

This replaces the ~100-line `_BASE_SYSTEM_PROMPT` in [agent.py:127-228](../../../agent.py#L127-L228).

**Prompt assembly (`prompts/builder.py`):**
```python
def build_system_prompt(scope: str, active_skills: set[str], context: RuntimeContext) -> str:
    parts = [
        load_or_blank("SOUL.md"),
        load_or_blank("AGENTS.md"),
        load_or_blank("USER.md"),
        f"[Now: {context.now_israel.isoformat()}]",
        f"[Active scope: {scope}]",
        registry.full_schemas(scope, active_skills),  # core + active skills' tools
        registry.compact_skill_list(scope, active_skills),  # advertise inactive ones
        load_or_blank("MEMORY.md"),  # index only, not file contents
    ]
    if scope == "heartbeat":
        parts.append(load_or_blank("HEARTBEAT.md"))
        parts.append(load_recent_daily_logs(days=2))
    return "\n\n".join(p for p in parts if p)
```

- Time context moves from [main.py:60](../../../main.py#L60) into the system prompt envelope.
- Each scope ("user" / "heartbeat") gets a different assembly.
- Files trimmed/skipped per OpenClaw's pattern: blank files skipped; if a file is missing, inject a single "missing file" marker so the agent knows.

**Agent factory replacing global singleton:**
```python
# agents/factory.py
def build_agent_for_turn(scope: str, thread_id: str) -> CompiledGraph:
    state = checkpointer.get_state(thread_id)
    active_skills = state.get("active_skills", set())
    return create_agent(
        model=llm,
        tools=registry.get_tools(scope=scope, active_skills=active_skills),
        system_prompt=build_system_prompt(scope, active_skills, RuntimeContext.now()),
        checkpointer=memory,
        state_schema=JarvisState,
    )
```

[main.py](../../../main.py) calls `build_agent_for_turn("user", thread_id)` per request. [heartbeat.py](../../../heartbeat.py) calls `build_agent_for_turn("heartbeat", "heartbeat")`.

**Files to change:**

*Half A (memory cleanup):*
- Decide on fitness.sqlite (read [tools/fitness_tools.py](../../../tools/fitness_tools.py); if structured-tabular, document; if note-shaped, migrate to markdown and delete DB).
- Move `/app/jarvis_memory/media/` → `/app/jarvis_code/gateway/telegram/media_cache/`. Edit [tools/history_tools.py:10](../../../tools/history_tools.py#L10) to point at new path.
- Add heartbeat tasks: `memory_index_audit.md` (validates MEMORY.md), `wal_checkpoint.md` (PRAGMA wal_checkpoint).
- Update [DEVELOPMENT.md](../../../DEVELOPMENT.md) and [CLAUDE.md](../../../CLAUDE.md) to document the seven memory layers explicitly.

*Half B (identity + dynamic prompt):*
- Create: `prompts/builder.py`, `/app/jarvis_memory/AGENTS.md` (extract from current `_BASE_SYSTEM_PROMPT`), `/app/jarvis_memory/USER.md` (consolidate user_preferences.txt + relevant memories).
- Edit: [agent.py](../../../agent.py) — delete `_BASE_SYSTEM_PROMPT`; expose `build_agent_for_turn(scope, thread_id)` (already restructured in Phase 2).
- Edit: [main.py](../../../main.py) — drop `time_ctx` prepending; pass `scope="user"`.
- Edit: [heartbeat.py](../../../heartbeat.py) — drop manual `[HEARTBEAT — scheduled check]` prefix and inline HEARTBEAT.md/daily-logs construction (the prompt builder owns it now).
- Edit: prompt builder includes today's daily log for user scope, last 2 days for heartbeat scope.
- Delete: backward-compat `media_paths`/`media_types` branch in [agent.py:267-272](../../../agent.py#L267-L272).

**Verification:**
1. `pct exec 106 -- systemctl restart jarvis.service` — system prompt logged once at startup per scope. Verify SOUL.md + AGENTS.md + USER.md content all present.
2. Telegram chat: ask "what did I do yesterday?" — Jarvis answers from yesterday's daily log without manually calling `read_memory` (it's already in the prompt for user scope).
3. Edit `/app/jarvis_memory/AGENTS.md` to add a temporary rule like "always end your response with 🦊", restart, send a message — behavior changes without touching code. Revert.
4. Trigger heartbeat — log heartbeat-scope prompt; verify HEARTBEAT.md and last 2 daily logs present.
5. Confirm time context is in the prompt envelope, not prepended to user text in [main.py](../../../main.py).
6. Verify `/app/jarvis_memory/media/` is gone and `gateway/telegram/media_cache/` holds the blobs; old chat history that referenced media still resolves.
7. Verify MEMORY.md audit task runs and reports any drift; intentionally create a stray `.md` and confirm it's flagged.

**Done criterion:** `_BASE_SYSTEM_PROMPT` deleted from [agent.py](../../../agent.py). All seven memory layers documented in CLAUDE.md. Telegram media blobs no longer live under `jarvis_memory/`. Daily logs visible to user scope. fitness.sqlite either explicitly documented as a "domain DB" tier or eliminated.

---

## Voice / Phone Channel — What It Forces Us to Reconsider (your question)

You asked whether the lack of streaming today blocks a future "talk on the phone with Jarvis" capability. Honest answer: **it doesn't block, but voice forces architectural decisions that the chat-only plan can otherwise duck.** Worth surfacing now so Phase 1's Channel ABC is forward-compatible.

### What voice/phone actually requires

A phone channel (Twilio Voice + Whisper STT + ElevenLabs/Cartesia TTS, or a unified provider like Vapi) needs:

1. **Sub-second response onset.** A turn that takes 8 seconds before the first audible word feels broken. Target: first audio chunk within 500ms of the user finishing speaking.
2. **Streaming output.** TTS starts playing the first sentence while the LLM is still generating the rest. Without streaming, the latency is "full LLM response time + full TTS time."
3. **Tool-call masking.** The LLM should be able to call tools without leaving conversational dead air. Either a filler phrase ("let me check…") plays during tool execution, or tools are short enough to fit in the dead air.
4. **Interruptibility.** User can cut off Jarvis mid-sentence. Requires the channel to detect speech and pre-empt the playback.

### What this means for our Channel ABC

The chat `Channel.send(text)` returns when the message is fully delivered — the LLM has finished generating, then `send()` posts. For voice this is unworkable.

**Forward-compatible Channel ABC** (Phase 1 should adopt this even though Telegram won't use it yet):

```python
class Channel(ABC):
    name: str
    supports_streaming: bool = False  # default False; voice channels override

    @abstractmethod
    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None: ...

    async def send_stream(self, chat_id: str, chunks: AsyncIterator[str]) -> None:
        """Default impl: collect all chunks, then send. Voice channels override."""
        full = "".join([c async for c in chunks])
        await self.send(chat_id, full)
```

Telegram inherits the default (it has typing indicators but no token-level streaming). A future `VoiceChannel` overrides `send_stream` to push tokens to TTS as they arrive.

### What this means for the agent loop

OpenClaw's runtime emits assistant deltas on a streaming bus (separate from tool events) — that's why their voice integrations work. Our `StateGraph` agent (Phase 2) needs an equivalent.

LangGraph supports streaming via `compiled_graph.astream_events(...)` which yields `on_chat_model_stream` events containing token deltas. **Phase 2's agent runner should expose two entry points:**
- `ask_jarvis(text, thread_id)` → returns final string (used by current Telegram flow).
- `ask_jarvis_stream(text, thread_id)` → returns async iterator of token chunks (used by voice flow when it lands).

Both go through the same graph; only the consumer differs. Adding `ask_jarvis_stream` is ~30 lines and doesn't affect Telegram.

### Decision

**Phase 1** adopts the streaming-aware Channel ABC. Default impl is non-streaming; only voice channels override. **No additional implementation work today** — just don't paint ourselves into a non-streaming corner.

**Phase 2** exposes both `ask_jarvis` and `ask_jarvis_stream` against the same graph.

**Voice channel itself is its own milestone** (M5 below). Significant integration work — Twilio webhooks, STT/TTS provider choices, latency tuning, interruptibility — but unblocked by the architecture.

OpenClaw note: their streaming-first design (assistant/tool/lifecycle event bus, `subscribeEmbeddedPiSession` bridge) is exactly the substrate that makes voice tractable for them. We can reach the same outcome with LangGraph's `astream_events` without rebuilding their event bus.

---

## Tool Data Location

You raised this correctly: shoving runtime DBs into `tools/<tool>/data/` mixes mutable data into the code repo, and `.gitignore` is a workaround, not a structure.

### The principle

Three top-level directories under `/app/`, each with a single role:

| Path | Role | Contents |
|---|---|---|
| `/app/jarvis_code/` | Code | Python source, tests, docs. Git-tracked. Conceptually read-only at runtime. |
| `/app/jarvis_memory/` | Agent's mind | Markdown the agent reasons about (SOUL/AGENTS/USER/MEMORY/daily/heartbeat) + LangGraph runtime state (`threads.sqlite`) + audit logs (`chat_history.jsonl`, `notifications.jsonl`). Read/written via memory tools by the agent. |
| `/app/jarvis_data/` | Tool-owned state | Tool DBs and code-managed JSON the agent doesn't read directly. Each tool gets its own subdir: `scheduling/`, `fitness/`, ... |
| `/app/secrets/` | Credentials | API keys, tokens. Not reachable from the agent. |

(See "Backups & Disaster Recovery" — backup strategy is **out of scope** for the three phases and tracked separately.)

### Why not `/var/lib/jarvis/`?

That's the Linux/systemd convention for service runtime state and would be the "professional" answer if Jarvis were shipping as a containerized service to many hosts. In practice:
- Jarvis runs on one LXC, deployed by hand.
- Existing `/app/` convention is consistent and discoverable (everything Jarvis-related is one `ls /app` away).
- Moving to `/var/lib/` later is a one-line systemd `WorkingDirectory=` change + path constant updates — trivially reversible.

Stay on `/app/`. Revisit if Jarvis ever runs on multiple hosts or ships as a container image.

### Why not `tools/<tool>/data/`?

It was the answer when the question was "where does fitness.sqlite go," but it doesn't generalize:
- Mixes runtime data into the code repo (gitignore games).
- Implies tools own their data path, which is fine for fitness.sqlite but breaks down for shared state (e.g. if scheduling and heartbeat both want to read `scheduled_events.json`).
- Reorganizing tool code (Phase 2 splits `tools/` into namespaced subdirs) would force data path changes too — coupling code structure to data structure.

`/app/jarvis_data/<tool>/` keeps data path stable across code reorgs.

### Migration

In Phase 3 Half A:
- Create `/app/jarvis_data/scheduling/`, `/app/jarvis_data/fitness/`.
- Move `scheduled_events.json` and `fitness.sqlite` to their new homes.
- Update [tools/heartbeat_tools.py](../../../tools/heartbeat_tools.py), [heartbeat.py](../../../heartbeat.py), [tools/fitness_tools.py:9](../../../tools/fitness_tools.py#L9) to read from new paths (env-overridable).
- Update CLAUDE.md to document the three-dir layout.
- One-time migration: `pct exec 106 -- mv /app/jarvis_memory/scheduled_events.json /app/jarvis_data/scheduling/` etc., done while service is stopped.

---

## Backups & Disaster Recovery — OUT OF SCOPE for the three phases

Currently Jarvis has no backups. The failure modes are real and worth naming so we don't forget the gap exists:

- `threads.sqlite` WAL corruption → conversation state lost.
- Agent bug overwrites SOUL.md or MEMORY.md → identity/index loss.
- LXC 106 storage failure → everything gone except credentials.
- `fitness.sqlite` drift → workout logs irrecoverable unless re-scraped from Arbox.
- Accidental `delete_memory` confirmed-by-mistake → file destroyed.

**This is parked, not solved.** The right strategy needs more thinking than fits inside Phase 3 — choices around snapshot frequency, retention, off-host vs local, whether to add a git layer for markdown, how to coordinate with the file write lock, restore procedure testing, encryption at rest, etc. None of that should be force-fit into the three phases.

What's been considered but not committed to:
- Daily local tarballs with `VACUUM INTO` for SQLite consistency.
- Off-host sync via restic/borgbackup to NAS/B2/another LXC.
- Git layer for `/app/jarvis_memory/` to give markdown free diff history.
- Inline versioning (`SOUL.md.bak.<timestamp>`) as a lighter alternative to git.

Each has trade-offs that need to be weighed when this gets its own scoping pass. **Treat as a separate workstream.** Until then: be aware loss of state is unrecovered.

---

## Deferred Milestones (after the three phases land)

These are real but lower-priority. Listed so they don't get lost.

### M4 — Read API for the custom app

After the architectural shift, build a separate FastAPI surface for the custom app's UI panels. Read-only over `/app/jarvis_memory/` and runtime state. **No agent invocation** — the API queries files and the scheduler, never runs LLM turns.

Sketch:
```
api/
├── server.py            # FastAPI app
├── memory.py            # GET /memory, GET /memory/{file}
├── heartbeat.py         # GET /heartbeat/tasks, GET /heartbeat/daily/{date}
├── reminders.py         # GET /reminders (from scheduled_events.json)
└── auth.py              # token auth
```

Why deferred: shipping value depends on the custom app being built; doing it now risks designing for a UI that doesn't exist yet. After Phase 1's gateway abstraction lands, adding this is straightforward.

### M5 — Load-time tool gating by env vars

You said you always have all keys, so this isn't urgent. The pattern (skip Sonarr if `SONARR_API_KEY` unset, skip Arbox if no token) is useful for graceful degradation when a service is temporarily down or for future deploys with partial config. Cheap to add later via `requires_env=[...]` on `@tool_register`. **Do not block the main phases on this.**

### M6 — Voice / phone channel

Twilio Voice (or Vapi) + STT + TTS. Implement `VoiceChannel(Channel)` overriding `send_stream`. Use `ask_jarvis_stream` from Phase 2. Add filler phrases for tool execution. Tune latency end-to-end. The architecture lands in Phase 1+2; this milestone is just the integration work. Significant effort (provider selection, webhook plumbing, interruptibility, cost tuning) but unblocked by the refactor.

### M7 — Backups & disaster recovery (separate workstream)

Backup strategy is parked entirely (see "Backups & Disaster Recovery" above). Not part of the three phases. When picked up, needs its own scoping: local snapshots vs off-host sync vs git layer vs inline versioning, retention, restore-test procedure, encryption.

---

## Risk & Rollback

- **Phase 1** is the riskiest (touches every tool's gateway access). Mitigations: preserve `get_gateway()` as a deprecated shim during the move; do tools in waves (memory → media → heartbeat).
- **Phase 2** has a behavior delta from skill activation UX. Keep the agent_executor singleton behind a feature flag during rollout; A/B for a day.
- **Phase 3** is low risk (file extraction + prompt assembly); easy to roll back by reinstating the hardcoded prompt.

Each phase ships independently; nothing requires the next phase to be useful.

(Per-phase critical-files lists are now in each phase's "Reference reading" subsection. The repo-wide files-to-know list is in the Prerequisites section.)
