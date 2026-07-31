# App channel — agent-side adapter (Track B)

**Status:** approved plan, not started (2026-07-12). Grilling round 2 (2026-07-12): decisions below locked, B4 amended, deferred list added.
**Repo:** lands **here** (`jarvis-agent`) — `gateway/channels/app/` plus the gateway capability work below. Follows GATEWAY.md's add-a-channel checklist; Telegram untouched and regression-tested each phase.
**Companion plans:** the hub backend + Android client are Track A (new `jarvis-app` repo), split across **[JARVIS_APP_ARCHITECTURE.md](JARVIS_APP_ARCHITECTURE.md)** (owns the contract), **[JARVIS_APP_IMPL.md](JARVIS_APP_IMPL.md)** (phases), and **[JARVIS_APP_CHECKLIST.md](JARVIS_APP_CHECKLIST.md)** (progress). This doc owns everything the agent does with that contract.
**Goal:** the Android app becomes a second channel next to Telegram — full chat basics (history, search, attachments, voice), rich tool-specific payloads (workout cards, confirmations, forms), streaming of tool activity later, and eventually agent-published "apps" (memory browser, fitness DB views) — while the agent codebase stays channel-agnostic and never learns the app exists.

---

## What the agent talks to

The new backend ("**jarvis-hub**", Track A) is a *self-hosted Telegram-Bot-API clone for exactly one bot and one user*. The Android app is the "Telegram client"; **jarvis-agent is the "bot"** and connects **out** to the hub via long-poll — a thin adapter implementing the existing `Channel` ABC, exactly like PTB polling. The hub never knows an agent URL (our LXC is default-deny inbound), and neither hub nor app imports or addresses jarvis-agent.

```
[Android app] ⇄ HTTPS+SSE ⇄ [jarvis-hub backend] ⇄ bot API (agent connects OUT) ⇄ [gateway/channels/app/]
                             Track A — see JARVIS_APP_*    long-poll, like PTB          this doc
```

**Bot API surface the adapter consumes** (`Bearer <AGENT_TOKEN>`, single static token; full contract in Track A):

- `GET /bot/v1/updates?offset=&timeout=25` — hanging long-poll, Telegram `getUpdates` semantics; `offset` acks everything below it; unacked updates replay on agent restart.
- `POST /bot/v1/messages {text?, blocks?, attachment_ids?, meta?}` — at least one of `text` / `blocks` / `attachment_ids` must be present · `PATCH /bot/v1/messages/{id}` (widget resolution: PATCH `blocks[].state`)
- `POST/GET /bot/v1/attachments`
- `POST /bot/v1/events` — ephemeral SSE fan-out (tool chips, deltas), never persisted
- `POST /bot/v1/commands` · `POST /bot/v1/apps`, `POST /bot/v1/apps/{id}/results` (later)

Updates are `{update_id, type: "message"|"action"|"app_query", ...}`. **Action updates carry the source block's kind** (`{"block_kind": "confirmation"|"form"|"buttons"|…}`, stamped by the hub), so agent-side routing is a typed switch, never payload sniffing (B2).

**Contract facts that constrain agent code.** Canonical definition: Track A's [JARVIS_APP_ARCHITECTURE.md](JARVIS_APP_ARCHITECTURE.md) §5 — this is a digest for the adapter author, **not a second source of truth**. When they disagree, §5 wins.

- A message is **`text` + `attachments` + `blocks`**. Any may be absent; **at least one must be present** — the one composition rule the hub validates.
- **`text` is markdown**, and it is optional. There is no format field; the agent sets nothing.
- **Bytes are attachments**: images, voice, PDFs are uploaded to `/bot/v1/attachments` and referenced by `attachment_ids` (hub enforces a 50 MB cap and a mime allowlist). The client renders them inline.
- **Blocks are interaction** — things you tap. The kinds are **`card`**, **`form`**, **`buttons`**, **`confirmation`**. The hub **rejects an unknown kind with a 422**; it does not degrade it.
- `card` and `form` carry their own prose (title, body, field labels). **`buttons` and `confirmation` carry none** — they are affordances only, so the prose that gives them meaning ("Delete Severance with files?") belongs in the message's `text`. This binds B2 and B4.
- The hub's Pydantic models are the single enforcement point, so **the agent stays dict-loose**; Track A's `scripts/fake_agent.py` doubles as executable examples of valid payloads.

