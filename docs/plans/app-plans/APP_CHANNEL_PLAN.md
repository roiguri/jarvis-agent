# App channel — parent plan

**Status:** planning, uncommitted. **Date:** 2026-07-20. **Branch:** `docs/app-channel-plan`.
**Goal:** ship the custom app as a second channel beside Telegram, without the Telegram loop —
the owner's day-to-day assistant — ever being the test surface.

This is the **index**. It holds the steps, their dependencies, and what must be true before any
of them start. Technical detail and findings live in the step documents.

---

## Steps

| # | Step | Document | What it touches |
|---|---|---|---|
| 1 | Staging environment | `01_STAGING_ENVIRONMENT.md` | `config.py`, `main.py`, path constants; a second service |
| 2 | Multi-channel support | `02_MULTI_CHANNEL_SUPPORT.md` | **Existing** gateway/agent code — the owner-addressing seam |
| 3 | Adding the new channel | `03_APP_CHANNEL.md` | **New** `gateway/channels/app/` |

None written yet.

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
| `contract.md` | Wire contract **digest**. Track A's `JARVIS_APP_ARCHITECTURE.md` §5 wins on disagreement |
| `fake_agent.py` | A fake **agent**, not a fake hub — it long-polls a *running* hub. Its value here is as the reference implementation of the poll loop step 3 must write |

**Absent, and worth requesting from the app author:** `JARVIS_APP_ARCHITECTURE.md` (the contract
authority) and `JARVIS_APP_IMPL.md` (owns the cross-track build order, which
`original_app_plan.md` line 85 deliberately points at rather than copies). Neither blocks the
first phases; both are needed once confirmations and rich payloads start.

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

1. **Staging** (step 1). The upstream plan verifies every phase against the live agent
   (`original_app_plan.md` line 91). B3 is the phase that starts pushing real heartbeat and
   reminder traffic to a new device.
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

1. **Build order** — staging first, or start step 2 in parallel? Step 2 touches production
   gateway code, which is an argument for staging existing first.
2. **Does deploy discipline ride with staging?** The stale `feat/staging-env` plan bundled prod
   pinning + `deploy.sh`/`rollback.sh` with the staging work. Lean: split — staging blocks the
   app work, deploy discipline does not.
3. **How do upstream deltas get maintained?** The step documents will record where
   `original_app_plan.md` is stale against current code. Annotating a frozen spec has an ongoing
   cost; the alternative is asking the app author to reissue it against current code.
