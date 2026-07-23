# App channel — execution plan

**Status:** planning, uncommitted. **Date:** 2026-07-23.
**Single source of truth.** Absorbs and replaces the former `APP_CHANNEL_PLAN.md` (index),
`02_MULTI_CHANNEL_SUPPORT.md`, and `03_APP_CHANNEL.md` (now in `archive/`). The only other live
material is the app author's pinned handover under `jarvis-app/` — **imported verbatim, not ours
to edit.**

**Goal:** ship the custom app as a second channel beside Telegram, without the Telegram loop — the
owner's day-to-day assistant — ever being the test surface. Work is sequenced by **real
dependency**, not by whether it touches existing or new code.

---

## Checklist

**Landed** (on `main`)
- [x] Staging environment + deploy discipline — `deploy.sh`/`rollback.sh` (archived `../archive/STAGING_AND_DEPLOY.md`)
- [x] Generic `build_stack(name)` factory + neutral `Stack` protocol (PR #42)
- [x] Channel registry + `default_outbox()`/`default_owner_thread_id()` resolve through it (PR #45)
- [x] Per-channel origin-scoped confirmations — prompt + ack on origin, no broadcast (PR #45)
- [x] Channel-agnosticism CI gate — path-isolation + `channel-agnostic`, required on `main` (PR #46)

**Stage A — free cleanups** (no deps; byte-identical / dead-code)
- [ ] A1 — delete the dead `supports_streaming` flag (`base.py:52`)
- [ ] A2 — generalize the `telegram_` thread-prefix filter (`agent.py:262`) to a channel-prefix set
- [ ] A2 — extend the CI channel-agnostic gate to cover `agent.py` (currently exempted)
- [ ] **Restart staging** + Telegram regression (GATEWAY.md step 9)

**Stage B — concurrent-turn safety** (before any second channel is live)
- [ ] B-lock — keyed lock serializing *user* turns; heartbeat exempt
- [ ] **Restart** + verify: a second user turn waits for the first; a heartbeat tick still runs concurrently

**Stage C — app channel, text round-trip** (the deliverable)
- [ ] **Owner:** `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID` in `staging.env` (staging-specific hub bot token)
- [ ] Re-validate every agent-internal reference against current code + `jarvis-app/contract.md` before B1
- [ ] B0 — `docs/architecture/APP_CHANNEL.md` (adapter map, degraded mode, pin `f1633277132cbedf`) + GATEWAY.md channel tables
- [ ] B1 — `client.py` (`HubClient`) · `channel.py` (`JarvisAppChannel`, text-only sends) · `router.py` (fetch loop + single consumer)
- [ ] B1 — hub-down: log once, back off 1→60s; SIGTERM drain (shared with #33)
- [ ] B1 — `build_jarvis_app_stack()` in factory; `main.py` builds it **only if `APP_HUB_URL` is set**
- [ ] **Start staging with `APP_HUB_URL` set**; curl the hub as "the app", confirm a reply + a `jarvis-app_<owner>` row in `chat_history.jsonl`
- [ ] **Restart staging** + Telegram regression

**Stage D — rich rendering / blocks** (design deliberately OPEN — decide when a consumer exists)
- [ ] Design the outbound seam against the real app renderer (`OutboundReply` is *one candidate*, not committed)
- [ ] 3c — media→caption fallback (fold in with the seam it hardens)
- [ ] 4b — sibling-thread chat injection (needs a second live user thread)
- [ ] 4c — durable cross-channel context (open question — not app-specific)
- [ ] Upstream deferrals: B1.6 media inbound · B2 app `AppConfirmationUI` · B5 chips · B6 apps

---

## Why this ordering

Steps 2 and 3 were split by *what code is touched* — existing gateway/agent code vs. the new
channel package. That is a good conceptual map and a misleading run order: taken linearly it
front-loads scaffolding (the render seam, sibling-thread context, the concurrency lock) **ahead of
the app channel that would give that scaffolding a consumer to verify against.** Several of those
slices have no live consumer until the phone grows a capability it does not have yet, so building
them first means writing code whose "verify" step cannot run.

Two dependencies the old step docs asserted **do not hold on inspection** (verified against current
code 2026-07-23):

1. **The render seam does *not* gate B1.** B1 is text-only: `JarvisAppChannel.send(chat_id, text)`
   implements the *existing* abstract `send(str)`, and the reply rides the existing
   `on_message → return str → channel.send(str)` path. B1 adds **zero new send call-sites**, so the
   "write against the seam to avoid a second edit at every send site" concern is empty.
   `OutboundReply` / `send_rich` matter only when something *emits blocks* — upstream B4, deferred
   behind the phone renderer regardless.
2. **The concurrency lock gates the *prod flip*, not building B1.** B1's own poll loop (single
   consumer, one turn at a time) prevents user↔user overlap *within* the app channel. The lock
   matters for **cross-channel** overlap — a Telegram turn and an app turn at the same instant —
   which is unreachable while B1 is built and validated in staging (one channel at a time), and
   becomes reachable only when both channels serve the owner concurrently in prod.

So **the app channel (B0/B1) is the next real deliverable**, with one safety item (Stage B)
deliberately pulled in front of it.

```
A. free cleanups ─► B. concurrency lock ─► C. app channel (B0/B1) ─► D. rich rendering (open)
   (no deps)           (before any 2nd         (the deliverable)        (design when a real
                        channel is live)                                 consumer exists)
```

---

## Stage A — free cleanups (no dependencies)

Byte-identical or dead-code removal; worth doing regardless of whether the app ever ships.

**A1 — delete `supports_streaming`.** Declared at `base.py:52`, **read nowhere.** Vestige of a
capability model that never landed; the render model does not need it. Delete rather than leave a
dead flag a future reader might trust.

**A2 — generalize the channel-prefix filter.** `agent.py:262` filters `tid.startswith("telegram_")`
(the live cross-scope awareness injection). Generalize to a channel-prefix set — harmless before a
second channel exists, byte-identical with one. This also **unblocks the CI channel-agnostic gate
to cover `agent.py`**, which the gate currently *exempts* precisely because this hardcoded channel
name still lives in domain code.

*Verify:* assembled prompts byte-identical with one channel; CI gate tightened to include
`agent.py`. **Restart staging** + Telegram regression.

---

## Stage B — concurrent-turn safety (the keyed lock)

A keyed lock serializes **user** turns against each other; **heartbeat stays exempt** — not a
global `ask_jarvis` lock, which would make a chat message wait behind a ~90s tick. User threads
share one lock; heartbeat holds none.

**Placed before the app channel on purpose.** Landing it while only one channel exists means B1
arrives into an already-safe world — there is never a window in which two live channels share the
unlocked write surfaces (`scheduled_events.json`, memory files, the confirmation store) with no
lock. Cheaper to land the safety property first than to land the channel and the property together.

**Honest note on present-day exposure** (verified 2026-07-23, so the rationale is not oversold):
with a single Telegram channel the overlap is *mostly latent*. PTB runs with `concurrent_updates`
off (`host.py:56`) and serializes its update queue, so two fast text messages do **not** overlap
despite `ask_jarvis` running in a worker thread (`main.py:64`). The only real single-channel
overlap paths today are the media-group flush detach (`router.py:91`) and heartbeat↔user — and the
lock leaves heartbeat exempt by design. So Stage B's real payoff is **cross-channel**; doing it
first is a safety-ordering choice, not a fix for a bug firing today. The interleaved-write exposure
is latent, not new — the second channel widens it.

*Verify:* a second user turn waits for the first to finish; a heartbeat tick still runs
concurrently with a user turn (the behavior that must **not** regress). **Restart** + two fast
messages reply in order, tick unaffected.

---

## Stage C — the app channel, text round-trip (B0 + B1)

The deliverable. Prereqs (generic factory, channel registry) are merged; the lock (Stage B) is in
place; the render seam is **not needed** (text-only).

**Channel identity.** `Channel.name = "jarvis-app"`; thread ids `jarvis-app_<owner>`. The Python
package is `gateway/channels/jarvis_app/` (packages cannot contain a hyphen — the one place dir and
name differ). Env vars keep the handover names (`APP_HUB_URL`, `APP_HUB_BOT_TOKEN`,
`APP_OWNER_USER_ID`) unless we prefix them `JARVIS_APP_*` — small open choice below.

**Scope is B0 + B1 only.** The hub validates blocks, chips, attachments and apps, but the phone
renders none of them yet; building those adapters now means code unexercisable when written.

**B0 — contract pin + doc.** New `docs/architecture/APP_CHANNEL.md`: how the adapter maps the bot
API onto the `Channel` ABC, the degraded-mode contract, and the pinned
`contract_version = f1633277132cbedf` (the hub reports it on `GET /v1/health`, so `HubClient`
**warns**, never hard-fails, on mismatch — the hub already 422s a bad payload, so this only gives a
*silent* skew a voice). Update GATEWAY.md's channel tables to list the app beside Telegram.

**B1 — text round-trip.** New `gateway/channels/jarvis_app/`, three modules:

- **`client.py` — `HubClient` (httpx).** `get_updates(offset, timeout)`, `send_message(body)`,
  `set_commands(...)`; raises `HubUnavailable` on 5xx so the router can enter degraded mode.
  Bearer-token auth from `APP_HUB_BOT_TOKEN`. The wider surface (attachments, events, PATCH) waits
  for the phases that use it.
- **`channel.py` — `JarvisAppChannel(Channel)`.** `name = "jarvis-app"`,
  `owner_thread_id = f"jarvis-app_{owner}"`. Implements the abstract **string/bytes** sends by
  POSTing to the hub. **No `OutboundReply` here** — its rich-render story is Stage D.
- **`router.py` — the poll loop.** The load-bearing part; shape is not negotiable —
  `jarvis-app/fake_agent.py:366-417` is the working reference:
  - **Two tasks over a queue.** A `_fetch_loop` that long-polls and, per update, advances the
    offset (`offset = update_id + 1`) and enqueues **before** any turn runs; and a single
    `_consume_loop` that runs turns one at a time. The re-poll is what acks the previous batch, so
    it must go out *while* a turn is still running — a serial `poll → turn → poll` collapses the
    app's ✓✓ into the reply and hides a class of concurrency bug.
  - **One consumer, not a task per update** — task-per-update would overlap same-user turns, the
    exact failure mode Stage B locks against.
  - **Fetch errors don't kill the fetcher** — a dropped poll logs, backs off, continues.
  - **Drain on SIGTERM** — cancel the fetcher (anything fetched is already queued — the ack was the
    poll), `queue.join()` to finish in-flight turns, then exit. Same graceful-shutdown work as #33.

Each inbound update becomes an `InboundMessage(thread_id=f"jarvis-app_{owner}")` and flows through
the existing shared `on_message` — slash commands, history logging, and confirmations work with no
app-specific code (already channel-agnostic).

**Degraded mode.** Hub unreachable → log **once** (not per failed poll) and back off 1→60s forever.
Telegram and heartbeat unaffected; the agent never crashes on a missing hub. This is what makes B1
safe to carry in prod behind the `APP_HUB_URL` gate before the channel is "done".

**Wiring.** `build_jarvis_app_stack()` joins the factory beside the Telegram builder (both thin
wrappers over `build_stack`). `main.py` builds the app stack **only when `APP_HUB_URL` is set** and
starts its router with `create_task(router.run())` — so prod, with the var unset, constructs
nothing and is byte-identical to today.

**Owner prereq.** Add `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID` to `staging.env`,
with a **staging-specific hub bot token** — the hub is one-bot-one-user, so once the channel is
live in prod a staging agent polling the same hub would fight over updates.

*Verify (staging):* with `APP_HUB_URL` pointed at the hub, curl the hub's client API as "the app";
confirm the live agent long-polls, replies, and writes a `jarvis-app_<owner>` row to
`chat_history.jsonl` — a phone-less end-to-end test. Then the Telegram regression, unchanged.

---

## Stage D — rich rendering / blocks (design deliberately OPEN)

**Not pre-designed.** When the phone can render blocks (upstream B4), the outbound seam gets
designed *then*, against the real contract and the app's real rendering support — that is when a
consumer first exists to judge the design against.

The **principle** stands and is not open: *render, don't negotiate.* A sender emits one payload;
each channel renders it as best it can, with a text fallback so **no delivery path errors or
silently drops** — a mismatch is cosmetic, never a silent failure. The contract makes the fallback
achievable: `card`/`form` blocks carry their own prose, while `buttons`/`confirmation` carry none
(they are affordances only — their meaning lives in the message `text`), so dropping an affordance
still leaves a coherent message.

The **mechanism** is open. `OutboundReply{text, blocks}` + a non-abstract `Channel.send_rich`
defaulting to `send(text)` is **one candidate** — not a committed choice. Decide it at Stage D.

Everything whose only consumer is a phone capability lands here, on the same "has a real consumer
now" gate:

- **3c — media→caption fallback.** Today no outbound path sends a non-image kind
  (`notifier.py:292` passes `"image"`), so the `NotImplementedError` in `channel.py:108,118` is
  unreachable; it becomes real when the app or a media feature first emits another kind. An
  unsendable kind should render as its caption (or a `[kind]` placeholder — the convention
  `Outbox._log` already uses at `outbox.py:115`). Fold in with the seam it hardens.
- **4b — sibling-thread chat injection.** User-scope prompts additionally inject today's chat from
  the *other* user thread (bounded by the same start-of-Israel-day window and per-entry cap as the
  existing slices). Needs a second *live* user thread to be meaningful — telegram↔app is the same
  person switching devices mid-conversation, so *"as I said a minute ago"* fails without it.
- **4c — durable cross-channel context** (open question 1). Injection carries only *today's*
  sibling chat; beyond that, continuity depends on the daily log or a memory write. Not
  app-specific — the same time-bound governs heartbeat↔user today — so treat it on its own terms,
  alongside `../CONTEXT_HANDLING_PLAN.md` (a wider window costs tokens).
- **Upstream honest-boundary deferrals:** B1.6 media inbound (+ app `media_cache.py`), B2 app
  `AppConfirmationUI`, B5 streaming chips, B6 apps. Each is real and specified in
  `jarvis-app/original_app_plan.md`; none is end-to-end testable today.

*Verify:* per slice, when each acquires its consumer.

---

## Decisions (carried forward, unchanged by the reorder)

**Routing splits by who initiated, not by channel.**

| Traffic | Goes to |
|---|---|
| **Reactive** — a reply to something the owner sent (chat replies, confirmation prompts *and* acks) | the **origin** channel |
| **Proactive** — Jarvis speaking first (heartbeat briefings, reminders, media notifications) | the **configured default** channel |

The default is configuration: `JARVIS_DEFAULT_CHANNEL=telegram` during development, flipped to
`app` only once push notifications land (a queued reminder read hours later is worthless; a queued
digest is still useful — the flip is safe for non-time-sensitive events before push, unsafe for
reminders until it). **We start default-only, not fan-out** — two devices buzzing per tick is
noise, and during Stage C the app is half-built.

**Confirmation stays on its own axis** — origin-scoped prompt *and* ack, no broadcast (this
supersedes the upstream B2 "first-resolve-wins fan-out" line). Already implemented (per-channel
store, origin resolved in `get_confirmation()` via `CURRENT_THREAD_ID`). It is the first instance
of a general per-channel *interaction* handler: future block types become `render_<block>` methods
with a **text-fallback default in the base class**, rich overrides app-only. We build the
confirmation template; the general framework is not built at N=2 channels.

**`thread_id` keeps the underscore.** `jarvis-app_<id>` parses fine beside `telegram_<id>` (prefix
is everything before the first `_`; neither channel name contains a `_`). Migrating to a `:`
separator would rewrite live conversation-state keys in `threads.sqlite` for a hypothetical
collision — don't.

**Proactive reliability (deferred).** A single default is a single point of failure; once the
default is `app`, a hub outage stops briefings and reminders reaching the owner, and the heartbeat
stamping rule (advance `state.json` only on successful delivery) turns a long outage into a growing
retry backlog. The routing axis already exists — `Outbox.notify_owner()` takes a frozen `event`
type — so policy can be per-event without a global switch. **Suggested shape when revisited:**
*fallback* (try default, then others) as the floor for all proactive sends; true *fan-out* opt-in
per event type, starting `EVENT_REMINDER` only. `Outbox._log` runs once per call regardless, so
`notifications.jsonl` keeps one row per send and the `event=="heartbeat"` prompt slice is
unaffected. Near-zero risk while `telegram` is the default; deliberately not built now.

**Architecture review (2026-07-23).** The Channel(transport) vs. Outbox(delivery-policy) split was
checked from first principles and against two comparable assistants — **OpenClaw**
(`openclaw/openclaw`) and **Hermes** (`NousResearch/hermes-agent`). Findings: the split is
principled (transport-adapter port vs. delivery-policy service), both references put proactive and
reactive through **one delivery seam differing only in destination** (validates `Outbox` as the
single owner-send seam), and both model confirmation as per-channel interactive UI, **not** fused
into the proactive registry. **Do NOT adopt yet** (over-engineering at N=2): plugin
self-registration, `DeliveryTarget` envelope routing, durable delivery queues, multi-account
channels. **Deferred cleanups** (tracked, not blocking): loop-bridge misfiled in `outbox.py`
(#43); `notify_owner`/`notify_owner_media` send-middleware seam (#44).

---

## Sources — the app author's handover (`jarvis-app/`, verbatim, not ours to edit)

| File | What it is |
|---|---|
| `contract.md` | The wire contract, **generated from the hub's Pydantic models** — the single source of truth for payload *shape*. Pinned at `contract_version = f1633277132cbedf`, which the live hub reports on `GET /v1/health`. B0 records the pin; `HubClient` warns (not hard-fail) on mismatch |
| `fake_agent.py` | A fake **agent** (not a fake hub) — it long-polls a *running* hub. Its value is as the reference poll loop B1 must write (`:366-417`) |
| `original_app_plan.md` | The approved Track B plan (2026-07-12), phases B0–B6. Upstream's *capability* sequencing |

**The honest boundary — don't build ahead of the phone.** The hub validates more than the phone
renders: `blocks` have no renderer or action path (B2/B4 wait), chips fan out with no consumer (B5
waits), no attachment/apps endpoints yet (B1.6/B6 wait). **Buildable and verifiable now: B0, B1**
(this plan's Stage C), plus B1.5/B3 which are folded into the landed multi-channel work and Stage D.

**Re-validation duty.** The handover cannot see this repo, so it asks that every agent-internal
reference in `original_app_plan.md` (`store.py:211`, `main.py:102`, the `Channel` ABC, `ask_jarvis`,
line numbers) be re-checked against current code before B1. Known deltas (verified 2026-07-20,
`original_app_plan.md` predates the Outbox unification / PR #34):

| Upstream says | Actually |
|---|---|
| B4: *"New `gateway/outbox.py`"* | **Exists** — the seam is designed around `Outbox`, not creating it |
| B3: *"Factory sets `default_user_channel`"* | No such accessor — `set_default_outbox()` / `default_outbox()` |
| B3: *"main.py passes it to `MediaNotificationManager`"* | **Already done** (`main.py:118` passes `stack.outbox`) |
| B2: `store.py:35/178/211` | Line numbers no longer match |
| B3: *"widen the `agent.py:257` filter"* | **Still correct** (Stage A2) |

---

## House rules & cross-cutting

**House rules.** Every stage ends with: the owner restarts the affected service, watch
`journalctl`, send a real message. After each stage touching the gateway, run the Telegram
regression (GATEWAY.md step 9) and the repo `code-review` skill. Source comments stay
behavior-only — plan context goes in commit messages. Nothing commits without approval.

**Isolation has a ceiling.** Staging isolates every byte Jarvis owns, not what Jarvis *reaches* —
Radarr, Sonarr, Jellyseerr, Arbox and web search are live from any instance, and
`fetch_upcoming_arbox_classes` upserts/purges rather than reads. Closing that gap is tool-layer
work, tracked nowhere yet.

---

## Open choices (decide at the stage that forces them)

| Choice | Decide by |
|---|---|
| Env prefix `APP_*` vs `JARVIS_APP_*` | Stage C — before the names are written into `staging.env` (avoid renaming twice) |
| Queue-epoch hub signal | Raise with the app author. Hazard: if the hub's queue is wiped and re-sequenced while the agent holds a higher offset, the next poll acks updates it never fetched → ✓✓ with no reply. Agent-side detection is **provably unreliable** (the ack is implicit in `GET /updates?offset=N`). Needs a hub-side queue epoch. Until then: **restart the agent alongside any hub wipe** |
| Blocks mechanism (`OutboundReply` or otherwise) | Stage D — kept open by this plan |