**One consequence for B4:** an app-bound message may carry `blocks` with `text=None`, but Telegram cannot send an empty message. `Channel.send_rich`'s default (`→ send(text)`) and `FanoutChannel`'s per-channel degradation must both handle `text=None` — skipping the Telegram send rather than emitting an empty one.

---

## Decisions locked (with Roi, 2026-07-12)

| Decision | Choice |
|---|---|
| Threads | Separate `app_<user_id>` thread, cross-aware with Telegram via the existing log-injection pattern |
| Rich proactive turns | Blocks seam lives **inside `ask_jarvis`** (every turn returns `OutboundReply`) — chat, heartbeat, reminders, and confirmation acks are all rich-capable via `send_rich` (amends B4) |
| Confirmations | Broadcast to both channels, first-resolve-wins (see B2) |
| Degraded mode | Hub-down = log once + backoff 1→60 s forever; Telegram/heartbeat unaffected |

**Risk posture.** Track B is strictly additive: the app channel is built behind `APP_HUB_URL` env gating, hub-down degrades to Telegram-only with backoff (never crashes the agent), and the Telegram regression + gateway conformance checklist run after every phase. Two small, deliberate ABC evolutions are flagged for explicit sign-off (B4/B5, and the Flagged decisions section).

**Env additions to `/app/secrets/.env`:** `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID`.

**Known new failure mode — concurrent same-user turns (documented 2026-07-12; accepted for v1, not implemented against).** A second user channel makes it possible for the same person to have two turns in flight at once (`telegram_<id>` and `app_<id>` both mid-`ask_jarvis`); today's only cross-thread concurrency is heartbeat-vs-chat. LangGraph thread state is isolated per thread, but the shared surfaces are not: memory files, `scheduled_events.json`, the confirmation store. `ask_jarvis` has no lock. Accepted single-user (collision odds are low; worst case is interleaved memory-file writes, not thread-state corruption). If it bites, the remedy is a global turn lock (or per-resource lock) around `ask_jarvis` — cheap, additive, zero contract change.

---

## Phases

- **B0 — Contract pin + doc.** New `docs/architecture/APP_CHANNEL.md` (adapter mapping, contract version, degraded-mode, the two ABC exceptions below); update GATEWAY.md tables. **The pin is against something real:** Track A generates `docs/CONTRACT.md` from its Pydantic models and reports `contract_version` on `GET /v1/health`, so B0 records the version the adapter was written against, and `HubClient` **logs a warning on mismatch at startup**. It does not hard-fail — the hub's strict validation already 422s a bad payload; this exists so a *silent* skew has a voice.

- **B1 — Channel: text round-trip.** New `gateway/channels/app/`: `client.py` (`HubClient` on httpx: get_updates/send_message/edit_message/upload/download/post_event/set_commands; `HubUnavailable` on 5xx), `channel.py` (`AppChannel(Channel)`, name="app"), `router.py` (long-poll loop → `InboundMessage(thread_id=f"app_{owner}")` → shared `on_message` → reply; **hub-down = log once + backoff 1→60 s forever; Telegram/heartbeat unaffected**). Factory: `build_app_stack(...)`. main.py: build if `APP_HUB_URL` set, `create_task(router.run())`, publish slash commands. Slash commands + history logging work for free (channel-agnostic).

  **The poll loop must be concurrent** — fetch in one task, advance the offset as soon as the batch is in hand and re-poll immediately; feed a queue; drain it with **one** consumer that runs the turns. This is PTB's fetcher/dispatcher split, so it is the shape the agent already has on Telegram, not a new one. A serial `poll → process → poll` blinds the agent to anything arriving mid-turn *and* collapses the app's ✓✓ tick into the reply, because the ack **is** the next poll. But do *not* spawn a task per update either: that would overlap same-user turns, the failure mode accepted-but-not-implemented-against above. **On SIGTERM, drain**: stop fetching, finish what is queued, then exit — updates are acked on receipt, so anything fetched and unfinished is already gone from the hub, and a deploy is the most frequent restart there is. Full reasoning: JARVIS_APP_IMPL.md, *Sync points with Track B*, and ARCH §7.

