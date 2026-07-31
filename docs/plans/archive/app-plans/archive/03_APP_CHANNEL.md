# Step 3 — Adding the app channel  ·  ARCHIVED

> **ARCHIVED 2026-07-23 — folded into [../EXECUTION_PLAN.md](../EXECUTION_PLAN.md)**, now the single
> source of truth (see its Stage C for B0/B1). Kept for the verbatim settled-decisions prose. Two
> dependencies asserted here did not hold on inspection: the render seam does **not** gate B1
> (text-only rides the existing `send(str)`), and the concurrency lock gates the **prod flip**, not
> building/validating B1 in staging.

**Status:** planning, uncommitted. **Date:** 2026-07-20.
**Parent:** [APP_CHANNEL_PLAN.md](APP_CHANNEL_PLAN.md).
**Hard-depends on:** step 2 phases 1d (generic factory) + 2a (channel registry).
**Soft-depends on:** the staging environment (`../archive/STAGING_AND_DEPLOY.md`) — this is where the
poll loop is developed and tested, so staging lands first.

**Goal:** a new `gateway/channels/jarvis_app/` that carries a text turn end-to-end between the
owner and the agent over the hub's long-poll bot API — Telegram untouched, and the whole thing
inert in prod until `APP_HUB_URL` is set.

**Channel identity.** `Channel.name = "jarvis-app"`; thread ids are `jarvis-app_<owner>`. The
Python package is `gateway/channels/jarvis_app/` (packages cannot contain a hyphen, so the dir
underscores where the name hyphenates — the one place the two differ). Env vars keep the app
author's handover names (`APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID`) unless we
decide to prefix them `JARVIS_APP_*` for consistency — **small open choice, noted below.**

**Scope is B0 + B1 only.** The handover's honest boundary (parent plan) is the reason: the hub
validates blocks, chips, attachments and apps, but the phone renders none of them yet. Building
their adapters now means writing code unexercisable at the moment it is written. B1.5 and B3 are
part of getting the app working end-to-end, but they touch *existing* code and therefore live in
step 2; this document is only the new package.

---

## Checklist

**Bold** items are yours (restarts, env, hub). Every phase ends with the Telegram regression
(GATEWAY.md step 9) — the app channel must never disturb it.

### Prereqs (elsewhere, must be green first)
- [x] Staging exists (`../archive/STAGING_AND_DEPLOY.md` — complete 2026-07-23)
- [ ] Step 2 phase 1d (generic factory) + 2a (channel registry) landed
- [ ] Re-validate every agent-internal reference the old plan cites (step 2 delta table)
- [ ] **Add `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`, `APP_OWNER_USER_ID` to `staging.env`**

### B0 — contract pin + doc
- [ ] `docs/architecture/APP_CHANNEL.md`: adapter mapping, degraded mode, contract version
- [ ] Record the pin `contract_version = f1633277132cbedf`
- [ ] Update GATEWAY.md's channel tables

### B1 — text round-trip
- [ ] `client.py` — `HubClient` (get_updates / send_message / set_commands); `HubUnavailable` on 5xx
- [ ] `channel.py` — `JarvisAppChannel(Channel)`, `name="jarvis-app"`, `owner_thread_id = f"jarvis-app_{owner}"`
- [ ] `router.py` — fetcher + single-consumer poll loop over a queue (see below)
- [ ] Hub-down: log once, back off 1→60s forever; Telegram + heartbeat unaffected
- [ ] Drain on SIGTERM: stop fetching, finish the queue, exit
- [ ] `build_jarvis_app_stack()` in the factory; `main.py` builds it **only if `APP_HUB_URL` is set**
- [ ] Concurrency lock is a **step 2 prereq**, not built here (see Settled decisions)
- [ ] **Start staging with `APP_HUB_URL` set**; curl the hub as "the app", confirm a reply and
      a `jarvis-app_<owner>` row in `chat_history.jsonl`
- [ ] **Restart staging**; Telegram regression still green

---

## Settled decisions

These were open in the parent discussion; resolved 2026-07-20.

