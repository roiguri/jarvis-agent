# Step 2 — Multi-channel support

**Status:** planning, uncommitted. **Date:** 2026-07-20.
**Parent:** [APP_CHANNEL_PLAN.md](APP_CHANNEL_PLAN.md). **Depends on:** nothing.
**Goal:** the gateway can host more than one channel. All work here is on **existing** code and
stands on its own — no new channel is added, and Telegram behavior is unchanged throughout.

Step 3 (`gateway/channels/app/`) cannot start until this lands: today three singletons and one
hardcoded thread-prefix assume exactly one channel exists.

---

## Checklist

**Bold** items are yours (restarts). Every phase ends with the Telegram regression
(GATEWAY.md step 9: inbound→reply, heartbeat proactive, confirmation, `/help`).

### Phase 1 — channel-agnostic cleanup (no behavior change) — ✅ implemented 2026-07-23
- [x] 1a — move `gateway/markdown_to_html.py` into `gateway/channels/telegram/` (copy in `_render.py` kept — the two renderers are genuinely different, not duplication)
- [x] 1b — drop channel names from tool docstrings (`radarr.py`, `sonarr.py`)
- [x] 1c — drop channel names from comments/docstrings (`observability/usage.py`, `gateway/commands/router.py`, `gateway/confirmation/{base,store}.py`)
- [x] 1d — generalize the factory: `build_stack(name)` + neutral `Stack` Protocol; Telegram builder is private `_build_telegram_stack` behind a name registry (no public wrapper); `main.py` calls `build_stack("telegram", …)`. Factory kept as composition root (concrete imports stay) — considered moving the builder into the channel package, chose not to
- [ ] **Restart staging + Telegram regression** — *pending*; prod happens later via `deploy.sh`

### Phase 2 — default channel + origin routing — ✅ implemented 2026-07-23
- [x] 2a — `JARVIS_DEFAULT_CHANNEL` (default `telegram`); registry keyed by `Channel.name`
- [x] 2b — `default_outbox()` resolves through the configured default (at call time)
- [x] 2c — confirmations origin-scoped: per-channel store, origin-resolved in `get_confirmation()` (Design B)
- [x] Verify: with one channel every path byte-identical (unit-verified: resolution + channel-local ack)
- [x] **Restart staging**; Telegram regression + confirmation round-trip — ✅ verified live 2026-07-23 (confirm + cancel round-trips, SOUL.md protected write, proactive reminder)

### Phase 3 — rendering
- [ ] 3a — `OutboundReply{text, blocks}` + `Channel.send_rich`, default → `send(text)` (upstream B4)
- [ ] 3b — Telegram renders blocks as text; `text=None` skips rather than sending empty
- [ ] 3c — a media kind the channel can't send renders as its caption, never silence
- [ ] 3d — retire the unused `supports_streaming` flag
- [ ] Verify: a card reaches Telegram as readable text; text-only sends byte-identical
- [ ] **Restart prod**; Telegram regression + a media notification

### Phase 4 — cross-channel context
- [ ] 4a — generalize the `telegram_`-prefixed filter to a channel-prefix set (`agent.py:239,257`)
- [ ] 4b — sibling-thread chat injection in `build_system_prompt` (upstream B1.5)
- [ ] 4c — settle open question 1 (context window across channels)
- [ ] Verify: heartbeat↔user awareness unchanged; prompts byte-identical with one channel
- [ ] **Restart prod**; confirm a heartbeat tick still sees today's chat

### Phase 5 — concurrent-turn safety (independent; parallelizable)
- [ ] 5a — a keyed lock serializing user turns against each other, heartbeat exempt
- [ ] 5b — verify a second user turn waits for the first; a heartbeat tick still runs concurrently
- [ ] **Restart prod**; two fast messages reply in order, tick unaffected

---

## Decisions

**Routing splits by who initiated, not by channel.**

