# Jarvis

<p align="center">
  <img src="docs/assets/jarvis-banner.png" alt="Jarvis" width="420">
</p>

A stateful, **proactive** personal AI assistant. Jarvis lives as a background
service, talks over Telegram, and takes real actions - managing a media stack,
setting reminders, searching the web, tracking fitness, and maintaining its own
long-term memory. Unlike a request/response chatbot, it also **wakes itself up on
a schedule** to check on things and reach out first.

Built as a hand-rolled [LangGraph](https://github.com/langchain-ai/langgraph)
agent over Google Gemini, with an architecture designed around three ideas:
**channel independence**, **progressive tool disclosure**, and **a two-scope
(interactive + autonomous) runtime that shares one memory**.

> Single-user by design - this is a personal assistant, not a SaaS. The
> interesting parts are architectural, and they're documented below.

---

## Design note: deterministic and sandboxed

The organizational patterns here - the channel gateway, file-driven identity,
scoped on-demand tools, the heartbeat, confirmation-gated actions - are adapted
from [**OpenClaw**](https://docs.openclaw.ai), an autonomous agent architecture.
Jarvis imports its *structure* but deliberately rejects its *capability surface*.

Where OpenClaw gives an agent a terminal, arbitrary code execution, and a plugin
marketplace, Jarvis has none of that. Two properties are enforced by design:

- **Sandboxed.** Every action Jarvis can take is an explicit, registered tool -
  and nothing else. There is no shell, no `exec`, no filesystem access beyond a
  path-validated memory directory. Its blast radius is exactly its tool set,
  which matters for something running unattended with access to real accounts.
- **Deterministic.** Behavior is driven by tools, not open-ended autonomy: a
  hand-rolled LangGraph `StateGraph` (not a prebuilt ReAct loop), low sampling
  temperature, per-scope tool whitelists, and explicit skill activation. What the
  agent does on a given turn is auditable and bounded.

---

## Highlights

- **Proactive heartbeat loop.** An APScheduler-driven agent turn runs on a cadence
  with its own prompt scope. It reads its task list, checks whether the user
  already handled something in chat, and only then acts - sending a briefing or
  staying silent via an explicit `[NO_ACTION]` contract.
- **Channel-decoupled gateway.** The agent never imports Telegram. A neutral
  `Channel` boundary (inbound messages, owner-addressed sends, confirmations)
  means a new channel - email, web, anything - is a new folder, not a rewrite.
- **Scoped tool registry + same-turn skill activation.** Tools are grouped into
  skills that stay *hidden* until the model activates them (`activate_skill`),
  keeping the prompt small and the tool surface relevant. Skills self-describe via
  a `SKILL.md` file; the registry auto-discovers them.
- **File-based, sandboxed memory.** Identity (`SOUL.md`), rules (`AGENTS.md`),
  user profile (`USER.md`), and daily logs are plain Markdown, read fresh every
  turn (hot-reload) and assembled into the system prompt. A path sandbox confines
  what the agent can read/write.
- **Bounded state + observability.** Conversation state is pruned to O(1) per
  thread at the storage layer; media blobs are stripped before persistence; every
  turn emits structured telemetry (tokens, tool calls, latency) to JSONL.
- **Confirmation pattern for destructive actions.** Irreversible operations route
  through a channel-agnostic confirmation flow (e.g. a Telegram inline button)
  before they execute.

## Tools

Jarvis's abilities *are* its registered tools - an explicit surface, not
open-ended capability. The registry is dynamic and extensible: a new tool is just
a decorated function the registry auto-discovers, and a new skill is a folder with
a `SKILL.md`. Core tools are always on; skills stay hidden until activated. The
list below is the set that exists **today**, not a fixed ceiling:

| Tool group | What it does |
|------------|--------------|
| **Core** (always on) | Long-term memory, web search, reminders/scheduling, chat history |
| **Media** (skill) | Search, request, and manage TV/movies across Sonarr, Radarr, Prowlarr, Jellyseerr; aggregated download-ready notifications from webhooks |
| **Fitness** (skill) | Gym class schedule/booking (Arbox), workout & running logging |
| **Health** (skill) | Sleep, workouts, resting HR, and HRV from a Pixel Watch (Google Health) |
| **GitHub** (skill) | Read issues/PRs, project management |

---

## Architecture

Two LangGraph threads share the same tools and memory but get **different prompts
by scope**: the user scope is conversational and sees today's proactive
notifications; the heartbeat scope is terse, sees today's chat, and follows the
`[NO_ACTION]` tick contract. Awareness flows both ways so the two never duplicate
each other's work.

Deep dives live in [`docs/architecture/`](docs/architecture/):

- [`GATEWAY.md`](docs/architecture/GATEWAY.md) - channel boundary, slash commands, confirmations, media notifier
- [`MEMORY.md`](docs/architecture/MEMORY.md) - placement principle, prompt assembly, access model
- [`RUNTIME.md`](docs/architecture/RUNTIME.md) - agent loop, scoped registry, skill activation
- [`OBSERVABILITY.md`](docs/architecture/OBSERVABILITY.md) - per-turn telemetry

---

## Tech stack

**Python** · **LangGraph** (hand-rolled `StateGraph`) · **LangChain** ·
**Google Gemini** (`langchain-google-genai`) · **FastAPI** (webhook ingress) ·
**APScheduler** (heartbeat + reminders) · **SQLite** (checkpointer) ·
**python-telegram-bot**.

---

## Running it

```bash
# 1. Install
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# 2. Configure - copy the template and fill in real values
cp .env.example .env      # see .env.example for every variable + notes

# 3a. Local REPL (no Telegram) - isolated dev thread
python3 agent.py

# 3b. Full service (Telegram + webhooks + heartbeat)
python3 main.py
```

Only a Telegram bot token and a Google API key are required to boot; every other
integration (media, fitness, health, GitHub) degrades gracefully to a
"not configured" response when its env vars are absent.

Deployment notes (systemd unit, runtime constants, local testing) are in
[`DEVELOPMENT.md`](DEVELOPMENT.md); the "where do I add X" map is in
[`CLAUDE.md`](CLAUDE.md).

---

## License

[MIT](LICENSE) © 2026 Roi Guri
