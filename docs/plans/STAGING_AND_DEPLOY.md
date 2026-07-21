# Staging environment & deploy discipline

**Status:** planning, uncommitted. **Date:** 2026-07-20.
**Supersedes:** `feat/staging-env` (e20a7a4, 2026-07-10) — stale, will not be merged. Much of the
analysis below is imported from it; additions and corrections are marked **[new]**.
**First beneficiary:** the app channel (`docs/plans/app-plans/APP_CHANNEL_PLAN.md`), but this is
general infrastructure and stands on its own.

**Goal:** development stops happening inside the live service, and a broken deploy is one
command to undo.

**Why now.** On 2026-07-20 a `git checkout -b <branch> main` inside `/app/jarvis_code` put the
running service's working tree on the wrong branch. For the window before it was corrected, a
restart would have silently booted prod without the heartbeat cost fix. Nothing warned anyone.
That is the problem this plan closes.

---

## Checklist

Work down it. **Bold** items are yours (I can't restart the service or talk to BotFather).

### Slice 0 — land the current branch
- [ ] Finish `fix/heartbeat-cost` verifications (its own plan, §5)
- [ ] Merge to `main`; **restart prod**; confirm the tick behaves

### Slice 1 — prod says what it is running
- [x] `main.py`: log git provenance at startup — a multi-line `Running code:` block (branch, sha,
      commit subject + date, deploy-tag) as the last startup line, plus a compact early one-liner
- [ ] **Restart prod**, read the block in `journalctl`/`systemctl status` (expect `branch : main …`)
- [ ] (instance + memory/data roots join the block at slice 2; heartbeat/webhook/reminders at slice 3)

### Slice 2 — local testing stops touching prod
- [ ] `config.py`: `JARVIS_INSTANCE`, `JARVIS_MEMORY_DIR`, `JARVIS_DATA_DIR` (+ derived paths)
- [ ] Repoint the path literals (inventory below)
- [ ] Boot guard: non-prod instance + prod memory dir = refuse to start
- [ ] REPL (`agent.py` `__main__`) defaults to a scratch tree
- [ ] Verify: no env set → every constant equals its old literal; prompts byte-identical
- [ ] **Restart prod**, send a message, watch one heartbeat tick

### Slice 3 — the staging bot exists
- [ ] `config.py`: `JARVIS_ENV_FILE`, `JARVIS_WEBHOOK_PORT`
- [ ] Toggles: `JARVIS_HEARTBEAT_ENABLED`, `JARVIS_REMINDERS_ENABLED`, `JARVIS_WEBHOOK_ENABLED`
- [ ] **Create the staging bot** via BotFather; hand over the token
- [ ] Dirs `/app/jarvis_staging/{code,memory,data}`; clone; venv
- [ ] `staging.env` = prod copy + staging bot token, `chmod 600`
- [ ] Seed memory: `rsync -a --exclude 'threads.sqlite*'`
- [ ] `jarvis-staging.service` — **not** `systemctl enable`d
- [ ] **Start staging**, chat with the staging bot
- [ ] Verify prod untouched: `find /app/jarvis_memory /app/jarvis_data -newer <marker>` empty

### Slice 4 — prod becomes deploy-only
- [ ] Move development to `/app/jarvis_staging/code`
- [ ] `scripts/deploy.sh` (tag outgoing commit → ff-only pull → print restart hand-off)
- [ ] `scripts/rollback.sh` (list tags → checkout → hand-off)
- [ ] Migrate in-flight branches; put `/app/jarvis_code` on `main`
- [ ] Update `CLAUDE.md` + `DEVELOPMENT.md`
- [ ] Dry run: deploy on an already-current main = clean no-op
- [ ] Rehearse a rollback before you need one

---

## What makes today "straight to production"

1. **Working tree = live service.** `ExecStart` runs `/app/jarvis_code/main.py`; development
   happens in the same tree. At the 2026-07-10 review prod was running a feature branch — the
   norm, not an accident. See the 2026-07-20 incident above.
2. **Every state path is a hardcoded constant.** A second instance today would share memory,
   `threads.sqlite`, logs, and heartbeat stamps.
3. **The REPL is not isolated.** `agent.py`'s `__main__` uses thread `local_dev_test_01` — a
   separate *conversation*, but the same DB, memory, logs and live tools. A "local test" calling
   `write_memory` mutates production memory. That thread is in prod `threads.sqlite` now.
4. **Two collision points:** the webhook binds `:8000` (`main.py:123`), and a second heartbeat
   scheduler would double-run every task.
5. **Reminders restore on boot** (`main.py:147`) — a staging start re-fires the owner's real
   events. **[new]** — the 2026-07-10 plan noted this only as a standing risk.
6. **Already safe:** `prompts/` (`agent.py:176`) and the Telegram media cache
   (`media_cache.py:15`) resolve relative to `__file__`, so a staging checkout reads its own.

---

## Target shape

```
/app/jarvis_code/            PROD code — deploy-only, tagged main
/app/jarvis_memory/          PROD memory     (defaults; the prod unit sets no JARVIS_* vars)
/app/jarvis_data/            PROD data
/app/secrets/.env            PROD secrets

/app/jarvis_staging/code/    DEV + STAGING code — feature branches; own venv
/app/jarvis_staging/memory/  STAGING memory — seeded from a prod snapshot
/app/jarvis_staging/data/    STAGING data — starts empty
/app/secrets/staging.env     STAGING secrets — prod copy, staging bot token
```

Same host (an LXC clone was considered and rejected 2026-07-10: Proxmox-level work, duplicated
resources, two-container drift). Staging seeds memory from prod because prompt behavior depends
on real SOUL/USER/MEMORY content — an empty tree is not representative.

**The workflow it produces:**

```
edit in /app/jarvis_staging/code
  → systemctl start jarvis-staging → chat with the staging bot → stop it
  → merge to main
  → cd /app/jarvis_code && ./scripts/deploy.sh → you restart jarvis
```

You get a second bot in Telegram. Testing means talking to the one that isn't Jarvis.

---

## Slices

Each slice ships alone, reverts alone, and leaves you better off than before. Risk climbs
monotonically — stop after any of them and nothing is half-done.

### Slice 0 — land `fix/heartbeat-cost`

Nothing starts until prod is on a known-good `main`. That branch carries an active token-leak
fix whose "after" readings are still pending (its §5). Carrying it across the slice-4 cutover
would be worse than finishing it first.

### Slice 1 — prod says what it is running

`main.py` logs git provenance at startup via `_running_provenance()` (fields) + `_provenance_block()`
(formatting). Two emissions: a **compact one-liner right after `Starting Jarvis...`** (so a boot
that crashes before "online" still says what code it was), and the **full multi-line block as the
LAST startup line** (so `journalctl -n`/`systemctl status` show it in the tail without scrolling
past boot noise). The block:

```
Running code:
    branch : main @ 5dc1842
    commit : feat(staging): log git provenance at startup — 2026-07-21
    deploy : none
```

The commit subject + date make it human-readable ("is this the fix I meant to deploy?"), not just
a SHA. Every `git` call is wrapped and degrades to `unknown` rather than blocking startup.

*Scope note:* **instance name and memory/data roots are deliberately not here** — they are
`config.py` concepts (slice 2); `heartbeat/webhook/reminders` state joins at slice 3. The block is
designed to grow a row per slice. The `deploy` field reads `none` until slice 4 creates `deploy-*`
tags; it is scaffolding that lights up then (and becomes the log-loudly warning). The branch/SHA
carry slice 1's value: the 2026-07-20 incident (prod tree on the wrong branch) would have been
visible immediately.

*Terminal note:* `systemctl restart` cannot stream a service's own output to the terminal (systemd
routes it to the journal), so "see it on restart" means peeking the tail. A `jrestart` shell
function (`systemctl restart` + `journalctl … | grep -A3 "Running code:"`) prints the block in one
command.

