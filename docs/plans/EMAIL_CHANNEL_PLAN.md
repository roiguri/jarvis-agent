# Email — send tool, dedicated-Gmail channel, and allow-listed correspondents

**Status:** planned, not started.
**Date:** 2026-07-31.
**Goal:** three staged capabilities, in order: (1) let Jarvis pick a *registered* channel (today:
Telegram or the app) to send to, independent of which channel the current turn came in on — no
email involved; (2) a full two-way email channel backed by Jarvis's own dedicated Gmail account;
(3) allow-listed third-party correspondents who can receive and reply to Jarvis, under a new
security-scope model.

---

## Context

Arrived here via a same-day discussion: researched OpenClaw's own email integration (a narrow,
pull-based `himalaya` CLI skill — full IMAP read/search/reply capability, but the agent has to
decide to check; their one attempt at a real bidirectional channel plugin, `agentmail`, is
unmerged and even it stays deliberately reply-only). From there: a narrower "reply to what Jarvis
sent" idea (cheap, correlation-based, owner-only) — superseded once the owner said they'd rather
give Jarvis its **own dedicated Gmail account**, which is actually the more architecturally honest
version: Telegram isn't "Jarvis reading the owner's Telegram," it's Jarvis as its own bot identity;
a dedicated Gmail account is the same pattern applied to email, not a special case. From there, the
owner raised wanting this to eventually extend to allow-listed third parties, with real security
scoping — the most open-ended of the three.

**Today's baseline, relevant to all three phases:** Jarvis is single-owner throughout.
`Channel.authorize()` compares against one configured owner id; `owner_thread_id` is a single fixed
property per channel; a tool's `scopes=("user","heartbeat")` governs *when* a tool is available
(a user turn vs. a heartbeat tick), never *who* is asking. Phase 3 is the first time "who" needs to
matter, and nothing in the current model answers that yet.

---

## Decisions carried in from discussion

- **Phase 1 touches only existing channels — no Gmail, no new credentials.** Cross-channel send
  among Telegram/`jarvis-app` is independent of email entirely; keeping them separate avoids
  coupling a small, self-contained tool to the much larger Phase 2 build.
- **Outbound-to-anyone (email, Phase 2+) is a tool, not a channel property.** `send_to_owner`
  assumes one fixed recipient; emailing an arbitrary address needs the recipient supplied
  per-call, so it's a plain tool (`send_email(to, subject, body)`-shaped), not part of the
  `Channel` ABC.
- **A dedicated Gmail account, not the owner's personal inbox.** Mirrors Telegram/the app
  channel's own-identity pattern.
- **Gmail specifically unlocks real push.** The Gmail API's `watch()` + Cloud Pub/Sub notifies on
  new mail — no IMAP IDLE/polling loop to get wrong, which was the actual cost driver behind
  hesitating on a generic email channel earlier in the discussion. (Still to be weighed against a
  *pull*-based Pub/Sub subscription — see the webhook discussion in Phase 2.)
- **Third-party outbound needs a confirmation gate**, at least initially — it's the first
  capability that reaches someone outside the owner relationship without them asking for it in the
  moment.
- **Whether destructive tools / `ConfirmationUI` should be reachable via email at all is
  unresolved — flagged in Phase 2, not decided.**
- **Phase 3 is a real design effort, not just implementation** — flagged throughout, not detailed
  to the same depth as phases 1–2.

---

## Phase 1 — cross-channel send tool (existing channels only, no email)

**Goal:** the agent can decide, mid-turn, to send to a *different registered channel* than the one
the current turn came in on (e.g. "let me also/instead notify you on the app"). Scoped strictly to
channels that already exist today (Telegram, `jarvis-app`) — no Gmail, no new credentials, no new
channel work in this phase.