- **B1.5 — Cross-device continuity (sibling-thread chat injection; promoted 2026-07-12 from the parked open item).** Unlike heartbeat↔chat, telegram↔app is the *same person switching devices mid-conversation* — daily-log lag means "as I said a minute ago" fails on day one, so this ships with B1, not later. Reuse the existing live log-injection mechanism in `build_system_prompt`: user-scope prompts additionally inject today's chat from the sibling user thread (among prefixes `telegram_`/`app_`, filter to the thread that is *not* the current one), bounded by the same start-of-Israel-day window and per-entry cap as the existing slices.

- **B1.6 — Media inbound (lands in Stage 4, with A3 + M4).** `router.py` gains: download attachments via `HubClient` → `media_cache.save` → attach to the `InboundMessage` (Gemini reads voice and images natively; the agent echoes the transcription). New `media_cache.py`, copying the Telegram pattern. **Deliberately not part of B1:** the hub has no attachments until A3 and the app cannot send any until M4, both a stage later — so written inside B1 this code would be unexercisable at the moment it was written, and "B1 done" would mean two different things depending on who was asked. It ships when there is media to feed it.

- **B2 — Confirmations.** `AppConfirmationUI` (confirmation block send / PATCH outcome / expire; `message_id↔callback_id` map). New `gateway/confirmation/fanout.py` — `FanoutConfirmationUI([telegram, app])`: prompt fans out, raises only if ALL fail; first resolve wins (store already handles the loser); `edit_outcome`/`expire` fan out to **all** UIs so the losing channel's prompt updates too (verified: the store already calls `ui.expire` on TTL sweep, store.py:211 — the hook exists). One shared `InMemoryConfirmationStore` wired to the fan-out UI; its second constructor arg — the verbatim-outcome fallback channel (store.py:35, used only when `on_outcome` is unwired, store.py:178) — becomes the B3 `FanoutChannel`, not the bare Telegram channel. **Action routing is a typed switch on the update's `block_kind`** (stamped by the hub): `"confirmation"` → `store.resolve(callback_id, confirmed)`, below the LLM, exactly like a Telegram inline-button callback; every other or unknown kind falls through to the B4 synthesized turn (fail-safe default — new interactive kinds land in the LLM path until explicitly registered). The switch doubles as an extensible below-LLM dispatch table: future block kinds needing deterministic no-LLM resolution (e.g. a card "refresh" re-query, a reminder "snooze") register a handler alongside `confirmation`, same altitude as slash commands and B6 `app_query` answering. Known v1 asymmetry: `on_confirmation_outcome` (main.py:102) still acks on the telegram thread — origin-thread routing is a flagged follow-up.

- **B3 — Proactive fan-out.** New `gateway/fanout.py` — `FanoutChannel(primary, secondaries)`: owner-addressed methods iterate channels with per-channel try/except; kind-unsupported skipped. Factory sets it as `default_user_channel` (`PROACTIVE_CHANNELS=telegram,app`) — heartbeat.py/reminders need zero changes; main.py passes it to `MediaNotificationManager` (verified constructor-injected, main.py:126). Cross-awareness: widen the `agent.py:257` filter to `("telegram_", "app_")`.