**1. Concurrency — lock across user threads only, and it lives in step 2.** A second user
channel makes it possible for the same person to have two turns in flight (`telegram_<id>` and
`jarvis-app_<id>`), and they share memory files, `scheduled_events.json`, and the confirmation
store; `ask_jarvis` has no lock. A lock that serializes *user* turns against each other — while
leaving heartbeat concurrent — fixes the new failure mode without slowing anything that works
today. Not a global `ask_jarvis` lock: that would serialize a chat message behind a 90s tick;
the lock is keyed so heartbeat is exempt. **This is the one settled decision that is real code,
and it belongs in step 2** (multi-channel support — making concurrent same-user turns safe is a
multi-channel-safety concern, on the same side of the line as the routing and registry work).
Step 3 depends on it being in place; it is not built in this package.

**2. Offset/queue-rebuild — restart discipline, because agent-side detection is not reliable.**
The hazard (observed in the app's own dev testing): if the hub's queue is wiped and
re-sequenced while the agent holds a live higher offset, the next poll *acks updates the agent
never fetched* — sends show ✓✓ with no reply. The tempting fix ("notice `update_id` went
backwards") **cannot work**: the ack is implicit in `GET /updates?offset=N`, which acks
everything below N, so the wiped updates are acked and never returned — there is no id to
compare. "Queue rebuilt beneath me" and "no new messages" are byte-identical from the agent.
Reliable detection needs a hub-side signal (a queue epoch/generation on `/health` or alongside
updates). **Now:** restart the agent alongside any hub wipe (offset resets to 0). **Raise with
the app author:** a queue-epoch field — a small Track A change — after which we implement
detection.

**3. `thread_id` — keep the underscore, delete the deferral note.** `gateway/base.py:24` freezes
the format at `telegram_<user_id>` and defers a `:` separator change to a checkpointer-key
migration. The only reason to prefer `:` is parse ambiguity if a channel name contained `_` —
and neither `telegram` nor `jarvis-app` does (the hyphen in `jarvis-app` is not a `_`).
`jarvis-app_<id>` parses fine beside `telegram_<id>` (prefix is everything before the first `_`).
Migrating would mean rewriting live conversation-state keys in `threads.sqlite` for a
hypothetical collision. Keep the underscore; remove the note as resolved.

**4. Sequencing — staging first, then B1.** Testing a long-poll loop means running an agent
pointed at the hub. Doing that from the prod tree is exactly what staging exists to prevent, and
B3 (in step 2) is what starts pushing real proactive traffic. B1 is developed in
`/app/jarvis_staging/code`.

---

## B0 — contract pin + doc

New `docs/architecture/APP_CHANNEL.md`: how the adapter maps the bot API onto the `Channel` ABC,
the degraded-mode contract, and the pinned `contract_version = f1633277132cbedf`. The pin is
against something real — the hub reports its version on `GET /v1/health`, so `HubClient` logs a
**warning** (never hard-fails) on mismatch at startup: the hub's strict validation already 422s a
bad payload, so this only gives a *silent* skew a voice. Update GATEWAY.md's channel tables to
list `app` beside `telegram`.

---

## B1 — text round-trip

New `gateway/channels/jarvis_app/`, three modules:

**`client.py` — `HubClient` (httpx).** For v1: `get_updates(offset, timeout)`,
`send_message(body)`, `set_commands(...)`. Raises `HubUnavailable` on 5xx so the router can
enter degraded mode. Bearer-token auth from `APP_HUB_BOT_TOKEN`. The wider surface
(attachments, events, PATCH) waits for the phases that use it.

**`channel.py` — `JarvisAppChannel(Channel)`.** `name = "jarvis-app"`,
`owner_thread_id = f"jarvis-app_{owner}"`. Implements the abstract sends by POSTing to the hub.
Its capability story is the render model from step 2 phase 3 — no negotiation here; `send_rich`'s
default already degrades blocks to text for channels that lack a renderer, and `JarvisAppChannel`
is the one that *has* one.

**`router.py` — the poll loop.** This is the load-bearing part, and the shape is not
negotiable — `fake_agent.py:366-417` is the working reference:

- **Two tasks over a queue.** A `_fetch_loop` that long-polls and, per update, advances the
  offset (`offset = update_id + 1`) and enqueues **before** any turn runs; and a single
  `_consume_loop` that runs turns one at a time. The re-poll is what acks the previous batch, so
  it must go out *while* a turn is still running. A serial `poll → turn → poll` collapses the
  app's ✓✓ into the reply and hides a whole class of concurrency bug.
- **One consumer, not a task per update** — task-per-update would overlap same-user turns, the
  exact failure mode decision 1 locks against.
- **Fetch errors don't kill the fetcher.** A dropped poll logs, backs off, and continues; the
  consumer must never be left idle with no symptom (`fake_agent.py:371-378`).
- **Drain on SIGTERM.** Cancel the fetcher (anything fetched is already queued — the ack was the
  poll), `queue.join()` to finish in-flight turns, then exit. Updates are acked on receipt, so a
  crash-without-drain loses whatever was fetched-not-finished — the same exposure Telegram has
  today, and the reason a clean drain matters most on deploy, the most frequent restart. This is
  the same graceful-shutdown work as **#33**, which the staging plan's auto-deploy question is
  blocked on — one implementation, two payoffs.

Each inbound update becomes an `InboundMessage(thread_id=f"jarvis-app_{owner}")` and flows through the
existing shared `on_message` — so slash commands, history logging, and confirmations work with no
app-specific code (they are already channel-agnostic).

**Degraded mode.** Hub unreachable → log **once** (not per failed poll) and back off 1→60s
forever. Telegram and heartbeat are unaffected; the agent never crashes on a missing hub. This
is what makes B1 safe to carry in prod behind the `APP_HUB_URL` gate before the channel is
"done".

**Wiring.** `build_jarvis_app_stack()` joins the factory beside `build_telegram_stack` (both now thin
wrappers over step 2's generic `build_stack`). `main.py` builds the app stack **only when
`APP_HUB_URL` is set** and starts its router with `create_task(router.run())` — so prod, with the
var unset, constructs nothing and is byte-identical to today.

**Verify (staging).** With `APP_HUB_URL` pointed at the hub: curl the hub's client API as "the
app", confirm the live agent long-polls, replies, and writes a `jarvis-app_<owner>` row to
`chat_history.jsonl` — a phone-less end-to-end test. Then the Telegram regression, unchanged.

---

## Deferred — not in this step (honest boundary)

| Phase | What | Waits on |
|---|---|---|
| B1.6 | media inbound + app `media_cache.py` | hub attachments (A3) + app send (M4) |
| B2 (app half) | `AppConfirmationUI` | step 2 phase 2c origin routing; phone action-update path |
| B4 | rich payloads / blocks | phone block renderer |
| B5 | streaming chips | phone chip consumer; unsigned ABC exception (b) |
| B6 | apps | manifest schema; no bot endpoint yet |

Each is real and specified in `original_app_plan.md`; none is end-to-end testable today.

---

## Open questions

1. **Queue-epoch signal (decision 2).** Restart discipline holds until the hub can report a
   queue generation. Raise with the app author; until then a forgotten restart after a hub wipe
   silently ✓✓s messages. Dev-time only, but sharp.
2. **`ask_jarvis` return type.** Step 2 phase 3c introduces `OutboundReply`. B1 sends text only,
   so it can consume either — but it should be written against the seam step 2 lands, not a
   pre-B4 string, to avoid a second edit at every send site. Confirm step 2 phase 3 precedes B1.
3. **Env var prefix.** Keep the handover's `APP_HUB_URL` / `APP_HUB_BOT_TOKEN` /
   `APP_OWNER_USER_ID`, or rename to `JARVIS_APP_*` to match the channel name? Cosmetic; decide
   before they are written into `staging.env` so the name is not changed twice.
4. **Concurrency lock placement — resolved.** The keyed user-turn lock (settled decision 1) is
   step 2 phase 5, independent and parallelizable. B1 depends on it being in place but does not
   build it.
