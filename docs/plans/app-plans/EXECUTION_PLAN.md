# App channel — execution plan

**Status (2026-07-29):** Stage C on branch `feat/stage-c-app-channel`. **Landed & committed:** C1
(text round-trip, verified live), C2 (slash commands), C6 (channel identity — #50 Phase 1), `/help`
list fix, **C3 (outbound media), C4 (inbound media)** + its review follow-ups, and **§4 (image upload
metadata — width/height + blur-up placeholder)**. The hub **upgraded to the pinned
`3b3a48f330f09a39`** (skew resolved), and an independent audit confirmed we are **in sync with
`roiguri/jarvis-app-v2@main`** (no re-pin). **Remaining:** restart staging → on-device verify pass →
Telegram regression + `code-review` → PR to main. **Deferred:** C5 → B2; real `file`-kind ingestion
(PDF/video) → **#51**, shipped; see [../archive/MEDIA_INGESTION_PLAN.md](../archive/MEDIA_INGESTION_PLAN.md)
(interim: an honest "can't read yet" note, never a silent drop). Stages A + B
merged (#47, #49).
**Single source of truth.** Absorbs and replaces the former `APP_CHANNEL_PLAN.md` (index),
`02_MULTI_CHANNEL_SUPPORT.md`, and `03_APP_CHANNEL.md` (now in `archive/`). The only other live
material is the app author's pinned handover under `jarvis-app/` — **imported verbatim, not ours
to edit.**

**Goal:** ship the custom app as a second channel beside Telegram, without the Telegram loop — the
owner's day-to-day assistant — ever being the test surface. Work is sequenced by **real
dependency**, not by whether it touches existing or new code.

**Revision 2026-07-24.** Two facts changed Stage C since the first draft. (1) The hub contract
advanced to `contract_version = 3b3a48f330f09a39` (from `f1633277132cbedf`), adding an
**attachments** model — upload-then-reference (`POST /bot/v1/attachments` → `attachment_ids`),
kinds `image|audio|file`, and `Message.attachments[]` inbound. (2) The **phone is now connected to
the hub**, so every step verifies as a real device round-trip, not a phone-less curl. Consequently
media (in *and* out) now has a live consumer on both sides — `notifier.py` posters outbound, phone
photos → Gemini inbound — so it joins Stage C as **isolated additive steps (C3/C4)** rather than
deferring to Stage D. Stage C thus grows from "text round-trip" to **B0 (doc) + C1 text · C2 slash
commands · C3 outbound media · C4 inbound media · C5 (unsupported-confirmation notice — since deferred → B2)**; rich *blocks*
stay in Stage D (still no phone renderer).

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
- [x] Restart staging + Telegram regression — verified fine (byte-identical/tooling-only)

**Stage B — shared-state write safety** (resource-level; heartbeat covered, not exempt) — ✅ PR #49
- [x] Audit: memory (`_WRITE_LOCK`) + confirmation store (`_lock`) already resource-locked; `scheduled_events.json` the lone gap
- [x] B-lock — `threading.Lock` around the `scheduled_events.json` read-modify-write (whole load→modify→save), covering user turns + heartbeat + future channels. Differential test: lock-free loses 280/400 updates, locked 0
- [x] Follow-up filed: unify the three ad-hoc locks behind one store-writer primitive (issue #48)
- [x] Restart staging + a reminder round-trip — verified fine (lock is a no-op single-threaded)

**Stage C — app channel** (the deliverable; contract `3b3a48f330f09a39`, phone connected for real round-trip)
- [x] **Owner:** `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID` in staging `secrets/.env` (staging-specific hub bot token); documented (commented) in `.env.example`
- [x] Hub reachable from staging + contract pin re-validated via `GET /v1/health` (returned `3b3a48f330f09a39`)
- [x] Re-validate agent-internal references against current code + `jarvis-app/contract.md` (done during the C1 build — Channel ABC, factory, `main`, Outbox, poll-loop reference all re-checked)
- [x] B0 — **no new doc.** Pin `3b3a48f330f09a39` recorded as the `HubClient` constant (warn-on-mismatch); app specifics + the Telegram-vs-app differences write-up live in `gateway/channels/jarvis_app/` module docstrings. `GATEWAY.md` untouched (stays channel-agnostic); its media-cache row lands with C4
- [x] C1 — text round-trip (commit `3f4e7b0`). **Verified live on the phone** — `jarvis-app_<owner>` round-trip in `chat_history.jsonl`, both channels up, no crash, no degraded backoff
- [x] C2 — slash commands declared to the hub (`declare_commands`, non-fatal) (commit `22b463a`). *Pending phone verify:* the `declared 8 slash commands…` log line + the app slash menu
- [x] Commands polish — `/help` reformatted as a markdown list so it renders on all channels (bare-newline list collapsed on the app's CommonMark renderer) (commit `899fe67`)
- [x] C6 — origin channel injected into the system-prompt envelope (`[Channel: <name>]`, user scope, gate-safe) (commit `c5309f3`); Phase 1 of #50 (capability descriptors deferred there)
- [x] C3 — outbound media: `send_media`/`send_to_owner_media` upload (`POST /bot/v1/attachments`) then send `attachment_ids` (commit `55a987a`). image/audio pass through (image sniffs PNG/JPEG); any other kind raises `NotImplementedError` (Outbox reports a failed send). Consumer: `notifier.py`. *Verified* via the real channel code round-tripping an image to the hub; *pending* on-device render after restart
- [x] C4 — inbound media: router downloads `Message.attachments` → app `media_cache` → `InboundMessage.attachments` → Gemini (commit `d3e986e`). *Verified* upload→download byte-exact + `_handle` cases; *pending* an on-device photo→describe after restart
- [x] C4 review follow-ups (commit `bee778c`): reject malformed `att_` ids before a filesystem path + basename guard; `media_cache.save` inside the per-attachment try; retrieval note instead of an empty turn when every download fails
- [x] §4 — image upload metadata (commit `0783aeb`): `upload_attachment` sends optional `width`/`height`/`blur_preview`; the channel computes them for images via Pillow (`Pillow==12.3.0` added, import guarded). App reserves aspect ratio (no reflow) + shows a blur-up placeholder. `duration_ms` omitted (no outbound-audio producer). *Verified* the hub stored `w/h/blur_preview`
- [x] `file`-kind honest fallback (commit `23c347d`): the agent surfaces any unreadable kind as text rather than silently dropping it; real PDF/video ingestion tracked in **#51**, planned in [../archive/MEDIA_INGESTION_PLAN.md](../archive/MEDIA_INGESTION_PLAN.md)
- [ ] C5 — **DEFERRED → B2** (decided 2026-07-24). An interim `UnsupportedConfirmation` is throwaway: the real `AppConfirmationUI` (upstream B2) replaces it, and its registration seam already exists. Interim behavior is **safe** — app-origin destructive tools fall back to the default channel's (Telegram) confirmation; nothing fires silently. Build the real UI when the app ships confirmation; don't build the placeholder.
- [ ] **Restart staging** + Telegram regression + `code-review` skill, then PR `feat/stage-c-app-channel` → main

> **Hub-skew note — RESOLVED (2026-07-29):** the hub now serves `contract_version =
> 3b3a48f330f09a39`, the pinned version (`GET /v1/health`), so the warn-on-mismatch no longer fires
> and C3/C4 are unblocked. An independent audit of `roiguri/jarvis-app-v2@main` confirmed the
> contract has not advanced past the pin (verified against its CI drift test) — **no re-pin needed.**
> One low-impact upstream note it surfaced, now closed: our upload omitted the optional
> `width/height/blur_preview` metadata → landed as §4.

**Stage D — rich rendering / blocks** (design deliberately OPEN — decide when a consumer exists)
- [ ] Design the outbound seam against the real app renderer (`OutboundReply` is *one candidate*, not committed)
- [ ] 4b — sibling-thread chat injection (needs a second live user thread)
- [ ] 4c — durable cross-channel context (open question — not app-specific)
- [ ] Upstream deferrals: B2 app `AppConfirmationUI` — the app's real confirm/cancel UI (this is the successor to the deferred C5; until it lands, app-origin confirmations fall back to Telegram — safe) · B5 chips · B6 apps

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

1. **The *block* render seam does *not* gate C1.** C1's text reply rides the existing
   `on_message → return str → channel.send(str)` path and adds **zero new send call-sites**, so the
   "write against the seam to avoid a second edit at every send site" concern is empty.
   `OutboundReply` / `send_rich` matter only when something *emits blocks* — upstream B4, deferred
   behind the phone renderer regardless. Media (C3) does add send work, but it rides the *existing*
   `send_media(kind, bytes)` primitive and the attachment endpoints — not the block seam — so the
   point stands for the whole of Stage C.
2. **Write safety is a small resource-level fix, not a turn lock that gates the channel.** The
   original plan imagined a keyed lock serializing whole user turns (heartbeat exempt). The audit +
   the Hermes/OpenClaw research (Stage B) overturned that: the hazard is interleaved read-modify-write
   of shared state, closed by per-resource locks the heartbeat also passes through — not by
   serializing turns. C1 needs nothing from it; per-conversation ordering is already handled by the
   channel transports (the single-consumer loop).

So **the app channel is the next real deliverable**, with one safety item (Stage B) deliberately
pulled in front of it. Neither dependency point weakens with the contract update: media (C3/C4)
rides the *attachment* endpoints and the existing `send_media` primitive, not the block seam, so
the render seam still does not gate it; and per-conversation ordering is still the single-consumer
loop's job, not Stage B's.

```
A. free cleanups ─► B. write safety ─► C. app channel ──────────────► D. rich blocks (open)
   (no deps)           (resource-level     C1 text · C2 commands ·       (design when the
                        lock; heartbeat     C3/C4 media · C5 → B2         phone renders blocks)
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

## Stage C — the app channel (B0 + C1–C4; C5 deferred → B2)

The deliverable. Prereqs (generic factory, channel registry) are merged; the lock (Stage B) is in
place; the block render seam is **not needed** (blocks stay in Stage D). Build order: **B0** (doc),
then **C1** (text round-trip — the old "B1"), then the additive steps **C2** (slash commands) and
**C3/C4** (media, unlocked by the `3b3a48f330f09a39` attachments model + the connected phone).
**C5** (an interim unsupported-confirmation notice) was **deferred to B2** (see below). C1 is the
foundation; the rest are independent and may land in any order (or Stage C may stop after C1 to
prove text first).

**Channel identity.** `Channel.name = "jarvis-app"`; thread ids `jarvis-app_<owner>`. The Python
package is `gateway/channels/jarvis_app/` (packages cannot contain a hyphen — the one place dir and
name differ). Env vars use the handover names `APP_HUB_URL` / `APP_HUB_BOT_TOKEN` /
`APP_OWNER_USER_ID` (settled — see Decisions; already in `.env.example`).

**Scope.** Text + media. (The interim unsupported-confirmation notice, C5, was deferred to B2; the
app-origin confirmation gap is covered by a safe Telegram fallback meanwhile.) The hub also validates blocks, chips
and apps, but the phone renders none of *those* yet, so their adapters still wait (Stage D) —
building them now means code unexercisable when written. Attachments graduated *out* of that list:
the contract now carries them and the phone renders them, so media has a live consumer both ways.

**B0 — contract pin + in-code docs (no new doc file).** The app's specifics live where Telegram's
do — in the channel package, not the architecture layer. `client.py` holds the pinned
`contract_version = 3b3a48f330f09a39` as a constant and **warns** (never hard-fails) on mismatch —
the hub reports it on `GET /v1/health`, and already 422s a bad payload, so this only gives a
*silent* skew a voice. The adapter map (bot API → `Channel` ABC), the poll-loop ack semantics, and
degraded mode are documented in the package's module
docstrings (`client.py` / `channel.py` / `router.py`), mirroring how `host.py` / `channel.py`
self-document Telegram and how `fake_agent.py` documents its loop. `GATEWAY.md` stays
**channel-agnostic** — it is *not* the home for app specifics; its channel-agnostic registry tables
(e.g. the per-channel media-cache table) pick up an app row when the step that adds that artifact
lands (C4), a byproduct rather than a doc-writing phase.

**C1 — text round-trip.** New `gateway/channels/jarvis_app/`, three modules:

- **`client.py` — `HubClient` (httpx).** C1 needs `get_updates(offset, timeout)` and
  `send_message(body)`; raises `HubUnavailable` on 5xx/network error so the router can enter
  degraded mode. Bearer-token auth from `APP_HUB_BOT_TOKEN`. Later steps grow it in place:
  `declare_commands` (C2), `upload_attachment`/`download_attachment` (C3/C4). Events/PATCH (chips,
  block resolution) wait for Stage D.
- **`channel.py` — `JarvisAppChannel(Channel)`.** `name = "jarvis-app"`,
  `owner_thread_id = f"jarvis-app_{owner}"`. C1 implements the **text** sends by POSTing to the hub;
  `send_media`/`send_to_owner_media` raise `NotImplementedError` per the ABC (the `Outbox` already
  reports that as a failed send, no crash) until C3 makes them real uploads. **No `OutboundReply`
  here** — the rich-block render story is Stage D.
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
the existing shared `on_message` — slash commands and history logging work with no app-specific
code (already channel-agnostic). Confirmations are the one interaction that needs an app-specific
piece, because the hub has no confirm/cancel widget the phone renders yet — deferred to B2 (interim:
app-origin confirmations fall back to Telegram, safe). The app
wire carries no per-message user id (the bot token scopes the single owner), so the router stamps
the configured owner's thread id and passes benign placeholders for the `InboundMessage`
`user_id`/`chat_id` ints — nothing downstream reads them (verified: only Telegram's own routing does).

**Degraded mode.** Hub unreachable → log **once** (not per failed poll) and back off 1→60s forever.
Telegram and heartbeat unaffected; the agent never crashes on a missing hub. This is what makes C1
safe to carry in prod behind the `APP_HUB_URL` gate before the channel is "done".

**Wiring.** `build_jarvis_app_stack()` joins the factory beside the Telegram builder (both thin
wrappers over `build_stack`). `main.py` builds the app stack **only when `APP_HUB_URL` is set** and
starts its router with `create_task(router.run())` — so prod, with the var unset, constructs
nothing and is byte-identical to today.

**C2 — slash commands.** `HubClient.declare_commands` posts the gateway's shared command list
(`gateway/commands`) to `POST /bot/v1/commands` at router startup, mirroring Telegram's
`register_command_menu`. Non-fatal: a hub that's down at startup just skips it (the degraded loop
carries on), so it never blocks C1. Isolated step with its own verify (the slash menu on the phone).

**C3 — outbound media.** The attachments model makes `send_media`/`send_to_owner_media` real: a
two-step **upload-then-reference** — `POST /bot/v1/attachments` returns an `att_…` id, then
`send_message(attachment_ids=[…], text=caption)`. Kinds are `image|audio|file`; `notifier.py`'s
posters are `image`, the live consumer. An unrepresentable kind raises `NotImplementedError`, which the Outbox reports as a failed send (a graceful caption fallback can come with the capability work in #50).
*Verify:* trigger a media notification → the poster renders on the phone.

**C4 — inbound media.** `Message.attachments[]` on an inbound update → the router downloads each via
`GET /bot/v1/attachments/{id}` → saves to an **app-owned `media_cache.py`** (absolute paths, the
same channel-owns-storage rule as Telegram's) → `InboundMessage.attachments=[{kind, path, mime_type,
source}]`, which the existing `process_inbound_message` already forwards to Gemini. Mirrors
Telegram's `_download_and_store`. *Verify:* send a photo from the phone → Jarvis describes it.

**C5 — the unsupported-capability notice — DEFERRED to B2 (decided 2026-07-24).** Building an
app-scoped `UnsupportedConfirmation` now is throwaway: the real `AppConfirmationUI` (upstream B2)
replaces it the moment the app can render a confirm/cancel widget, and the per-channel registration
seam it would plug into (`register_confirmation` / `_confirmation_stores` in the factory) already
exists. So we skip the placeholder and build the real UI when the app ships confirmation.

**Interim behavior (safe, documented).** Until B2, no confirmation store is registered for the app,
so `get_confirmation()` on an app-origin turn falls back to the **default channel's store
(Telegram)**: a destructive tool triggered from the app renders its confirm/cancel prompt on
Telegram, and the action fires **only** if the owner taps Confirm there — nothing destructive
happens silently. It is a cross-device wrinkle (a dead-end if the owner isn't on Telegram), not a
hazard. The general principle still stands and is realized by B2: a channel that can't render a rich
interaction has the gap surfaced to the agent, which phrases the human explanation.

**Owner prereq (done).** `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID` are in staging's
`secrets/.env` with a **staging-specific hub bot token** — the hub is one-bot-one-user, so a
staging agent sharing prod's token would fight over the update queue. Documented (commented) in
`.env.example`.

*Verify (staging, real device).* The phone is connected to the hub, so each step above verifies as
a real round-trip on the device (not the phone-less curl the first draft assumed — that remains a
fallback). C1: type on the phone, get a reply, see a `jarvis-app_<owner>` row in
`chat_history.jsonl`. Then the Telegram regression, unchanged, plus the `code-review` skill.

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

- **Media kind fallback.** C3 makes `image` real; an outbound kind the app can't represent raises
  `NotImplementedError` (the Outbox reports a failed send). A graceful caption / `[kind]`-placeholder
  degradation (the convention `Outbox._log` uses at `outbox.py:115`) can land with the capability
  work (#50).
- **Inbound `file`-kind ingestion (#51)** — **planned in
  [../archive/MEDIA_INGESTION_PLAN.md](../archive/MEDIA_INGESTION_PLAN.md)** (2026-07-29); that doc supersedes this
  sketch. C4 downloads `file`-kind attachments (the hub's `file` bundles PDF + video by mime), but
  the agent can't yet feed them to the model — it emits an honest "can't read yet" note. **No longer
  gated:** the app composer *can* send a video as a file. Two corrections to the sketch above: the
  `file`+mime → kind mapping belongs in the **app router** (as Telegram already does at
  `telegram/router.py:95-102`), not in `agent.py`; and the inline-size guard is **channel-agnostic**
  — it belongs at the model boundary in `agent.py`, where it also closes the same unguarded hole on
  Telegram. Size question decided as **cap-and-note** (Files API gated behind a `_strip_media_blobs`
  fix — see the plan).
- **4b — sibling-thread chat injection.** User-scope prompts additionally inject today's chat from
  the *other* user thread (bounded by the same start-of-Israel-day window and per-entry cap as the
  existing slices). Needs a second *live* user thread to be meaningful — telegram↔app is the same
  person switching devices mid-conversation, so *"as I said a minute ago"* fails without it.
- **4c — durable cross-channel context** (open question 1). Injection carries only *today's*
  sibling chat; beyond that, continuity depends on the daily log or a memory write. Not
  app-specific — the same time-bound governs heartbeat↔user today — so treat it on its own terms,
  alongside `../CONTEXT_HANDLING_PLAN.md` (a wider window costs tokens).
- **Upstream honest-boundary deferrals:** B2 app `AppConfirmationUI` (the confirm/cancel UI; the
  successor to the deferred C5), B5 streaming chips, B6 apps. Each is real and specified in
  `jarvis-app/original_app_plan.md`; none is end-to-end testable today. (B1.6 media inbound
  graduated to **Stage C4** — the phone sends attachments now.)

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

**Env var names are `APP_*`, settled.** `APP_HUB_URL` / `APP_HUB_BOT_TOKEN` / `APP_OWNER_USER_ID`,
matching the app author's handover — no `JARVIS_APP_*` prefix. Already written (commented) into
`.env.example`; the value in staging's `secrets/.env` uses a staging-specific hub bot token so
staging and prod never fight over the one-bot-one-user update queue.

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
| `contract.md` | The wire contract, **generated from the hub's Pydantic models** — the single source of truth for payload *shape*. Pinned at `contract_version = 3b3a48f330f09a39` (bumped 2026-07-24, adds the attachments model), which the live hub reports on `GET /v1/health`. B0 records the pin; `HubClient` warns (not hard-fail) on mismatch |
| `fake_agent.py` | A fake **agent** (not a fake hub) — it long-polls a *running* hub. Its value is as the reference poll loop B1 must write (`:366-417`) |
| `original_app_plan.md` | The approved Track B plan (2026-07-12), phases B0–B6. Upstream's *capability* sequencing |

**The honest boundary — don't build ahead of the phone.** The hub validates more than the phone
renders: `blocks` have no renderer or action path (B2/B4 wait), chips fan out with no consumer (B5
waits), no apps endpoints yet (B6 waits). **Attachments crossed the boundary on 2026-07-24** — the
contract added them and the phone renders them, so media is now buildable/verifiable (Stage C3/C4).
**Buildable and verifiable now: B0, C1–C4** (C5 deferred → B2; this plan's Stage C), plus B1.5/B3 which are folded
into the landed multi-channel work and Stage D.

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
| Queue-epoch hub signal | Raise with the app author. Hazard: if the hub's queue is wiped and re-sequenced while the agent holds a higher offset, the next poll acks updates it never fetched → ✓✓ with no reply. Agent-side detection is **provably unreliable** (the ack is implicit in `GET /updates?offset=N`). Needs a hub-side queue epoch. Until then: **restart the agent alongside any hub wipe** |
| Blocks mechanism (`OutboundReply` or otherwise) | Stage D — kept open by this plan |