| Traffic | Goes to | Examples |
|---|---|---|
| **Reactive** — a reply to something the owner sent | the **origin** channel | chat replies, confirmation prompts *and* their acknowledgements |
| **Proactive** — Jarvis speaking first | the **configured default** channel | heartbeat briefings, reminders, media notifications |

The default is configuration, not code: `JARVIS_DEFAULT_CHANNEL=telegram` during development,
flipped to `app` when the app is ready to be primary. One knob, one place.

**This diverges from upstream B3**, which specifies proactive **fan-out** — every heartbeat and
reminder reaching both channels. **We start default-only:** two devices buzzing for every tick
is noise, and during step 3 the app will be half-built, so proactive traffic should not go there
by default.

**Known accepted risk.** A single default is a single point of failure. Once the default is
`app`, a hub outage stops heartbeat briefings and reminders reaching the owner at all — and
because the heartbeat stamping rule only advances `state.json` on successful delivery, a long
outage means either a growing retry backlog or silently missed briefings. Telegram never had
this exposure. Mitigation is cheap and analysed in open question 2; it is deliberately not built
now, and the risk is near-zero while `telegram` remains the default.

**When can the default flip to `app`?** Two conditions, both temporary (app-author handover,
2026-07-20). Until then `telegram` stays the proactive default:

- *Delivery is already safe.* App proactive delivery is currently **foreground-only** — the hub
  *queues* a proactive message and delivers it when the app next opens; it does **not** drop.
  So this is a timeliness limit, not a data-loss one.
- *Timeliness is the gate.* A queued heartbeat digest read hours later is still useful; a queued
  reminder ("leave now") is worthless. So the flip is safe for **non-time-sensitive** events
  before push lands, and unsafe for **time-sensitive** ones (reminders) until it does. Push (FCM
  tickle) is on the Track A roadmap. This is the same per-event-type axis as open question 2.

**Confirmations still broadcast** (upstream B2, first-resolve-wins): the *prompt* goes to every
channel because the owner may be at any device, but the *acknowledgement* follows the channel
that answered. That is the reactive rule, and it fixes the asymmetry upstream flagged as a
follow-up.

---

## Phase 1 — channel-agnostic cleanup

No behavior change. Each slice is independently revertable, and none needs a second channel to
be worth doing.