*Delivers:* `journalctl` always answers "what is prod actually running?"
*Risk:* essentially zero — pure logging, no config, no new failure mode, and git failure can't
block boot. Deliberately first, so the process gets rehearsed on something that cannot break.
*Revert:* delete the two helpers and their log lines.

### Slice 2 — local testing stops touching prod

`config.py` reads `os.environ` at import and exposes plain constants; modules keep their local
names and derive them (`MEMORY_DIR = config.MEMORY_DIR`), so call sites don't churn. It
`makedirs(exist_ok=True)` the subtrees so a fresh tree self-initializes.

| Env var | Default (= today) |
|---|---|
| `JARVIS_INSTANCE` | `prod` |
| `JARVIS_MEMORY_DIR` | `/app/jarvis_memory` |
| `JARVIS_DATA_DIR` | `/app/jarvis_data` |

**Inventory** (verified 2026-07-20):

| File | Constant |
|---|---|
| `agent.py:155` | `DB_PATH` (threads.sqlite) |
| `agent.py:171` | `_MEMORY_DIR` |
| `agent.py:243,287` | inline `chat_log` / `notif_log` |
| `tools/core/memory.py:20` | `MEMORY_DIR` — sandbox root; the `startswith` check derives from it |
| `tools/core/history.py:14` | `_LOG_DIR` — `observability/telemetry.py` imports it, so turns/tool_calls follow for free |
| `tools/core/scheduling.py:15` | `EVENTS_PATH` |
| `heartbeat_state.py:31,32` | `HEARTBEAT_PATH`, `STATE_DIR` |
| `tools/fitness/fitness_tools.py:12` | `DB_PATH` |
| `scripts/prune_checkpoints.py:21-23` | threads.sqlite paths |