- **B4 — Rich payloads (the deliberate seam; amended 2026-07-12 — seam moved inside `ask_jarvis` for channel symmetry).** New `gateway/outbox.py`: `TURN_BLOCKS` ContextVar — seeded at entry and drained in the `finally` of `ask_jarvis` itself (same pattern as `turn_context.CURRENT_SCOPE`), so **every** turn is rich-capable regardless of caller; tools call `emit_block(dict)` (no-op outside a turn). **ABC exception (a)**: `OutboundReply{text, blocks}` dataclass; `ask_jarvis` returns it from all call sites; `OnMessage` returns `str | OutboundReply | None`; non-abstract `Channel.send_rich(...)` defaulting to `send(text)` — Telegram byte-identical. All four delivery sites use `send_rich`: chat reply (main.py), heartbeat notify (heartbeat.py:140), reminders (heartbeat.py:169), confirmation acks (main.py:108); `FanoutChannel` gains `send_rich` with per-channel degradation; heartbeat `[NO_ACTION]` gating and notification logging key off `.text` (unchanged). **Two deterministic entry points to rich content — block payloads are never LLM-generated JSON:** (a) *in-turn*: tool code builds the block dict and calls `emit_block`; the LLM only decides to invoke the tool, the payload is code-built; (b) *out-of-turn*: no-LLM code paths construct `OutboundReply(text, blocks=[...])` directly and call `send_rich` — the `TURN_BLOCKS` ContextVar is the in-turn collection mechanism, not a gate on rich content. Reminders are rich-capable via (b) only (`fire_reminder` runs no LLM turn); heartbeat notify and confirmation acks are rich-capable via (a). Regression covers all four flows, not just chat. Telegram router normalizes `OutboundReply→.text` (2 lines); app router calls `send_rich`. First consumer: fitness tool emits a workout `card`. Generic widget actions: the fall-through arm of B2's typed `block_kind` switch — app router synthesizes a turn (`"[app action] User tapped … values {…}"`) then PATCHes the block's `state` — no channel knowledge leaks into tools.

- **B5 — Streaming chips.** New `observability/turn_events.py` (thread-safe pub/sub). `telemetry.record_tool_call` publishes `tool_call_result` (+2 lines); **ABC exception (b)**: one `record_tool_call_start` line in `_tool_node` before `tool.invoke`. AppChannel subscribes, filters `thread_id.startswith("app_")`, bridges worker-thread → loop via `run_coroutine_threadsafe(client.post_event(...))`; hub fans out, never persists. Token deltas (`astream_events` async entry point) deliberately out of scope — separate later effort; contract slots reserved.

- **B6 — Apps (later).** `gateway/channels/app/apps.py`: publish manifests at startup; answer `app_query` updates **below the LLM** (same altitude as slash commands, e.g. memory readers) via `/results`. Manifest schema deferred until the first real app (memory browser) forces it.

---

## Sync points & build order

**Owned by [JARVIS_APP_IMPL.md](JARVIS_APP_IMPL.md) → *Sync points with Track B*** — the capability table and the cross-track build order live there, and this doc links rather than copies. They were duplicated once, and the copies drifted (this one had lost M2.5, the MVP marker, and the whole tail) — which is the entire argument for a link.

What Track B needs to know about the ordering: **B1 lands as early as possible** (right after the Android client can talk to `fake_agent`), so every later phase is exercised against the real agent rather than a stand-in. **B1.6** waits for Stage 4, because there is no media before A3 + M4.

## Verification

Real hub in its LXC; curl the client API as "the app" while the live agent long-polls (phone-less E2E); check `chat_history.jsonl` rows carry `app_1`. After every B phase run the Telegram regression (GATEWAY.md step 9: inbound→reply, heartbeat proactive, confirmation, `/help`) + the repo `code-review` skill (gateway conformance checklist) pre-merge. Backend-down drill in B3: stop the hub LXC → reminders still reach Telegram, agent backs off cleanly, backlog drains on restart. Per project convention: Roi restarts `jarvis.service` after each deploy; verify via journalctl + a real Telegram/app message.