**1a — Telegram HTML leaves the shared namespace.** `gateway/markdown_to_html.py` converts to
*Telegram-flavoured* HTML and is imported only by `TelegramChannel` (`channel.py:17`). It sits at
the gateway root, where a second channel would reasonably assume it is shared. Move it into
`gateway/channels/telegram/`. Note `_render.py:13-14` already mirrors its fence loop rather than
importing it, deliberately, to keep the channel self-owned (issue #48) — this move makes that
copy unnecessary; decide whether to reunify them or leave the duplication.

**1b — tool docstrings stop naming a channel.** `radarr.py:305` and `sonarr.py:312` say *"sends a
Telegram confirmation button"*. Unlike every other item here, tool docstrings are **prompt
content** — they are bound into the LLM's tool schemas, so Jarvis is currently told that
confirmations are a Telegram thing. Behavior is already channel-agnostic (`get_confirmation()`);
only the wording leaks. Reword to "sends a confirmation request".

**1c — comments and non-prompt docstrings.** `observability/usage.py:226`,
`gateway/commands/router.py:53`, `gateway/confirmation/base.py:50`, `store.py:7`. Cosmetic; batch
them.

**1d — the factory stops being Telegram-shaped.** `build_telegram_stack()` returns a
`TelegramStack` of concrete types and is called by name from `main.py:108`. Generalize to
`build_stack(name, ...)` returning a neutral `Stack`, with `build_telegram_stack` kept as a thin
wrapper so `main.py` changes in one line. The concrete Telegram imports stay in the factory —
that is its job — but the *shape* stops being one-channel.

*Verify:* Telegram regression; no diff in assembled prompts (1b changes tool schema text, so
expect exactly that diff and nothing else).

---

## Phase 2 — default channel + origin routing

Three singletons in `gateway/factory.py:51-53` assume one channel. They generalize differently.

| Singleton | Today | After |
|---|---|---|
| `_default_outbox` | the one Outbox | resolves through the configured default channel |
| `_confirmation` | the one store | per-channel store, origin-resolved in `get_confirmation()` (no broadcast) |
| `_default_channel` → `default_owner_thread_id()` | the one channel's thread | **origin-aware** — see below |

**2a — a channel registry.** Channels register by `Channel.name`; `JARVIS_DEFAULT_CHANNEL`
(default `telegram`) selects the proactive target. With one channel registered, every lookup
returns what it returns today.

**2b — proactive resolves through the default.** `default_outbox()` keeps its signature;
heartbeat, reminders and the media notifier need no changes, exactly as upstream B3 promised.

**2c — confirmations are origin-scoped (Design B: per-channel handlers).** A confirmation
belongs to the channel of the turn that raised it — prompt *and* ack. Today both are hardwired to
the single Telegram store + `default_outbox()`, so a request from a second channel would prompt
and acknowledge on Telegram instead. The fix keeps confirmation **on its own axis** (not fused
into the proactive `_registry`) and makes each channel own its confirmations:

- **Per-channel store.** Each channel builds its own confirmation store (its own `ConfirmationUI`
  + outbox + owner thread), registered by name in a `_confirmation_stores` map. Store internals are
  unchanged — each is just "one channel's confirmations."
- **Origin resolved once.** `get_confirmation()` (in the factory — it has both the turn context
  and the registry) reads a new `CURRENT_THREAD_ID` ambient var → origin channel name → returns
  that channel's store. Falls back to the default channel for origin-less turns (heartbeat).
  Destructive tools call `get_confirmation()` unchanged.
- **Tap + ack are naturally channel-local.** The button tap already returns to the origin
  channel's own store (each channel's host holds its store); the ack runs `ask_jarvis` on the
  store's own thread and sends via the store's own outbox. No `PendingAction` change, no
  cross-channel resolution, no resolver injected into the store.

*Why Design B over a central store that resolves per-request:* origin is resolved in one obvious
place (`get_confirmation`), the store's internals don't change, and it matches how OpenClaw
(`ChannelApprovalCapability`) and Hermes (per-adapter `send_*` methods) structure interactive UI.

**Scaling to blocks (buttons / forms / cards).** Confirmation is the first instance of a general
per-channel *interaction* handler. Future block types become `render_<block>` methods on the same
per-channel handler, each with a **text-fallback default in the base class**: a channel that can
render richly overrides, one that can't inherits text. The origin seam (`get_confirmation` → a
general `get_interaction`) is unchanged. Because block richness is **app-only** (Telegram has no
custom blocks), Telegram overrides only `confirmation` (native buttons) and inherits text
fallbacks for everything else — future block work is nearly all additive on the app side.
"Render, don't negotiate" keeps capability checks out of `tools/`/`agent.py`. We build the
confirmation template now; the block methods + the app's rich overrides arrive with Phase 3 +
Step 3, not before (no consumer exists yet).

*Verify:* with one channel, every path byte-identical. A confirmation round-trip acknowledges in
the same thread it was answered from.

---

## Phase 3 — rendering

**The model is render, not negotiate.** A sender emits one payload; each channel renders it as
best it can. There is no capability query, no branching at call sites, and nothing is "sent to a
channel that doesn't support it" — the payload was never channel-shaped to begin with. Telegram
renders a workout summary as text; the app renders the same summary as a card. Same information,
two representations.

This keeps channel knowledge out of `tools/` and `agent.py`, which is the standing architectural
rule, and it needs no capability model to do it.

**Scope is deliberately small.** The app is expected to reach parity with Telegram, so the gap
this handles is temporary. Building a capability-negotiation framework for a mismatch with a
known expiry date would be over-engineering. Rules, not machinery:

1. **Never error on a delivery path.** A mismatch is cosmetic; a raised exception turns it into a
   silent delivery failure, which is the failure mode this plan exists to prevent.
2. **Never silently drop.** Something always arrives, even if it is only text.
3. **Render, don't invent.** Fall back to prose that is already in the payload.

**The contract makes rule 3 achievable**, by design rather than luck:

> `card` and `form` carry their own prose (title, body, field labels). **`buttons` and
> `confirmation` carry none** — they are affordances only, so the prose that gives them meaning
> belongs in the message's `text`.

So `card`/`form` render to text from their own content, and dropping `buttons`/`confirmation`
leaves a coherent message *because the contract forbids putting meaning in the affordance*.
Nothing has to be synthesized.

**3a — the rich seam (upstream B4).** `OutboundReply{text, blocks}`; non-abstract
`Channel.send_rich` defaulting to `send(text)`, so Telegram is byte-identical until it overrides.

**3b — Telegram renders blocks as text.** Card/form prose becomes markdown;
buttons/confirmation drop (the message text already carries their meaning). One sharp edge
upstream already caught: an app-bound message may carry `blocks` with `text=None`, and Telegram
cannot send an empty message — the default must **skip** that send rather than emit blank.

**3c — media renders to its caption.** Telegram's outbound support is **`image` only**
(`channel.py:108,118` raise for anything else), while its *inbound* handling accepts image, video
and audio (`media_cache._EXT`). That asymmetry exists today, inside one channel, before any
second channel is involved — so this rule is testable immediately. An unsendable kind renders as
its caption, or a `[kind]` placeholder when there is none, reusing the convention `Outbox._log`
already applies at `outbox.py:115`.

**3d — retire `supports_streaming`.** Declared at `base.py:52`, **read nowhere.** It is the
vestige of a capability model that never landed; the render model does not need it. Delete rather
than leave a dead flag for a future reader to trust.

*Verify:* a card reaches Telegram as readable text; an unsendable media kind arrives as its
caption rather than silence; text-only sends byte-identical to today.

---

## Phase 4 — cross-channel context

Memory splits on a line that is already right:

| Layer | Scope |
|---|---|
| `SOUL.md`, `USER.md`, `MEMORY.md`, memory files | **Shared** — one Jarvis, one identity |
| `chat_history.jsonl` | shared file, tagged by `thread_id` |
| LangGraph checkpoint (50-message window) | **per-thread** |

Shared long-term memory, split short-term context. The bridge between threads is live injection
in `build_system_prompt`, and it is currently hardcoded to one prefix.

**4a — generalize the prefix.** `agent.py:239,257` filters `tid.startswith("telegram_")`.
Becomes a channel-prefix set. Harmless before a second channel exists, so it can ship early.

**4b — sibling-thread injection (upstream B1.5).** User-scope prompts additionally inject today's
chat from the *other* user thread, bounded by the same start-of-Israel-day window and per-entry
cap as the existing slices. Upstream promoted this out of the parked list for a good reason:
telegram↔app is the same person switching devices mid-conversation, so *"as I said a minute ago"*
fails on day one without it.

**4c — the durability gap** is open question 1 below, and it is not app-specific: the same
mechanism carries heartbeat↔user awareness today.

*Verify:* heartbeat↔user awareness unchanged; with one channel the assembled prompts are
byte-identical.

---

## Phase 5 — concurrent-turn safety

**Independent of phases 1–4 — parallelizable.** It touches neither routing, rendering, nor
context injection; it can proceed alongside them in any order.

A second user channel makes it possible for the same person to have two turns in flight at once
(`telegram_<id>` and `jarvis-app_<id>`). LangGraph thread state is isolated per thread, but the
surfaces a turn *writes* are not: memory files, `scheduled_events.json`, and the confirmation
store are shared, and `ask_jarvis` has no lock. Upstream accepted this for v1 when a second user
channel was hypothetical; step 3 makes it real, so it is made safe here — in the step that owns
multi-channel safety — rather than bolted onto the new channel.