Untouched by design: user-facing error strings naming `/app/secrets/.env` (cosmetic); `prompts/`
and `media_cache` (already checkout-relative).

**[new] Boot guard.** `config.py` refuses to start when `JARVIS_INSTANCE != "prod"` and
`MEMORY_DIR` resolves to the production path. The reason is specific: `tools/core/memory.py:12-18`
documents the memory write lock as a process-wide `threading.Lock`, correct *only* while memory
has a single writer **process**. A staging unit missing one `Environment=` line violates that
silently — a `threading.Lock` raises nothing when it fails to protect. Full `fcntl.flock`
migration stays out of scope; the guard makes it unnecessary until we deliberately want two
processes on one tree.

**[new] REPL defaults to scratch.** `agent.py`'s `__main__` points at a scratch tree unless the
caller exported real dirs, so `python agent.py` stops being a production write.

*Verify:* import every module with no `JARVIS_*` set, assert each constant equals its previous
literal · assembled prompts byte-identical for both scopes · REPL against a scratch dir leaves
prod mtimes unchanged.
*Revert:* one commit; defaults made it a no-op for prod anyway.

### Slice 3 — the staging bot exists

| Env var | Default | Effect when off |
|---|---|---|
| `JARVIS_ENV_FILE` | `/app/secrets/.env` | — |
| `JARVIS_WEBHOOK_PORT` | `8000` | staging uses `8001` |
| `JARVIS_WEBHOOK_ENABLED` | `true` | don't construct/serve uvicorn |
| `JARVIS_HEARTBEAT_ENABLED` | `true` | skip the `IntervalTrigger` job (`main.py:131`); scheduler still starts, reminders still schedulable |
| `JARVIS_REMINDERS_ENABLED` | `true` | **[new]** skip restore of pending events (`main.py:147`) |

**Bootstrap order matters.** `config.py` resolves `JARVIS_ENV_FILE` from the *process*
environment (systemd `Environment=`), then `load_dotenv`s it — an env file cannot name its own
path. All `JARVIS_*` vars are process-env, not `.env` content: environment shape belongs to the
unit, secrets belong to the file.

**[new] Separate heartbeat and reminder flags**, not one `PROACTIVE` switch. Testing a heartbeat
tick against seeded memory is a wanted capability; re-firing the owner's real reminders never is.

**Provisioning.** Dirs under `/app/jarvis_staging/`; clone + venv; `cp /app/secrets/.env
/app/secrets/staging.env` with `TELEGRAM_BOT_TOKEN` swapped and `chmod 600`; `rsync -a --exclude
'threads.sqlite*' /app/jarvis_memory/ /app/jarvis_staging/memory/`; a
`/etc/systemd/system/jarvis-staging.service` that is the prod unit plus:

```ini
Environment="JARVIS_INSTANCE=staging"
Environment="JARVIS_ENV_FILE=/app/secrets/staging.env"
Environment="JARVIS_MEMORY_DIR=/app/jarvis_staging/memory"
Environment="JARVIS_DATA_DIR=/app/jarvis_staging/data"
Environment="JARVIS_HEARTBEAT_ENABLED=false"
Environment="JARVIS_REMINDERS_ENABLED=false"
Environment="JARVIS_WEBHOOK_ENABLED=false"
```

**Not `systemctl enable`d** — started on demand, so a stopped staging cannot surprise anyone.

*Verify:* clean boot showing the slice-1 identity line · chat with the staging bot; memory tools
list the seeded files · prod `journalctl` quiet and `find /app/jarvis_memory /app/jarvis_data
-newer <marker>` empty after a staging conversation · `write_memory` in staging lands only under
`/app/jarvis_staging/memory` · a destructive tool's inline keyboard arrives in the *staging* chat.
*Revert:* `systemctl stop jarvis-staging`; the staging tree is inert data.

### Slice 4 — prod becomes deploy-only

`deploy.sh` (run in the prod checkout): assert a clean tree → `git fetch` → tag the current
commit `deploy-YYYY-MM-DD-N` → `git checkout main && git pull --ff-only` → print the new SHA and
the restart hand-off. **The script never restarts** — that stays with the owner.
`rollback.sh`: list recent `deploy-*` tags → checkout the chosen one → same hand-off.