- New accessor in `gateway/factory.py`, e.g. `outbox_for_channel(name: str) -> Outbox` — looks up
  `_registry[name].outbox`, raising with the registered names on a miss (same style as
  `get_confirmation()`'s error). Today's registry only exposes a channel's outbox two ways:
  `default_outbox()` (the configured default) or by being a turn's own origin — there's no
  "give me channel X's outbox by name" accessor yet.
- New tool, e.g. `send_message_to_channel(channel, text)` in `tools/core/`. Validates `channel`
  against whatever's actually registered at call time (`sorted(_registry)`), not a hardcoded
  `("telegram", "jarvis-app")` tuple, so a future channel needs no change here.
- No `destructive=True`/confirmation — this isn't irreversible in the sense destructive tools are,
  and it only ever reaches the owner (every channel today is owner-addressed).
- **History handling, resolved by the OpenClaw research this session:** do **not** try to write a
  synthetic assistant-turn into the destination channel's own conversation/thread state — OpenClaw
  deliberately doesn't (`source-reply-mirror.ts` only mirrors same-conversation sends). Route the
  send through `outbox.notify_owner(text, event=...)`, the same path reminders/heartbeat
  notifications already use.
- **Correction (independent review, post-write): the "existing cross-scope log-injection" this
  leans on does not actually cover telegram↔app today — it needs to be built as part of Phase 1,
  not assumed.** Verified: `agent.py`'s injection (`_load_recent_heartbeat_notifications` /
  its call site) is hardcoded to `event == "heartbeat"` and gets injected into **every** user-scope
  turn unconditionally, with no per-channel/destination filtering at all. Reusing `event="heartbeat"`
  for a cross-channel send would broadcast it into every channel's next turn, not just the
  destination's — wrong. Phase 1 needs its own, channel-scoped filter (parallel to the heartbeat
  one, not a reuse of it) so a send tagged for channel X only surfaces in channel X's next turn.
  Scope this explicitly as Phase 1 work, not borrowed infrastructure.

**Verification:** from a Telegram turn, ask Jarvis to send something to the app instead; confirm it
arrives there, surfaces correctly in the app's *next* turn (and only that channel's), and the
Telegram turn's own reply behaves normally (no double-send, no crash if the target channel is
unreachable).

---

## Phase 2 — dedicated-Gmail email channel (full two-way, owner only)

**Goal:** a real `Channel` implementation; inbound mail triggers a turn the same way a Telegram or
app message does.

- New `gateway/channels/email/` package: `client.py` (Gmail API wrapper — send, `watch()`/Pub/Sub
  subscribe, fetch), `channel.py` (`EmailChannel(Channel)`), an inbound handler for the Pub/Sub
  push route.
- `owner_thread_id = f"email_{owner_id}"`, matching the existing `<channel>_<id>` convention.
- Reuses `Outbox` / `InMemoryConfirmationStore` / `ConfirmationUI` — this is "add a channel,"
  which `GATEWAY.md`'s own checklist already anticipates (and already uses email as its worked
  example).
- **Correction (independent review, post-write): "mirrors `jarvis_app`'s shape, no gateway-level
  changes" is wrong for the inbound side — a push channel needs a genuinely different `Stack`
  shape, and real changes outside `gateway/channels/`.** Verified: `TelegramStack`/`JarvisAppStack`
  each own an asyncio task or PTB lifecycle they `start()`/`stop()` themselves — that's the whole
  point of the `Stack` protocol. A Pub/Sub-push email router has no task of its own; it's invoked
  by an HTTP request inside a FastAPI app someone else already starts. Worse, `create_webhook_app`
  (`gateway/webhook/server.py`) hardcodes its routes inline with no `APIRouter`/mount mechanism
  today — wiring in an email route genuinely requires changing that function's signature and
  `main.py`'s call site. This is real gateway/host-layer work, not just a new package under
  `gateway/channels/`; plan for it explicitly (an `EmailStack` whose `start()` is a no-op or just
  registers a route, plus a `create_webhook_app` signature change to accept additional routers).
- **Open, not decided: should destructive tools / `ConfirmationUI` be reachable from email at
  all?** Email is a lower-trust surface than Telegram/the app (spoofable From headers, a wider
  window for account compromise, no device-level confirmation the way a phone tap has).
  **Correction (independent review): deferring `EmailConfirmationUI` is not a neutral placeholder —
  it currently means the *permissive* outcome, not the safe one.** Both existing channel builders
  always construct a `ConfirmationUI` and call `register_confirmation` together — there is no
  "channel with no confirmation store" case today. Skip `EmailConfirmationUI` and
  `register_confirmation("email", ...)` never happens, so `get_confirmation()` silently falls back
  to the *default* channel (e.g. Telegram) for an email-origin turn — a destructive-tool request
  from a spoofed or compromised email would still prompt the owner, just over Telegram instead of
  being refused. That's backwards for a surface already called out as lower-trust. **The actual
  decision needed before Phase 2 ships not just before the UI is built: should email-origin turns
  have destructive tools refused outright** (e.g. a scope check tools already do via
  `turn_context.current_scope()`, extended to check the origin channel) **rather than silently
  falling back to another channel's confirmation.** Leaning refuse-outright is the safe default;
  don't ship the fallback-to-Telegram behavior by default while this is unresolved.