## Open items — parked for a separate Track B grilling session

- **Automated messages vs Jarvis's own words.** As non-LLM-composed messages appear (templated reminders, canned acknowledgements, heartbeat notices), decide whether the app should render them differently from prose Jarvis actually wrote — and whether the *user* should be able to tell.

  Do not reach for `meta.source` when that day comes. It carries the **trigger** (`heartbeat`/`reminder`/`notifier`), which is a different axis from **authorship**: a reminder may be composed by the LLM ("traffic's bad, leave early") or emitted as a canned string ("⏰ Dentist, 15:00"), and `source: "reminder"` cannot distinguish them. `meta.source` is also deliberately *non-branchable* by the app (ARCH §4 — those are Jarvis's concepts, not universal ones), whereas *"did a model write this?"* is a question any agent can answer about itself.

  So if the distinction turns out to matter, it needs its own contract field — a **Track A change**. Raise it there rather than overloading `source`.

- ~~Cross-channel awareness between the `telegram_` and `app_` user threads~~ — resolved 2026-07-12: live sibling-chat injection, promoted to B1.5
- Sign-off on ABC exception (b) — `record_tool_call_start` hook in `_tool_node` (B5)
- Outbound media notifications to the app: Telegram-only until A3+M4 (`FanoutChannel` skips unsupported kinds) — confirm that's acceptable
- **Agent offset must not outlive a rebuilt hub queue.** If the hub's `bot_updates` are wiped and re-sequenced (a dev wipe; an A7 restore) while the agent keeps a live, higher offset, its next poll acks everything below that offset — stamping ✓✓ on the reset-id updates it **never fetched** (seen in M3.1 dev testing: a wipe under a running agent marked the sends delivered with no reply). This is the agent-side mirror of ARCH §7c's client-side rebuild asymmetry, and a sharper face of flagged-decision 3 — there the agent at least *received* the update before losing it. Pre-A7 remedy is to restart the agent alongside the wipe (offset resets to 0), exactly as §7c treats client+hub as wiped together. Decide whether B1 should re-sync/reset its offset on a detected rebuild rather than lean on a manual restart.

## Flagged decisions (need sign-off during implementation)

1. **Two additive ABC/agent exceptions** (GATEWAY.md says push back; these are deliberate): `Channel.send_rich` + `OutboundReply` return type (B4) — **signed off 2026-07-12 in the amended seam-inside-`ask_jarvis` form**; `record_tool_call_start` observability hook in `_tool_node` (B5) — still pending sign-off. Both behavior-preserving for Telegram; documented in APP_CHANNEL.md.
2. **Confirmation broadcast** (both channels, first-resolve-wins) for v1; origin-thread routing (also fixes the main.py:102 hardcoded telegram ack-thread) recommended as a follow-up.
3. **Bot updates are acked on receipt** — a crash loses everything fetched and not yet completed (the fetcher never stops, so a long turn accumulates an in-memory queue), and nothing recovers it: the liveness watchdog sees a healthy agent and the ticks read ✓✓. Same exposure as Telegram today; full rationale and the agent-side durability escape hatch in ARCH §7. The obligation it puts on B1 is the **concurrent poll loop** (see B1, *Phases*).
4. **Token-delta streaming** requires an async `ask_jarvis` variant (`astream_events`) — scheduled separately after B5.

## Deferred — explicitly not v1 (agent side)

- Origin-thread confirmation routing (flag 2)
- Token-delta streaming (flag 4) — and, downstream of it, voice calls with Jarvis
- A turn lock around `ask_jarvis` for concurrent same-user turns (see Known new failure mode)