The tag is the point: rollback becomes "pick the last one" rather than git archaeology at the
worst possible moment.

**The migration is the risky part** and deserves more than the one bullet the 2026-07-10 plan
gave it: development moves to `/app/jarvis_staging/code`, so **Claude Code sessions start
there** and `CLAUDE.md` must say so; in-flight branches (`fix/heartbeat-cost` — landed in slice
0 — plus `feat/context-handling`, `docs/app-channel-plan`) need somewhere to live; and
`/app/jarvis_code` goes onto `main` and is thereafter touched only by these two scripts.

*Verify:* dry-run deploy on an already-current main is a clean no-op, tag created once · rehearse
a rollback and confirm `git describe` before restarting.
*Revert:* it is convention plus two scripts — reverting is resuming in-place edits.

---

## What staging does not isolate

Once `MEMORY_DIR` and `DATA_DIR` are isolated, tools sort into four groups and only one is a
problem:

| Group | Examples | In staging |
|---|---|---|
| Reads of external services | `web_search`, Arbox fetches, Radarr/Sonarr *list* | Harmless |
| Writes to local state | fitness DB, memory files, `scheduled_events.json` | **Isolated** — safe by construction |
| Messages to the owner | reminders, notifications | Go to the **staging bot** |
| **Writes to external services** | Radarr/Sonarr add + delete-with-files, Jellyseerr requests | **The real risk** — same live servers from any instance |

That last row is narrow and already confirmation-gated: the inline keyboard arrives in the
staging chat, so damage requires actively tapping Confirm on a bot you know is the test one.
Keys stay shared for now (open question 2).

**Correction to the 2026-07-10 analysis and to earlier drafts of this plan.**
`fetch_upcoming_arbox_classes` was described as a hazard because it upserts and purges rather
than reads. Verified 2026-07-20: `_arbox_post` is only ever used for *queries*
(`/schedule/betweenDates`, `/logbook/workouts`, `/schedule/weekly`) — nothing writes to the
remote Arbox service, and the upsert/purge touches only the local fitness DB, which `DATA_DIR`
isolates. Staging computes its own notice from its own DB; prod is unaffected.

The Gemini key is shared; staging draws the same quota (negligible at flash pricing).

**Heartbeat in staging**, when deliberately enabled, reads external services, writes staging's
own state, and messages the staging bot. The cost is duplicate API calls and Gemini spend, not
corruption. Staging inherits all 8 tasks with the memory seed — trim the staging copy by hand
when testing one; nothing propagates back.

**The webhook** is the FastAPI server the Arr services POST to when media finishes. They point
at prod's `:8000` only, so staging with the webhook off simply never receives any. Drive it with
`scripts/test_webhooks.py` against `:8001` rather than repointing a real Arr service, so prod
keeps receiving its webhooks throughout.

---

## What this does not solve

Staging enables *manual* behavior testing — chatting with the staging bot. It automates nothing.
`docs/plans/TESTING_AND_FEEDBACK_LOOP_PLAN.md` Phases 3–4 (pytest + fixtures, and the tests that
matter) are the natural follow-on and are not duplicated here.

---

## Open questions

1. **Auto-deploy.** A pull-based systemd timer (`git fetch`; if `main` moved, pull + restart)
   needs no inbound networking and is ~20 lines. Two things block it today: restarting drops
   in-flight turns and graceful SIGTERM shutdown is an open follow-up (#33), and an unattended
   deploy has nobody to watch `journalctl` and send a test message. **Revisit once #33 lands.**
   Cheap interim: a timer that compares deployed HEAD against `origin/main` and pings through
   Jarvis — *"main is 3 commits ahead of what I'm running"* — the reminder without the risk.
2. **Media/external keys in `staging.env`.** Shared for now, so media flows are testable. Prune
   per service if it ever bites; graceful degradation already returns "not configured".
3. **Memory re-seed policy** — manual rsync vs a `reseed_staging.sh`. Lean: manual first.
4. **Staging heartbeat guard.** Nothing in code prevents enabling staging's heartbeat while
   prod's runs — duplicate task execution and double messages. The default-off flag and the
   slice-1 identity line are the only guards, deliberately.
5. **A second hub for the app channel.** Not blocking this plan, but it surfaces here: the app
   channel is *outbound* (long-poll), so unlike the webhook it binds no port and there is no
   collision. But the hub is one-bot-one-user, so two agents polling it with one token would
   fight over updates. During step-3 development this is moot — `APP_HUB_URL` will be unset in
   prod, so only staging polls. It bites *after* the channel deploys to prod. Raise with the app
   author: does the hub support a second bot token, or is a staging hub instance the answer?
