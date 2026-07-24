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

**Stage A — free cleanups** (no deps; byte-identical / dead-code) — ✅ merged PR #47
- [x] A1 — deleted the dead `supports_streaming` flag (`base.py`)
- [x] A2 — the heartbeat chat filter (`agent.py`) now excludes the heartbeat thread *by identity* (`HEARTBEAT_THREAD_ID`) instead of a hardcoded `telegram_` prefix — chose the exclude-list over a registry allow-list as it states the real intent
- [x] A2 — CI gate: added check #4 (no channel name in `agent.py`); also flipped check #1 allow-list → deny-list so a new module can't silently escape
- [ ] Restart staging + Telegram regression — *not run; changes byte-identical/tooling-only, so this is regression smoke only*

**Stage B — shared-state write safety** (resource-level; heartbeat covered, not exempt) — ✅ PR #49
- [x] Audit: memory (`_WRITE_LOCK`) + confirmation store (`_lock`) already resource-locked; `scheduled_events.json` the lone gap
- [x] B-lock — `threading.Lock` around the `scheduled_events.json` read-modify-write (whole load→modify→save), covering user turns + heartbeat + future channels. Differential test: lock-free loses 280/400 updates, locked 0
- [x] Follow-up filed: unify the three ad-hoc locks behind one store-writer primitive (issue #48)
- [ ] Restart staging + a reminder round-trip — *regression smoke only (the lock is a no-op single-threaded); not run*

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
front-loads scaffolding (the render seam, sibling-thread context, the write-safety work) **ahead of
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
2. **Write safety is a small resource-level fix, not a turn lock that gates B1.** The original
   plan imagined a keyed lock serializing whole user turns (heartbeat exempt). The audit + the
   Hermes/OpenClaw research (Stage B) overturned that: the hazard is interleaved read-modify-write
   of shared state, closed by per-resource locks the heartbeat also passes through — not by
   serializing turns. B1 needs nothing from it; per-conversation ordering is already handled by the
   channel transports.

So **the app channel (B0/B1) is the next real deliverable**, with one safety item (Stage B)
deliberately pulled in front of it.

```
A. free cleanups ─► B. write safety ─► C. app channel (B0/B1) ─► D. rich rendering (open)
   (no deps)           (resource-level     (the deliverable)        (design when a real
                        lock; heartbeat                              consumer exists)
                        covered)
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

## Stage B — shared-state write safety (resource-level, heartbeat covered)

The hazard is interleaved **read-modify-write of shared state**, not concurrent turns as such.
Turns already run in parallel worker threads (`asyncio.to_thread(ask_jarvis, …)`), and a second
channel widens that — but the thing that actually corrupts is two writers loading the same file,
each mutating, each saving back (a lost update). An atomic `os.replace` prevents a *torn* file, not
a lost update. This supersedes the original plan's "keyed user-turn lock, heartbeat exempt."

**Audit (2026-07-24).** Of the three shared surfaces, two are already protected at the **resource**
level, both covering the heartbeat:
- **Memory files** — `tools/core/memory.py` `_WRITE_LOCK` (its comment already names the user turn
  *and* the heartbeat as the racing writers, and a second channel as "just another thread here").
- **Confirmation store** — `InMemoryConfirmationStore._lock` guards every `_pending` access.
- **`scheduled_events.json`** — the lone gap: `_append_event`/`_remove_event` did an unguarded
  load→mutate→save. And it's a **live** race today, not hypothetical: `heartbeat.py:201,211` removes
  fired events in a worker thread while a user `manage_reminder` can append/delete concurrently.

**The fix.** A module `threading.Lock` in `scheduling.py` held across the whole load→modify→save, so
each writer reads fresh committed state (locking only the save would keep the lost update). Covers
user turns, the heartbeat, and a future app channel *by construction* — a resource lock doesn't care
which thread you are. **No turn-level lock, no heartbeat exemption, no chat waiting behind a tick.**

**Why resource-level, not a turn lock (research-backed, 2026-07-24).** Both reference assistants —
**Hermes** (`NousResearch/hermes-agent`) and **OpenClaw** (`openclaw/openclaw`) — do exactly this:
shared writes are protected by **per-resource** locks held across the full read-modify-write with a
fresh in-lock read; the background scheduler is **not** turn-serialized and **not** exempt — it
funnels through the same resource locks. Both reserve turn-level serialization for *per-conversation
ordering* only, which Jarvis's channels already provide (PTB serializes Telegram; the app router is
single-consumer). OpenClaw's own review even flagged that *it* lacks a memory-file lock — a gap
Jarvis's `_WRITE_LOCK` already closes.

**Deferred (documented, not built).** Both references add a cross-process `flock`/DB lock because
*multiple processes* touch their stores. Jarvis's `scheduled_events.json` is single-process (one
service unit per instance root), so an in-process lock is correct and sufficient — the same
reasoning `memory.py` documents. The escalation trigger is identical: only if the heartbeat is ever
split into its own process. Tracked, together with the broader "one general store-writer primitive
instead of three ad-hoc locks" cleanup, in **issue #48**.

*Verify:* concurrent appends/removes lose no events (thread-stress test — done: 400 concurrent
appends → 400, no lost updates); `manage_reminder` and the heartbeat fire-path behave unchanged
single-threaded. **Restart** + a reminder round-trip.

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
  - **One consumer, not a task per update** — task-per-update would overlap same-user turns within
    the app channel; a single consumer keeps them ordered, the same per-conversation serialization
    the other transports provide (distinct from Stage B, which is resource-level write safety).
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