**5a — a keyed lock, heartbeat exempt.** Serialize *user* turns against each other; leave
heartbeat concurrent. Not a global `ask_jarvis` lock — that would make a chat message wait behind
a 90s tick. The key is the point: user threads share one lock, heartbeat holds none.

**Worth doing before the app exists.** Two fast Telegram messages can already overlap on one
thread today, so the interleaved-write exposure is latent, not new — the second channel only
widens it. That is why this is placed as its own phase with real verification, not a footnote to
step 3.

*Verify:* a second user turn waits for the first to finish; a heartbeat tick still runs
concurrently with a user turn (the behavior that must **not** regress).

---

## Architecture review (2026-07-23)

Before building Phase 2's confirmation routing, the Channel-vs-Outbox split was checked
from first principles and against two comparable assistants — **OpenClaw**
(`openclaw/openclaw`) and **Hermes** (`NousResearch/hermes-agent`). Outcome:

- **The split is principled, not an artifact of the pre-existing Outbox.** It is the
  transport-adapter (port) vs. delivery-policy (application service) seam. OpenClaw splits it
  *more* aggressively (a channel-agnostic `infra/outbound/deliver.ts` core + thin per-channel
  outbound adapters); Hermes fuses send into the adapter for replies but adds a separate
  `DeliveryRouter` for proactive sends. Both put proactive and reactive through **one delivery
  seam, differing only in destination** — which validates `Outbox` as the single owner-send seam
  and replies going straight through the channel.
- **Confirmation stays on its own axis** (this **supersedes** the Decisions section's earlier
  "confirmations broadcast, first-resolve-wins" line). A confirmation lives entirely on the
  channel of the turn that raised it — prompt *and* ack. Interactive UI is per-channel,
  capability-degrading (plain-text fallback), origin-resolved, with a shared callback-id
  convention — **not** fused into the proactive `{channel, outbox}` registry. Both reference
  systems model it this way (Hermes's `send_clarify`/`send_exec_approval`/… family; OpenClaw's
  `ChannelApprovalCapability`), and both treat confirmation as one instance of a general
  interactive-callback pattern. We build confirmation as that template but do **not** build the
  general framework yet.

**Deferred cleanups (tracked, not blocking):**
- Loop-bridge (`bind_loop`/`submit`) is misfiled in `outbox.py` — it is shared sync→loop infra
  (the confirmation store uses it too), not owner-send policy → issue #43.
- `notify_owner`/`notify_owner_media` duplicate a `try/except → log` block; refactor to a send
  middleware seam when a 3rd cross-cutting concern (retry/rate-limit/fallback) lands → issue #44.
- The "owner" concept is baked into the `Channel` transport port (`send_to_owner`) — app policy
  leaking into transport. Low value for a single-user box; flag only, do not churn.
- `Channel` fuses inbound + outbound where OpenClaw splits them — revisit only if a send-only or
  receive-only channel is ever added.

**Do NOT adopt yet (over-engineering at N=2 channels):** plugin self-registration + lazy loading,
`DeliveryTarget`-style envelope-string routing, durable delivery queues / commit hooks, and
multi-account-per-channel. Noted as the growth path if the channel count ever climbs.

## Deltas — upstream plan vs. this codebase