- `build_email_stack()` in `gateway/factory.py`, gated by an `EMAIL_*` env var the same way
  `APP_HUB_URL` gates the app channel today — inert by default, additive.
- **Webhook hosting — decided: push, piggybacking the existing `gateway/webhook/` FastAPI server.**
  Reconsidered pull (matches Telegram/app's existing poll-based pattern, avoids a new auth
  mechanism, avoids coupling to `WEBHOOK_ENABLED`) against push, and the deciding factor is traffic
  volume: this channel won't be busy, so a persistent poll loop's overhead is paid mostly for
  nothing, while a webhook costs nothing when idle and only fires on real activity. The server is
  confirmed already internet-reachable, so piggybacking doesn't newly expose anything.
  Consciously accepted trade-offs from this choice, to actually handle during implementation, not
  hand-wave past:
  - A new Pub/Sub push route must validate the request is genuinely from Google (verify the signed
    OIDC token — audience, issuer) — a new, security-sensitive code path with no existing analog
    in this codebase.
  - Email's inbound now depends on `WEBHOOK_ENABLED` being on wherever it runs. Staging currently
    runs *without* the webhook subsystem by default — enabling email-channel testing on staging
    means also flipping `WEBHOOK_ENABLED` there, a real coupling to keep in mind, not a surprise
    later.
  - Mount as its own router (e.g. `gateway/channels/email/webhook.py`), not folded into the
    existing `notifier.py` media-notification code — same process/port, cleanly separate concern.
- **Open engineering question, to resolve during design, not before:** how `watch()` renewal
  (Gmail watches expire on the order of a week) gets re-armed — most likely a heartbeat-tick job,
  matching how other periodic upkeep already rides the heartbeat.

**Verification:** email the dedicated address from the owner's personal account; confirm a turn
runs and a reply arrives, correctly threaded.

---

## Phase 3 — allow-listed correspondents (new security-scope axis)

**Goal:** specific other people can message Jarvis (most likely via the email channel, possibly
others later) and get replies, under a restricted permission set.

This phase needs its own design pass when it's actually started — not detailed further here,
beyond naming the axis so phases 1–2 don't accidentally paint it into a corner:

- **Identity threading.** `Channel.authorize()` returns a bool against one configured owner today;
  it (or the router) would need to identify *which* allow-listed correspondent this is, not just
  "authorized: yes/no."
- **A new, orthogonal scope axis — not a repurposing of the existing one.** `scopes=("user",
  "heartbeat")` means *when*; *who* needs its own dimension — e.g. a tool declares `owner_only`
  (the implicit default every tool has today) vs. `guest_safe`, rather than overloading the
  existing tuple.
- **Per-correspondent threads.** Thread ids would need to carry the correspondent's own identity
  (`email_<their-address>`), not just the channel — a real generalization of today's
  single-owner-per-channel thread model.
- **What "guest_safe" means, concretely.** Almost certainly an explicit narrow allow-list of tools
  (no destructive tools, no memory tools, no heartbeat-authoring), not a deny-list — the cost of
  getting this wrong is a stranger acting on the owner's behalf.
- **Prompt assembly implications.** `SOUL.md`/`USER.md` currently assume the reader is the owner;
  a guest turn needs its own reduced framing so private context doesn't leak to an allow-listed
  correspondent.

Given the size of this relative to phases 1–2, and that it depends on a real channel already
existing, this is its own planning effort once Phase 2 has shipped and is stable — the same
"design when a real consumer exists" call already made twice this session (Stage D, the generic
confirmation interface).

---

## Sequencing

Phase 1 (independent, no email) → Phase 2 (stands up the Gmail credential + channel from scratch)
→ Phase 3 (depends on Phase 2 existing; a separate design effort, not scoped in detail yet).

## Open questions

- **Phase 1:** none outstanding — no confirmation gate needed, scope is self-contained.
- **Phase 2:** webhook hosting — **settled** (push, piggyback the existing webhook server; see
  Phase 2 for the accepted trade-offs). Still open: whether destructive tools/`ConfirmationUI`
  should be reachable via email at all — explicitly deferred until discussed; do not build
  `EmailConfirmationUI` before this is settled.
- **Phase 3:** not ready to answer yet — needs its own scoping conversation when it's time.
