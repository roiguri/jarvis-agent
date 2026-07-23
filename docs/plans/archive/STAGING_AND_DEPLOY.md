# Staging environment & deploy discipline

**Status:** ✅ **COMPLETE** — slices 0–4 landed and verified live 2026-07-23 (rollback rehearsed, both paths work); archived.
**Date:** started 2026-07-20, completed 2026-07-23.
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
- [x] Finish `fix/heartbeat-cost` verifications (its own plan, §5)
- [x] Merge to `main`; **restart prod**; confirm the tick behaves

### Slice 1 — prod says what it is running
- [x] `main.py`: log git provenance at startup — a multi-line `Running code:` block (branch, sha,
      commit subject + date, deploy-tag) as the last startup line, plus a compact early one-liner
- [x] **Restart prod**, read the block in `journalctl`/`systemctl status` (expect `branch : main …`)

  *(the `root :` row joins the block at slice 2; heartbeat/webhook/reminders at slice 3)*

### Slice 2 — local testing stops touching prod

Split into six sub-slices. Each ships and reverts alone; **2a–2d are provable no-ops in prod**,
because under `JARVIS_ROOT=/app` every derived path equals the literal it replaces.

**2·0 — snapshot prod state before touching a path** *(do this once, before 2c)*

- [x] `bash deploy/backup_state.sh pre-slice2` → `/app/backups/state-pre-slice2-<ts>.tar.gz` of `/app/jarvis_memory` + `/app/jarvis_data` (see [Backup & rollback](#backup--rollback))
- [x] Confirm the tarball lists SOUL/USER/MEMORY + `threads.sqlite` before proceeding
- [x] *Revert path for all of 2c–2d:* `git revert` the commit **and** `backup_state.sh --restore` if any write went to the wrong tree *(n/a — 2c/2d landed clean under `JARVIS_ROOT=/app`; no wrong-tree write, so no restore needed)*

**2a — the unit declares the instance** *(ops only, no code)*

- [x] **Add four `Environment=` lines to `jarvis.service`** (`JARVIS_ROOT` + the three slice-3 toggles); `daemon-reload`
- [x] Commit `deploy/jarvis.service`; `DEVELOPMENT.md` points at it instead of an inline copy *(staging unit lands in slice 3)*
- [x] Verify: `systemctl show jarvis -p Environment` lists them; **nothing else changes** (no code reads them yet) *(also fixed StartLimit\* [Service]→[Unit] — was silently ignored)*

**2b — the process knows its root**

- [x] `config.py`: read `JARVIS_ROOT`, **refuse to start if unset**; derive the subpaths; no project imports, no lock, subtree `makedirs` only
- [x] Both `load_dotenv` calls (`agent.py:148`, `main.py:40`) → `config.ENV_FILE`
- [x] Provenance block gains `root :` / `instance :` rows
- [x] **Restart prod** — the block reads `root : /app`. This is the step that proves 2a's lines apply

**2c — the memory tree** *(everything under `/app/jarvis_memory`)*

- [x] `agent.py:171` `_MEMORY_DIR` (`:172-175` follow), `agent.py:155` `DB_PATH`, `tools/core/memory.py:20`, `heartbeat_state.py:31` *(+ dropped the dead `tools/core/__init__.py` re-export; `/memory`,`/heartbeat` handlers read `config.MEMORY_DIR`)*
- [x] Delete the import-time `makedirs` at `agent.py:156`
- [x] **Restart prod**, send a message, have Jarvis write a memory file — lands in `/app/jarvis_memory` as before; history intact (same DB)

**2d — the data tree** *(everything under `/app/jarvis_data`)*

- [x] `tools/core/history.py:14` (+ **delete `:15`**), `tools/core/scheduling.py:15`, `heartbeat_state.py:32`, `tools/fitness/fitness_tools.py:12` *(+ `agent.py` inline chat/notif logs)*
- [x] **Restart prod**, watch one heartbeat tick — `turns.jsonl` and `state.json` still update in place *(both verified appending in place under `/app/jarvis_data`; `state.json` stamping confirmed on the hourly ticks)*
- [x] Run the scratch-root assertion by hand; it must now pass *(full-module scan: zero `/app/jarvis_memory`/`/app/jarvis_data` leak)*

**2e — dev tooling and the regression net** *(touches no production runtime)*

- [x] **Delete** the local REPL — `agent.py`'s `__main__` block + the README entry (superseded by the staging bot). *Revised 2026-07-23: deleted outright, not moved to `scripts/repl.py` — it was unused.*
- [x] **Delete** `scripts/prune_checkpoints.py` (obsolete — `PruningSqliteSaver` self-prunes on every write); drop the dead `.gitignore:5` line
- [x] Land the scratch-root assertion as **`scripts/ci/check_paths.py`** (standalone; exits non-zero on any hardcoded prod-path leak; verified it catches an injected leak). Its `sys.modules` guard is the one the REPL would have carried. Wired into commit/merge/deploy gates at slice 4 — see [Regression gate (CI)](#regression-gate-ci)
- [x] Reword `tools/core/memory.py` single-writer comment: invariant held **procedurally**; staging (own root) and a second channel (same process) are *not* triggers — heartbeat-split-to-own-process is

### Slice 3 — the staging bot exists
- [x] Toggles read from the unit (**default off**; prod opts in at 2a), parsed by one shared `_env_bool` that rejects garbage loudly *(landed in 3A)*
- [x] Effective toggle states join the provenance block *(the `proactive :` row)*
- [x] `JARVIS_WEBHOOK_PORT` — plain override, default `8000`
- [x] **Create the staging bot** via BotFather; hand over the token
- [x] Dirs `/app/jarvis_staging/{code,jarvis_memory,jarvis_data,secrets}`; clone; venv
- [x] `/app/jarvis_staging/secrets/.env` = prod copy + staging bot token; dir `chmod 700`, file `600`
- [x] `scripts/check_env.sh` — key-set diff (never values) across `.env.example` and both instances
- [x] Seed memory *(via `tar --exclude 'threads.sqlite*'` — `rsync` isn't installed on this host)*; seed `heartbeat/state.json` alongside it
- [x] `jarvis-staging.service` — own `ExecStart`/`WorkingDirectory`, `JARVIS_ROOT=/app/jarvis_staging`, no toggle lines; not `enable`d
- [x] **On its first boot, read `root :` before anything else** — this is what replaces the dropped lock *(read `root : /app/jarvis_staging` before typing anything)*
- [x] **Start staging**, chat with the staging bot
- [x] Verify isolation by named file, not a blanket `find` *(staging note present only under `/app/jarvis_staging`; prod SOUL/USER/MEMORY mtimes unchanged; staging has its own `threads.sqlite`)*

### Slice 4 — prod becomes deploy-only

Split: **4a is useful on today's single-tree layout** and reverts by deleting two scripts; 4b is
the one-way cutover. Don't bundle them.

**4a — the deploy scripts**

- [x] `deploy/backup_state.sh` — `<label>` / `--restore <tarball>` / `--prune`; shared by 2·0, `deploy.sh`, and manual use (see [Backup & rollback](#backup--rollback)) *(built early in 2·0)*
- [x] `deploy/deploy.sh`: clean-tree check → **`backup_state.sh` keyed to the tag-to-be** → `fetch` → `pull --ff-only origin main` → **tag the incoming commit** `deploy-YYYY-MM-DD-N` (push it) → sync deps if `requirements.txt` moved → `JARVIS_ROOT=/app` smoke check → assert unit still declares `JARVIS_ROOT` → `backup_state.sh --prune` → print hand-off
- [x] `deploy/rollback.sh`: list `deploy-*` → checkout → **write a rollback marker** → if the target's commit flags a format change, `backup_state.sh --restore` its tarball → hand-off
- [x] `deploy.sh` refuses to run from a rolled-back/detached tree without `--force`
- [x] Provenance block gains a loud row when HEAD is detached or the tree is rolled back
- [x] Dry run: deploy on an already-current main = clean no-op, tag created once *(exercised live — `deploy-2026-07-23-{1,2,3}` tags + matching state snapshots)*
- [x] **Rehearse a rollback before you need one** — both a code-only rollback and a `--restore` *(rehearsed 2026-07-23; both paths work)*
- [x] **Regression gate (CI)** — `.githooks/pre-commit` (via `core.hooksPath`) + a GitHub Actions workflow running `scripts/ci/check_paths.py` + a branch-protection rule on `main` + fold the check into `deploy.sh`'s smoke check (see [Regression gate (CI)](#regression-gate-ci))

**4b — development moves out of prod**

- [x] Migrate in-flight branches (`feat/context-handling`, `docs/app-channel-plan`) to `/app/jarvis_staging/code` *(development now runs from the staging tree; prod checkout sits on `main`)*
- [x] Copy `.claude/` and migrate the Claude Code project dir (see below); put `/app/jarvis_code` on `main` *(`.claude/` present under the staging tree)*
- [x] Update `CLAUDE.md` (sessions start in staging) + `DEVELOPMENT.md` §Development Workflow 1 *(commit c7bb3b2, PR #39)*

---

## What makes today "straight to production"

1. **Working tree = live service.** `ExecStart` runs `/app/jarvis_code/main.py`; development
   happens in the same tree. At the 2026-07-10 review prod was running a feature branch — the
   norm, not an accident. See the 2026-07-20 incident above.
2. **Every state path is a hardcoded constant.** A second instance today would share memory,
   `threads.sqlite`, logs, and heartbeat stamps — and nothing would say so: the memory write lock
   is a process-wide `threading.Lock` (`tools/core/memory.py:12-18`), which raises nothing when it
   fails to protect across processes.
3. **The REPL is not isolated.** `agent.py`'s `__main__` uses thread `local_dev_test_01` — a
   separate *conversation*, but the same DB, memory, logs and live tools. A "local test" calling
   `write_memory` mutates production memory. That thread is in prod `threads.sqlite` now.
4. **Two collision points:** the webhook binds `:8000` (`main.py:181`), and a second heartbeat
   scheduler would double-run every task.
5. **Reminders restore on boot** (`main.py:205`) — a staging start re-fires the owner's real
   events. **[new]** — the 2026-07-10 plan noted this only as a standing risk.
6. **Already safe:** `prompts/` (`agent.py:176`) and the Telegram media cache
   (`media_cache.py:15`) resolve relative to `__file__`, so a staging checkout reads its own.

---

## Target shape

Two instances with an **identical relative layout**, each rooted at a directory its own systemd
unit declares (slice 2). One line per unit; every other path follows from it.

```
/app/                             PROD root  (JARVIS_ROOT=/app)
├── jarvis_code/                    code — deploy-only, tagged main
├── jarvis_memory/                  memory
├── jarvis_data/                    data
└── secrets/.env                    secrets

/app/jarvis_staging/              STAGING root  (JARVIS_ROOT=/app/jarvis_staging)
├── code/                           DEV + STAGING code — feature branches; own venv
├── jarvis_memory/                  seeded from a prod snapshot
├── jarvis_data/                    starts empty (except seeded heartbeat stamps)
└── secrets/.env                    prod copy + staging bot token; dir 0700
```

Each unit declares its own `JARVIS_ROOT` (`/app`, `/app/jarvis_staging`) and every other path
derives from it. Adding a third instance is `git clone` into a new root plus one `Environment=`
line — and a checkout with no declared root refuses to start rather than guessing.

Same host (an LXC clone was considered and rejected 2026-07-10: Proxmox-level work, duplicated
resources, two-container drift). Staging seeds memory from prod because prompt behavior depends
on real SOUL/USER/MEMORY content — an empty tree is not representative.

**The workflow it produces:**

```
edit in /app/jarvis_staging/code
  → systemctl start jarvis-staging → chat with the staging bot → stop it
  → merge to main, push to origin
  → cd /app/jarvis_code && ./deploy/deploy.sh   (pull, tag, deps, smoke check)
  → you restart jarvis
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

*Scope note:* **the instance root is deliberately not here** — it is a `config.py` concept
(slice 2b, where the block gains `root :` and `instance :`, and where those rows are how you
confirm 2a's unit lines actually applied). The proactive-toggle row joins at slice 3. The block is
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

**One declared fact, everything else derived from it.** An earlier draft introduced three env vars
(`JARVIS_INSTANCE`, `JARVIS_MEMORY_DIR`, `JARVIS_DATA_DIR`) plus a boot guard whose only job was
to check they agreed; slice 3 added four more. A second draft went the other way and *derived*
the root from the checkout's own location (`dirname(<checkout>)`), configuring nothing. Both were
wrong, in opposite directions. What ships is one declaration:

```ini
Environment="JARVIS_ROOT=/app"                     # prod's unit
Environment="JARVIS_ROOT=/app/jarvis_staging"      # staging's unit
```

```python
ROOT       = normpath(environ["JARVIS_ROOT"])   # required — no default, no fallback
MEMORY_DIR = ROOT/jarvis_memory
DATA_DIR   = ROOT/jarvis_data
ENV_FILE   = ROOT/secrets/.env
INSTANCE   = basename(ROOT)                     # a log label only — "app" for /app, "jarvis_staging" for /app/jarvis_staging
```

Under `JARVIS_ROOT=/app` every path equals today's literal exactly, so 2a–2d change nothing
observable in prod. `normpath` is not cosmetic: `/app/` would otherwise make `basename` return
`""`.

**Why declared beats derived.** Deriving from the checkout's location always produces an answer,
including for checkouts nobody thought about. `cp -r /app/jarvis_code /app/jarvis_code.bak`, a
second clone at `/app/jarvis_test`, and `git worktree add ../jarvis-review` all sit one level
under `/app`, so all three would compute prod's paths — prod memory, prod `threads.sqlite`, prod
token — while believing they were isolated. That is the 2026-07-20 incident (plan header) one
directory level up, and it is silent. `/app` is also the Docker/buildpack default application
path, so any future containerization would land at `/app/jarvis_code` and claim prod.

Requiring the declaration inverts the default: an undeclared checkout **refuses to start** instead
of guessing the most dangerous answer. A backup copy, a worktree and a container are all inert
until someone deliberately names a root. That also retires guards the derived design needed —
running `prune_checkpoints.py` from a worktree already requires typing `JARVIS_ROOT=/app`, which
*is* the conscious act a `--yes-prod` flag was simulating.

The cost is honest and small: **prod does not start without that line**, so 2a lands before 2b and
the plan is ordered that way. `Environment=` lines are inert to code that doesn't read them, which
is what makes 2a a zero-risk ops step rather than a coupled change.

**Why this is not the seven env vars again.** Those were four restatements of *one* fact (which
instance am I) that could disagree with each other, which is why they needed a guard. `JARVIS_ROOT`
is one fact with nowhere to disagree — every path derives from it. Derive *layout* (a convention);
declare *identity* (a decision).

**Why `JARVIS_ROOT` cannot live in `.env`.** `ENV_FILE` is `ROOT/secrets/.env`, so the process
would have to read the file to learn where the file is. It must arrive from the process
environment — the unit, or the shell for one-off runs. The three slice-3 toggles *could* live in
`.env` and deliberately don't: `.env` is the file provisioning **copies** between instances
(prod's keys are wanted in staging), so a toggle stored there travels with the copy and staging
inherits "be proactive" silently. Rule: `.env` holds what is the same across instances; the unit
holds what differs.

**The unit is now configuration, so it belongs in the repo.** Commit `deploy/jarvis.service` and
`deploy/jarvis-staging.service`; `DEVELOPMENT.md` currently reproduces the unit as an inline
snippet that can drift from what is installed. Slice 4's `deploy.sh` can diff installed against
committed.

`config.py` imports nothing from the project (so it can be imported first, everywhere), takes no
locks, and `makedirs(exist_ok=True)` the **subtrees** — and `DATA_DIR/logs` explicitly, not just
`MEMORY_DIR`/`DATA_DIR`. `tools/core/history.py` is the one state writer with **no lazy
`makedirs`**: `_append_line` (`:50`) and `trim_log` (`:16`) just `open()` the path, where
`scheduling.py:34`, `heartbeat_state.py:365`, `memory.py:193` and `fitness_tools.py:51` all create
their dir on write. Today only the import-time `history.py:15` line creates the log dir, and slice 2
deletes it — so if `config` makes only the two top-level roots, a fresh instance (staging's
`jarvis_data` starts empty) throws `FileNotFoundError` on its **first** `append_chat_log`, breaking
slice 3's "chat with the staging bot" step. `config` must materialize `DATA_DIR/logs` up front. It
requires `ROOT` itself to already exist: a typo'd root must fail loudly, not silently
self-initialize an empty instance. The
derivation is a pure function of `(root, environ)` with the module constants sitting on top, so
both halves of the verification below are ordinary assertions rather than subprocess wrangling.

**Inventory** (verified 2026-07-21):

| File | Constant |
|---|---|
| `agent.py:155` | `DB_PATH` (threads.sqlite) |
| `agent.py:171` | `_MEMORY_DIR` — `:172-175` (`_DAILY_DIR`, `_SOUL_PATH`, `_USER_PATH`, `_HEARTBEAT_MD_PATH`) derive from it and follow for free |
| `agent.py:243,287` | inline `chat_log` / `notif_log` |
| `agent.py:148` + `main.py:40` | `load_dotenv("/app/secrets/.env")` — both become `config.ENV_FILE` |
| `tools/core/memory.py:20` | `MEMORY_DIR` — sandbox root; the `startswith` check derives from it |
| `tools/core/history.py:14` | `_LOG_DIR` — and **delete `:15`**, whose `makedirs` runs at import; **`config` must then create `DATA_DIR/logs` itself** — `history.py`'s write path never does (see below). `observability/telemetry.py:22` imports `_LOG_DIR`, so turns/tool_calls follow for free |
| `tools/core/scheduling.py:15` | `EVENTS_PATH` |
| `heartbeat_state.py:31,32` | `HEARTBEAT_PATH`, `STATE_DIR` |
| `tools/fitness/fitness_tools.py:12` | `DB_PATH` |
| ~~`scripts/prune_checkpoints.py`~~ | **Deleted in 2e** — obsolete now that `PruningSqliteSaver` self-prunes on every write; deleting it drops the prod-hardcoded footgun rather than repointing it |
| `scripts/trace.py` | same: reads the log dir |

Follows for free but worth knowing it is a live call site: `gateway/commands/handlers.py:138,171`
(`/memory`, `/heartbeat` list `MEMORY_DIR`).

Untouched by design: user-facing error strings naming `/app/secrets/.env` (cosmetic); `prompts/`
(`agent.py:176`) and `media_cache.py:15` (already checkout-relative, so a staging checkout reads
its own).

**Import-order rule.** `import config` is the **first project import** in every entrypoint —
`main.py`, `agent.py`, `heartbeat.py`, `scripts/repl.py`, every `scripts/*.py`. This is not
stylistic: several modules read `os.environ` at import time (`gateway/webhook/notifier.py:31,33`,
`tools/fitness/fitness_tools.py:165`), and today `main.py` only works by luck — its line 11
imports `agent`, which `load_dotenv`s at line 148, before `gateway.webhook.notifier` is imported
at line 17. One reordered import and Jellyfin URLs quietly fall back to defaults. Slice 2 adds a
check that no module reads `os.environ` at import before `config`.

**[new] No single-writer lock — considered, deliberately not built.** Earlier drafts of this slice
added an `fcntl.flock` so a second process on one root would refuse to boot. It is dropped, and
the reasoning is recorded here so it is re-decided on evidence rather than re-litigated.

*What it would have guarded.* Two processes sharing a root, whatever the cause. The memory write
lock is a process-wide `threading.Lock` (`tools/core/memory.py:12-18`), correct only under a single
writer **process**; two processes interleave writes to the same file and raise nothing.

*Why procedure covers it here.* With `JARVIS_ROOT` required, the realistic path to a duplicate is
one mistake made once: copying prod's unit to create staging's and not changing the root. That
happens at slice-3 provisioning and is caught by that slice's **first** verification — staging's
clean boot must print `root : /app/jarvis_staging`, so a mis-copied unit announces itself in the
journal on its first start, before it has done anything. This is a two-instance, single-user, one
-operator system; the mistake is not recurring, and the check is one line of reading.

*Why it is not merely deferred — it is on a collision course.* `memory.py:15` names the case that
would genuinely need locking: *"if a second memory-writing process is ever introduced (e.g.
heartbeat split out, multi-worker)"*. In that world two processes share a root **on purpose**, and
a boot lock forbids exactly the thing being built. The correct answer there is per-write `flock`
on the memory files, which is a different and larger change. A boot lock is not a step toward it;
it is something you would delete on the way.

*What is accepted.* If the unit is mis-copied *and* the boot log goes unread, staging writes prod's
memory. Partly self-announcing — both processes would 409 each other on the shared Telegram token —
but not reliably, since the dangerous window is a staging start while prod is stopped.

*Revisit if:* the heartbeat (or anything else) splits into its own process; a third instance
appears; or provisioning stops being a once-per-instance manual act. The first of those needs
per-write locking, not this.

*Instead, this slice does the cheap durable thing:* reword `tools/core/memory.py:14-18` so the
invariant reads as **procedurally** held — one service unit per root, verified at provisioning —
and names what would change the answer. The comment currently reads as a TODO; it should read as
a decision.

**[new] The REPL — revised 2026-07-23: deleted outright, not moved.** It was unused and the staging
bot supersedes it, so `agent.py`'s `__main__` block and its README entry are removed and no
`scripts/repl.py` is created; `prune_checkpoints.py` is likewise deleted (obsolete — see the
inventory). The `sys.modules` guard the REPL would have needed lives in `scripts/ci/check_paths.py`
instead. The original analysis is retained below as the rationale for *why* relocating would not have
been trivial. "`agent.py`'s `__main__` defaults to a scratch
tree" is unimplementable where the earlier draft put it: `agent.py` binds `DB_PATH` at line 155
and `_MEMORY_DIR` at 171 at **module scope**, while `if __name__ == "__main__":` runs at line 765.
Anything the REPL sets is too late. `scripts/repl.py` sets `JARVIS_ROOT` *before* `import agent`.
Three details that make it real rather than decorative:

- **Delete `agent.py:764-780`.** If the block survives, `python3 agent.py` still works, still binds
  the prod root, and is still the documented command at `DEVELOPMENT.md:166-170` — which changes
  in the same commit.
- `assert "config" not in sys.modules and "agent" not in sys.modules` at the top, before setting
  the env. Otherwise the first future edit that adds a top-level `from agent import ask_jarvis`
  silently binds prod while the file still *looks* correct.
- it defaults to a scratch root and only reaches prod when `JARVIS_ROOT=/app` is named explicitly.
  No `--yes-prod` flag is needed: with the root required, naming prod *is* the deliberate act.
  The same holds for `prune_checkpoints.py`, which rewrites `threads.sqlite`.

**[new] No module re-exports a config value as its own module-scope constant** — call sites read
`config.MEMORY_DIR`. The late-binding pattern is already in the code (`memory.py:40` reads it per
call; `handlers.py:138` imports inside the function), and it is the difference between a test
fixture being one `setattr` and being a subprocess. `from config import MEMORY_DIR` at module
scope destroys that seam. Correspondingly, delete the import-time `makedirs` at
`tools/core/history.py:15` and `agent.py:156`: `config` now creates the subtrees, and those two
lines are exactly what materializes a stray `/app/jarvis_data/logs/` when something sets the root
a moment too late.

*Verify — both directions, and the scratch one is load-bearing.* The prod-default check (no env
set → every constant equals its old literal) is weak: a literal you **forgot** to repoint also
equals its previous literal, so every miss passes. The real assertion runs the other way — with
`JARVIS_ROOT=/tmp/scratch`, import everything and assert **no** module-level constant in
`sys.modules` contains `/app/jarvis_memory` or `/app/jarvis_data`. That fails on a missed site by
construction rather than by hoping a test exercises it. Two things it needs:

- **a scratch `secrets/.env` with a dummy `GOOGLE_API_KEY`** — `agent.py:148-152` does
  `load_dotenv` then raises at import when the key is absent, so without the fixture the
  load-bearing test dies before asserting anything, and the failure looks enough like a config bug
  that someone "fixes" it by pointing at prod's `.env`;
- **a home in CI / the pytest suite, not a slice-2 checkbox.** It verifies the code as of this
  commit; a hardcoded path added in six months regresses it silently. Both directions collapse to
  ordinary assertions on `derive()` in one process — only the wiring claim needs a subprocess.
  (`importlib.reload(agent)` is not an option: it re-runs `ChatGoogleGenerativeAI(...)` at
  `agent.py:159` and rebuilds the graph.)

Plus: prompts byte-identical for both scopes **under a frozen clock** (`build_system_prompt`
injects `[Current time]` and today's daily log, so an unfrozen comparison is flaky, not
reassuring); REPL against a scratch root leaves prod mtimes unchanged; a second process on the
same root is refused, and the journal names the holder.

*Also here:* `.gitignore:5` (`/app/jarvis_memory/threads.sqlite`) is dead — a leading-slash
pattern is repo-root-relative, so it has never matched. The DB is outside the checkout in every
instance anyway. Drop the line.

*Revert:* one commit; the derivation made it a no-op for prod anyway.

### Slice 3 — the staging bot exists

**[new] Slice 2's derivation absorbed most of this slice's config.** `JARVIS_ENV_FILE` is gone —
the env file is `ROOT/secrets/.env`. What remains is three behavior toggles and one port:

| Env var | Default | Effect when off |
|---|---|---|
| `JARVIS_WEBHOOK_ENABLED` | **off** | don't construct/serve uvicorn |
| `JARVIS_HEARTBEAT_ENABLED` | **off** | skip the `IntervalTrigger` job (`main.py:189`); scheduler still starts, reminders still schedulable |
| `JARVIS_REMINDERS_ENABLED` | **off** | skip restore of pending events (`main.py:205`) |
| `JARVIS_WEBHOOK_PORT` | `8000` | only consulted when the webhook is on — staging sets `8001` on the runs where it deliberately enables it |

**[new] Off everywhere by default; prod opts in.** The three lines were added to prod's unit back
in **2a**, so this slice only teaches the code to read them. Two properties fall out. Any missing
or misapplied configuration produces an **inert** instance, never one that messages the owner —
the failure direction that matters. And the `"prod"` magic string disappears from the codebase:
behavior no longer keys off what a directory is *named*, so relocating prod (`/srv/jarvis`, a new
host, a container) cannot silently switch its heartbeat off. This settles open question 4.

**[new] Parse the toggles through one shared `_env_bool(name, default)`** that rejects
unrecognized values loudly. Three ad-hoc `os.environ.get(...)` reads in `main.py` would treat
`"false"` and `"0"` as true — the failure being a staging instance that runs its heartbeat while
its unit says it shouldn't.

**[new] The port is a plain override, not derived.** An earlier draft derived it from the instance
name (prod `8000`, else `8001`), which is the clever version of a one-line config that works for
exactly two instances and collides on the third.

**[new] The provenance block carries the effective toggle states**, not just the paths. Off is now
the default, so the failure worth surfacing is a prod whose unit lines went missing: it starts
fine, answers chat, and quietly never runs a tick — and a heartbeat that never fires logs
identically to one with nothing due (`heartbeat.py` returns before the model call when the gate
says so). One startup line makes that visible instead of an absence you notice days later:

```
    root     : /app  (instance: app)
    proactive: heartbeat=on reminders=on webhook=on :8000
```

The label is `basename(ROOT)`, so prod reads `instance: app` and staging `instance: jarvis_staging`
— honest, and it deliberately keeps the `"prod"` magic string out of the code. `root :` already
carries the identity unambiguously; `instance :` is only a short handle for scanning logs.

**Separate heartbeat and reminder flags**, not one `PROACTIVE` switch. Testing a heartbeat tick
against seeded memory is a wanted capability; re-firing the owner's real reminders never is.

**Provisioning.** `/app/jarvis_staging/{code,jarvis_memory,jarvis_data,secrets}` — the subtree
names match prod's so both instances have an identical relative layout. Clone + venv;
`cp /app/secrets/.env /app/jarvis_staging/secrets/.env` with `TELEGRAM_BOT_TOKEN` swapped, dir
`chmod 700` and file `600` (the instance root becomes one permission boundary, which is why
secrets moved in-tree rather than staying at `/app/secrets/staging.env`); `rsync -a --exclude
'threads.sqlite*' /app/jarvis_memory/ /app/jarvis_staging/jarvis_memory/`.

**[new] `scripts/check_env.sh` ships with this slice**, not as an open question — slice 3 is what
creates the second `.env`, so it is where drift starts. It diffs **key sets** (never values)
across `.env.example` and both instances' files. The reason it can't wait: unset keys degrade
*gracefully* (`gateway/webhook/notifier.py:31,33` fall back to `jellyfin.local` /
`jellyfin.example.com`), so a drifted staging `.env` yields a plausible wrong answer rather than
an error. The app channel's three keys will be the first to drift.

**[new] Seed `heartbeat/state.json` with the memory.** `DATA_DIR` starts empty but the rsync
brings all 8 tasks, so the first enabled tick has *everything* due at once — one huge turn, real
external calls, real spend, a burst of notifications. Seed the stamps alongside the memory (or
stamp every task `last_run = now`), then trim the staging task list by hand when testing one.
Nothing propagates back.

The staging unit (committed as `deploy/jarvis-staging.service`) is prod's with its own
`ExecStart`/`WorkingDirectory`, `JARVIS_ROOT=/app/jarvis_staging`, and **no toggle lines** — add
one only for a run where you deliberately want that behavior. **Not `systemctl enable`d** —
started on demand, so a stopped staging cannot surprise anyone.

*Verify:* **first, before anything else — staging's clean boot prints `root : /app/jarvis_staging`
in the slice-1 block.** With no lock in the design (slice 2), this line is the whole guard against
a mis-copied unit pointing staging at prod's root; read it before typing anything into the staging
bot. Then: chat with the staging bot, memory tools list the seeded files · `write_memory` in
staging lands only under `/app/jarvis_staging/jarvis_memory` · a destructive tool's inline keyboard
arrives in the *staging* chat.

**[new] Isolation is verified by named file, not a blanket `find`.** An earlier draft asserted
`find /app/jarvis_memory /app/jarvis_data -newer <marker>` is empty after a staging conversation.
It never is: prod is running throughout, and every turn appends `chat_history.jsonl`,
`turns.jsonl` and `tool_calls.jsonl`, `threads.sqlite-wal` churns continuously, and the hourly
heartbeat writes `daily/daily_*.md` and `state.json`. That check either false-alarms or gets
waved away. Instead: assert positively that staging's writes landed under
`/app/jarvis_staging/**`, then run named negatives — the memory file written in staging does
**not** exist in prod; prod's `SOUL.md`/`USER.md`/`MEMORY.md` mtimes unchanged;
`/app/jarvis_staging/jarvis_memory/threads.sqlite` exists.

*Revert:* `systemctl stop jarvis-staging`; the staging tree is inert data.

### Slice 4 — prod becomes deploy-only

**Code flows through GitHub** (decided 2026-07-21): staging pushes `origin/main`, prod pulls from
`origin`. The off-box copy and PR review are worth more than the offline case; a GitHub outage
postpones a deploy, which is acceptable. Verified 2026-07-21 that the prod checkout already has
working SSH access to `origin` (`git ls-remote` succeeds), so **no deploy-key provisioning step is
needed**. Fallback if that ever changes: `git remote add staging /app/jarvis_staging/code` and pull
locally — no network, no keys.

#### 4a — the deploy scripts

`deploy.sh`, run in the prod checkout. **It never restarts** — that stays with the owner.

```
clean-tree check
  → snapshot memory + data to a tarball keyed to the tag-to-be
  → git fetch && git checkout main && git pull --ff-only origin main
  → tag the NEW commit  deploy-YYYY-MM-DD-N   (and push the tag)
  → if requirements.txt changed: venv/bin/pip install -r requirements.txt
  → smoke check: JARVIS_ROOT=/app venv/bin/python -c "import main"
  → assert the installed unit still declares JARVIS_ROOT (grep systemctl cat)
  → print the new SHA + restart hand-off
```

The unit-grep is the counterpart to the hardcoded `JARVIS_ROOT=/app` in the smoke check: the
prefix makes the import test pass, which means it *cannot* also detect a unit that lost its
`JARVIS_ROOT` line. `systemctl cat jarvis.service | grep -q 'JARVIS_ROOT='` (compared against the
committed `deploy/jarvis.service`) is what catches that — otherwise the deploy reports green and
the next restart is the fail-closed crash.

**[new] Tag the incoming commit, not the outgoing one.** An earlier draft tagged before the pull,
which put the tag on the commit being *left*. `_running_provenance()` reads
`git describe --tags --exact-match HEAD` (`main.py:98`) and reports it as `deploy` (`:105`) — so
after a successful deploy HEAD would be the freshly-pulled, untagged main and the field would read
`none` forever, populating only after a *rollback*. Tagging what you land on makes `deploy :` name
the live deploy, makes rollback "pick the previous tag", and turns `deploy : none` into a true
warning: *this tree was not deployed by the script*. Push the tags — with zero tags in the repo
today, this is where deploy history starts, and it should not live only on the prod box.

**[new] Dependency sync + a smoke check, because the failure is a silently dead prod.**
`DEVELOPMENT.md` documents deps as "pip install, then re-freeze", so a deploy that adds one pulls
cleanly and restarts into `ImportError`. With `Restart=always`, `RestartSec=30` and
`StartLimitBurst=5`, systemd gives up after ~2.5 min and leaves the unit `failed` — the only
symptom being a bot that stopped answering. `JARVIS_ROOT=/app venv/bin/python -c "import main"`
costs a second and exercises every import, the dotenv load, and the `GOOGLE_API_KEY` assert
(`agent.py:151`) without starting the bot. **The `JARVIS_ROOT=/app` prefix is mandatory here** —
after slice 2 `config.py` is fail-closed on an unset root, and a deploy shell (unlike the systemd
unit) carries no `Environment=`, so a bare `import main` would raise on *every* deploy, good or bad.
That prefix also means the check catches the one new slice-2 failure mode — a genuinely missing
root in the unit — only if you additionally assert the unit still sets it (below).

**[new] Snapshot state before the pull.** Rollback restores *code*; nothing restores data. A deploy
that changes an on-disk format — `state.json`, the `HEARTBEAT.md` grammar, checkpoint shape, a
JSONL field — leaves a rolled-back prod running old code against new data. `deploy.sh` therefore
calls `backup_state.sh` keyed to the tag-to-be *before* the pull, and the rule stands: **a deploy
that changes a persisted format is not rollback-safe by code alone, and its commit message must say
so** — that flag is what tells `rollback.sh` to `--restore` the matching tarball, not just check out
the old tag. The full mechanism, retention, and restore procedure live in
[Backup & rollback](#backup--rollback); this is just its deploy-time call site.

`rollback.sh`: list recent `deploy-*` tags → checkout the chosen one → **write a rollback marker**
→ same hand-off.

**[new] The marker exists because rollback leaves prod detached.** Without it the next `deploy.sh`
opens with `git checkout main` and the rollback evaporates silently, bringing the known-broken code
back. So `rollback.sh` records what it did and why; `deploy.sh` refuses to run from a rolled-back
or detached tree without an explicit `--force`; and the provenance block gains a loud row when HEAD
is detached, so a rolled-back prod says so on every boot rather than looking normal.

*Verify:* dry-run on an already-current main is a clean no-op with the tag created once · a deploy
that bumps `requirements.txt` reinstalls and passes the smoke check · **rehearse a rollback**, then
confirm `deploy.sh` refuses until you pass `--force`.
*Revert:* delete two scripts. 4a changes no application code and is useful before 4b exists.

#### Regression gate (CI)

`scripts/ci/check_paths.py` (landed in 2e) is the check; this is how it becomes a *gate* nothing
gets past. Four layers, tightest last — all except the script itself are **slice-4-timed**, because
a merge gate is meaningless until code actually flows through `origin/main` (which 4b establishes):

| Layer | Mechanism | Blocks | When |
|---|---|---|---|
| Script | `scripts/ci/check_paths.py` — imports the app under a scratch root, exits non-zero on any hardcoded prod path | (invoked by the layers below) | **done (2e)** |
| Commit | tracked `.githooks/pre-commit` (via `git config core.hooksPath .githooks`) runs the script | `git commit` — advisory, bypassable with `--no-verify` | slice 4 |
| Merge | GitHub Actions runs the script on every push/PR; a branch-protection rule on `main` requires it green | merge to `main` — **unbypassable**, the real teeth | slice 4 |
| Deploy | `deploy.sh` runs the script in its smoke check before the restart hand-off | a prod deploy | slice 4 (4a) |

The unbypassable guarantee ("never merged to `main`, never deployed") is the Actions + branch-protection
+ deploy-gate combination; the commit hook is fast local feedback only. Branch protection is a GitHub
repo setting the owner enables once the workflow exists. CI installs deps and imports the app with a
dummy `GOOGLE_API_KEY` under a scratch root — the import makes no network call, so no real secret is
needed. The workflow starts as this one check and is where the later test suite
(`TESTING_AND_FEEDBACK_LOOP_PLAN.md`) hangs its jobs.

#### 4b — development moves out of prod

Development moves to `/app/jarvis_staging/code`; `/app/jarvis_code` goes onto `main` and is
thereafter touched only by the two scripts. This is the one-way step, and it is more than a
directory change:

- **In-flight branches** (`feat/context-handling`, `docs/app-channel-plan`; `fix/heartbeat-cost`
  landed in slice 0) need to exist in the staging tree before prod is reset to `main`.
- **[new] The Claude Code project moves with it.** Sessions key off cwd:
  `/home/jarvis_user/.claude/projects/-app-jarvis-code/` holds the memory index, permissions and
  history, and does not follow to a new path. `.gitignore:6` excludes `.claude/`, so local project
  settings aren't in the repo either. Copy both deliberately, or the first staging session starts
  blank and re-prompts for every permission.
- **Docs go stale in the same commit:** `CLAUDE.md`'s deployment section and `DEVELOPMENT.md`
  §Development Workflow 1 ("Edit code at `/app/jarvis_code`") both describe the old world.

*Verify:* a full round trip — edit in staging, test against the staging bot, push, `deploy.sh` in
prod, restart, confirm the provenance block shows the new SHA **and** the new `deploy-` tag.
*Revert:* it is convention — reverting is resuming in-place edits.

**[new] Staging inherits the pathology one level down, deliberately.** The staging tree is both the
dev tree and a running service — exactly what 4b fixes for prod. That is an accepted trade (blast
radius is staging), with two consequences worth naming: you cannot test branch A while editing
branch B, and staging routinely runs dirty, which the provenance block already reports as
`(uncommitted)`.

---

## Backup & rollback

Rollback has **two independent axes** — code and state — and a bad change can require either or
both. Keeping them separate is the point; conflating them is how a "simple revert" loses data.

| Axis | Mechanism | Restores | Does **not** restore |
|---|---|---|---|
| **Code** | git: `deploy-*` tags (slice 4a) or `git revert` (slice 2) | the running code | on-disk *data* written by the newer code |
| **State** | `deploy/backup_state.sh` tarballs of `jarvis_memory` + `jarvis_data` | memory, DB, logs, reminders, heartbeat stamps | code |

**`deploy/backup_state.sh`** — one script, three call sites, so the mechanism is identical
everywhere:

- `backup_state.sh <label>` → `/app/backups/state-<label>-<UTC-ts>.tar.gz` of both state trees.
  A cheap `tar` (a few hundred MB); the checkpointer `threads.sqlite` is included, so take it with
  the service **stopped** (or accept a `-wal` mid-write — fine for a restore-to-a-point, not for a
  live copy). `deploy.sh` calls it non-interactively keyed to the tag-to-be; you call it by hand
  before slice 2 (2·0) and before any risky manual edit.
- `backup_state.sh --restore <tarball>` → stop service → move the current trees aside to
  `…-superseded-<ts>` (never delete on restore — a wrong tarball must itself be undoable) → unpack
  → hand off the restart. Prints what it moved.
- `backup_state.sh --prune` → keep the last N (default 10) plus every tarball keyed to a `deploy-*`
  tag that still exists; delete the rest. Called at the tail of `deploy.sh` so `/app/backups` can't
  grow without bound.

**When each fires:**

- **A deploy that only changes code/behavior** — roll back with the previous `deploy-*` tag; state
  is untouched, no restore needed. This is the common case.
- **A deploy that changed a persisted format** (`state.json`, `HEARTBEAT.md` grammar, checkpoint
  shape, a JSONL field) — *not rollback-safe by code alone*; its commit message must say so
  (slice 4a). Rolling back means old-tag checkout **and** `--restore` of that deploy's pre-pull
  tarball, or the old code reads new data. This is the case the state axis exists for.
- **The slice-2 migration** — the 2·0 snapshot is the floor: if a mis-repointed path (2c/2d) sends
  a write to the wrong tree before you catch it in the restart check, `--restore` puts prod back.

**What a backup cannot recover** — the same boundary as the isolation table below: writes that
already left the box (a real Radarr add, a filed Jellyseerr request, a GitHub issue) are not in any
tarball. State backup covers everything Jarvis *owns*; it does nothing for what Jarvis *reached*.

**Off-box copy.** Tarballs live on the same disk as what they protect, so they survive a bad deploy
but not a disk loss. Periodically copying `/app/backups` (and `/app/secrets`, which is in **no**
tarball and in no git repo) to another machine is out of scope here but worth a line in
`DEVELOPMENT.md` — it is the only copy of the `.env` secrets.

---

## What staging does not isolate

Once `MEMORY_DIR` and `DATA_DIR` are isolated, tools sort into four groups and only one is a
problem:

| Group | Examples | In staging |
|---|---|---|
| Reads of external services | `web_search`, Arbox fetches, Radarr/Sonarr *list* | Harmless |
| Writes to local state | fitness DB, memory files, `scheduled_events.json` | **Isolated** — safe by construction |
| Messages to the owner | reminders, notifications | Go to the **staging bot** |
| **Writes to external services** | Radarr/Sonarr add + delete-with-files, Jellyseerr requests, **GitHub issue/PR writes** | **The real risk** — same live servers from any instance |

**[new] Correction — that row is NOT "already confirmation-gated". Most of it is ungated.**
Earlier drafts (and the 2026-07-10 analysis) claimed the inline keyboard makes this row safe by
requiring a deliberate Confirm on the staging bot. Verified 2026-07-23, that is false — only a
minority of the external writes carry `destructive=True` + `request_confirmation_sync`:

| Tool | Gated? |
|---|---|
| `delete_radarr_movie_with_files` (`radarr.py:301`), `delete_sonarr_series_with_files` (`sonarr.py:308`) | **yes** — `destructive=True` |
| GitHub `draft_github_issue` (`issues.py:120`), `update_issue_status` (`:171`), `add_issue_comment` (`:217`) | **yes** — `destructive=True` |
| `add_radarr_movie` (`radarr.py:178`), `add_sonarr_series` (`sonarr.py:184`) | **no** — fires a real download, no confirm |
| `delete_radarr_movie` / `delete_sonarr_series` (no-files), `remove_from_*_queue`, `set_*_monitored`, `trigger_*_search`, `set_*_quality_profile` | **no** |
| `request_media` (`jellyseerr.py:127`) | **no** — files a real Jellyseerr request |

So a staging turn that decides to "add Severance" or "request that film" hits the **real** Radarr /
Jellyseerr with no button at all. The confirmation seam is a UX gate on the few `destructive` tools,
**not** an environment boundary — a staging bot's Confirm still executes against the live service,
and most of these tools never reach a Confirm in the first place. GitHub reads
(`list_repo_issues`, `read_pr_details`, …) are harmless; the three GitHub *writes* are gated but
still act on the real repo if confirmed.

*This is a known, accepted gap under Design A* (keys stay shared, open question 2). The honest
mitigations, none of which this plan currently builds: point staging's `.env` at throwaway media
instances, omit `GITHUB_TOKEN` from staging (writes then degrade to "not configured" via
`_shared.py:19`), or gate external writes behind an instance check. Called out here so the row is
not mistaken for safe-by-construction — it is the one class staging does not isolate.

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
2. **Media/external keys in staging's `.env`.** Shared for now, so media flows are testable. Prune
   per service if it ever bites; graceful degradation already returns "not configured". The
   *drift* half of this is no longer an open question — `scripts/check_env.sh` ships in slice 3.
3. **Memory re-seed policy** — manual rsync vs a `reseed_staging.sh`. Lean: manual first. Whichever
   wins must also re-seed `heartbeat/state.json`, or every re-seed re-creates the all-tasks-due
   burst (slice 3).
4. ~~**Staging heartbeat guard.**~~ **Settled by default-off toggles.** Proactive behavior is off
   unless a unit turns it on (prod's does, at 2a), so staging is heartbeat-off, reminders-off and
   webhook-off with no configuration at all. Duplicate execution is still *possible* — it is now
   opt-in rather than one-missing-line-away, which was the actual complaint.
5. **A second hub for the app channel.** Not blocking this plan, but it surfaces here: the app
   channel is *outbound* (long-poll), so unlike the webhook it binds no port and there is no
   collision. But the hub is one-bot-one-user, so two agents polling it with one token would
   fight over updates. During step-3 development this is moot — `APP_HUB_URL` will be unset in
   prod, so only staging polls. It bites *after* the channel deploys to prod. Raise with the app
   author: does the hub support a second bot token, or is a staging hub instance the answer?
