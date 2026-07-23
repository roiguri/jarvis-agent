# App channel — parent plan

**Status:** planning, uncommitted. **Date:** 2026-07-20. **Branch:** `docs/app-channel-plan`.
**Goal:** ship the custom app as a second channel beside Telegram, without the Telegram loop —
the owner's day-to-day assistant — ever being the test surface.

This is the **index**. It holds the steps, their dependencies, and what must be true before any
of them start. Technical detail and findings live in the step documents.

---

## Steps

| # | Step | Status | Document | What it touches |
|---|---|---|---|---|
| 1 | Staging environment | ✅ **done 2026-07-23** | [../archive/STAGING_AND_DEPLOY.md](../archive/STAGING_AND_DEPLOY.md) | `config.py`, `main.py`, path constants; a second service |
| 2 | Multi-channel support | planning | `02_MULTI_CHANNEL_SUPPORT.md` | **Existing** gateway/agent code — the owner-addressing seam |
| 3 | Adding the new channel | planning | `03_APP_CHANNEL.md` | **New** `gateway/channels/app/` |

Step 1 is **not owned by this plan.** Staging and deploy discipline are general infrastructure —
the app channel is their first beneficiary, not their reason — so they live outside `app-plans/`
and proceed on their own schedule. This plan consumed them as a dependency; **they are now
complete and archived** (staging bot live, prod deploy-only via `deploy.sh`/`rollback.sh`). Steps
2 and 3 are not written yet.

**Dependencies.** 1 and 2 are independent and can proceed in either order — staging is about
*where state lives*, step 2 is about *how the gateway addresses the owner*; they share no code.
Step 3 hard-depends on 2 (a second channel cannot exist while the gateway assumes exactly one)
and soft-depends on 1 (buildable without staging, but every test turn then writes to production
memory, `threads.sqlite`, and `turns.jsonl`).

```
1. staging ──────────┐
                     ├──► 3. new channel
2. multi-channel ────┘
```

Step 2 is worth doing on its own terms: it is architectural work on code already in production,
and it stands whether or not the app ships.

---

## Sources

The app author supplied the material in `jarvis-app/`. It is **imported verbatim and not ours to
edit** — where it disagrees with this codebase, the step documents record the delta.

| File | What it is |
|---|---|
| `original_app_plan.md` | The approved Track B plan (2026-07-12), phases B0–B6. Upstream's *sequencing*; the steps above are the conceptual split, and the phases map across them |
| `contract.md` | The wire contract, **generated from the hub's Pydantic models** — the single source of truth for payload *shape*. Verified current at `contract_version = f1633277132cbedf`, which the live hub reports on `GET /v1/health`. B0 records the pin; `HubClient` warns (does not hard-fail) on mismatch at startup |
| `fake_agent.py` | A fake **agent**, not a fake hub — it long-polls a *running* hub. Its value here is as the reference implementation of the poll loop step 3 must write |

**Contract authority (app-author handover, 2026-07-20).** `contract.md` *is* the shape authority
— it is generated, not a digest, superseding `original_app_plan.md` line 29's pointer at a
`JARVIS_APP_ARCHITECTURE.md §5` we never received. The cross-track build order that line 85
deferred to `JARVIS_APP_IMPL.md` is likewise resolved: the handover states `B0 → B1 → B1.5 → B3`
is the whole of what is end-to-end verifiable today (see below), so neither absent doc blocks us.

**The honest boundary — don't build ahead of the phone.** The hub validates more than the phone
can render: `blocks` have no renderer or action path (B2/B4 wait), chips fan out with no consumer
(B5 waits), and there are no attachment/apps endpoints yet (B1.6/B6 wait). Buildable and
verifiable now: **B0, B1, B1.5, B3.** Three B1 requirements live in the plan prose, not the
schema — concurrent poll loop, drain on SIGTERM, hub-down backoff 1→60s — and `fake_agent.py`
demonstrates the first two.

**Our own re-validation duty.** The handover cannot see this repo, so it explicitly asks that
every agent-internal reference in `original_app_plan.md` (`store.py:211`, `main.py:102`, the
`Channel` ABC, `ask_jarvis`, line numbers) be re-checked against current code before B1. That is
exactly the delta table in [02_MULTI_CHANNEL_SUPPORT.md](02_MULTI_CHANNEL_SUPPORT.md) — the
re-validation is already underway there, not a new task.

---

## How the B-phases map onto the steps

Upstream sequences by capability; these steps sequence by what code is touched. Both views are
useful — this table is how to read one in terms of the other.

| Step | B-phases |
|---|---|
| 2 — multi-channel support | B3 (proactive fan-out), B4 (`send_rich` / `OutboundReply` seam), B2's `FanoutConfirmationUI`, B1.5 (sibling-thread chat injection), `default_owner_thread_id()` routing |
| 3 — adding the new channel | B0 (contract pin + doc), B1 (client/channel/router), B1.6 (media inbound), B2's `AppConfirmationUI`, B5 (streaming chips), B6 (apps) |

---

## Before anything starts

Track A's hub **exists and runs**, and per the app author B1, B1.5 and B3 are implementable now.
What stands in the way:

1. ~~**Staging**~~ — ✅ **done** ([../archive/STAGING_AND_DEPLOY.md](../archive/STAGING_AND_DEPLOY.md), completed
   2026-07-23). The staging bot is live against its own root, so every phase now verifies against
   the live agent without touching prod state. One hub-side consequence still stands (that plan's
   open question 5): the app channel is *outbound*, so it binds no port and cannot collide the way
   the webhook does — but the hub is one-bot-one-user, so once the channel is live in prod, a
   staging agent polling the same hub would fight over updates. Give staging its own hub bot token.
2. **Three env vars** in `/app/secrets/.env` — `APP_HUB_URL`, `APP_HUB_BOT_TOKEN`,
   `APP_OWNER_USER_ID`. Owner-supplied.

Nothing else is a hard blocker for the first phases.

**Open, not blocking:** ABC exception (b) — the `record_tool_call_start` hook in `_tool_node` —
is unsigned, but that is B5. Concurrent same-user turns are accepted for v1 and not implemented
against; worth re-confirming now that a second channel is real rather than hypothetical.

---

## Cross-cutting

**Isolation has a ceiling, and it is not config-shaped.** Staging isolates every byte Jarvis
owns. It does not isolate what Jarvis *reaches* — Radarr, Sonarr, Jellyseerr, Arbox and web
search are live from any instance, and `fetch_upcoming_arbox_classes` upserts and purges rather
than reads, with a one-shot removal notice. Closing that gap is tool-layer work, tracked nowhere
yet.

**House rules.** Every phase ends with: the owner restarts the affected service, watch
`journalctl`, send a real message. After each phase touching the gateway, run the Telegram
regression (GATEWAY.md step 9) and the repo `code-review` skill. Source comments stay
behavior-only — plan context goes in commit messages. Nothing commits without approval.

---

## Open questions

1. ~~**Build order** — staging first, or start step 2 in parallel?~~ **Resolved:** staging landed
   first (2026-07-23), so step 2 can now start against the staging bot with no risk to prod.
2. ~~**Does deploy discipline ride with staging?**~~ **Resolved:** they shipped together but as
   separate slices — `deploy.sh`/`rollback.sh` (slice 4a) landed alongside the staging bot (slice 3)
   under one plan, now archived. The split the lean called for held: each reverts on its own.
3. **How do upstream deltas get maintained?** The step documents will record where
   `original_app_plan.md` is stale against current code. Annotating a frozen spec has an ongoing
   cost; the alternative is asking the app author to reissue it against current code.