`original_app_plan.md` predates the Outbox unification (PR #34, merged 2026-07-16). Verified
2026-07-20:

| Upstream says | Actually |
|---|---|
| B4: *"New `gateway/outbox.py`"* | **Exists.** The seam is designed around `Outbox`, not creating it |
| B3: *"Factory sets it as `default_user_channel`"* | No such accessor — `set_default_outbox()` / `default_outbox()` |
| B3: *"main.py passes it to `MediaNotificationManager` (main.py:126)"* | **Already done** — `main.py:118` passes `stack.outbox` |
| Flag 2: *"fixes the `main.py:102` hardcoded telegram ack-thread"* | Partly — `main.py:93` now calls `default_owner_thread_id()`; the origin-routing gap remains (phase 2c) |
| B2: `store.py:35/178/211` | Line numbers no longer match |
| B3: *"widen the `agent.py:257` filter"* | **Still exactly correct** (phase 4a) |

Two details land right by construction rather than needing design: `Outbox._log` runs once per
call, not per channel, so `notifications.jsonl` gets one row per proactive send — which matters,
since `agent.py` filters `event=="heartbeat"` to build the user-scope prompt slice. And
`SendOutcome` already carries delivery success, which the heartbeat stamping rule keys off.

---

## Open questions

1. **Cross-channel context is time-bounded, and so is heartbeat↔user.** Injection carries
   *today's* sibling chat; beyond that, continuity depends on the daily log or on something
   having been written to memory. Say it on Telegram Monday, ask in the app Wednesday, and it is
   gone. This is not new and not app-specific — the same limit governs what a heartbeat tick
   knows about yesterday's conversation. Worth improving on its own terms. Options: widen the
   window, promote a cross-thread summary into the prompt, or lean harder on memory writes.
   Interacts with `docs/plans/CONTEXT_HANDLING_PLAN.md` (the 63% is message history) — a wider
   window costs tokens.
2. **Fan-out for proactive sends.** We start default-only (see Decisions). Analysed here so the
   choice is revisitable on evidence rather than re-argued from scratch.

   **The routing axis already exists.** `Outbox.notify_owner()` takes an `event` parameter and
   the types are frozen: `EVENT_HEARTBEAT`, `EVENT_REMINDER`, `EVENT_MEDIA`, `EVENT_LLM_MEDIA`
   (`gateway/outbox.py:35-38`). So this is not a global on/off — policy can be per event type,
   and the outbox already has the event in hand at the decision point.

   **Three modes, one seam.** `FanoutChannel` is itself a `Channel`, so all three are the same
   plumbing; only the policy the outbox consults differs.

   | Mode | Behavior | Fits |
   |---|---|---|
   | **Default only** (today's choice) | Send to the configured channel; failure is failure | Heartbeat briefings — hourly, individually low-stakes |
   | **Fallback** | Try the default; on failure try the others | A reliability floor. Fixes the single-point-of-failure risk with zero happy-path noise |
   | **Fan-out** | Send to all | Anything genuinely must-not-miss — reminders |

   **The strongest case for moving off default-only is reliability, not reachability** — see the
   accepted risk in Decisions. Adding **fallback as the baseline** would close it while keeping
   the configured-default model intact and adding no duplicate messages.

   **The cost of true fan-out is stale duplicates.** There is no cross-channel dismissal for
   plain notifications: B2 solves this for confirmations (`edit_outcome` fans out so the losing
   channel's prompt updates), but a fanned-out reminder acted on in the app sits unread in
   Telegram forever. That argues for fan-out being opt-in per event type rather than the default.

   **Suggested shape when revisited:** fallback as the floor for all proactive sends; fan-out
   opt-in per event type, starting with `EVENT_REMINDER` only. `Outbox._log` runs once per call
   regardless of mode, so `notifications.jsonl` keeps one row per send and the
   `event=="heartbeat"` prompt slice is unaffected either way.

   **Decide when revisiting:** should fallback be an unconditional floor, or should some events
   genuinely fail rather than reroute? And is `EVENT_REMINDER` the only fan-out case, or do media
   notifications belong there too?
3. **`thread_id` format.** `gateway/base.py:24` freezes it at `telegram_<user_id>` for "Phase 1"
   and defers a `:` separator change to "Phase 2, coupled to the checkpointer-key migration".
   Adding `app_<id>` is the moment that deferral comes due — do it now while there is one
   channel's history to migrate, or keep the underscore and drop the note?
4. **`markdown_to_html` duplication.** `_render.py` mirrors its fence loop deliberately (issue
   #48). After phase 1a they are in the same package — reunify or keep the copy?
