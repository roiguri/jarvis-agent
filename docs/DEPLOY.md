# Deploy & operations runbook

How to run the operator tooling for the two Jarvis instances. Design rationale lives
in [plans/archive/STAGING_AND_DEPLOY.md](plans/archive/STAGING_AND_DEPLOY.md); this is the how‑to.

## The two instances

| | Prod | Staging |
|---|---|---|
| Root (`JARVIS_ROOT`) | `/app` | `/app/jarvis_staging` |
| Code | `/app/jarvis_code` | `/app/jarvis_staging/code` |
| Service | `jarvis.service` | `jarvis-staging.service` (started on demand, not enabled) |
| Proactive (heartbeat/reminders/webhook) | on | off (inert) |
| Bot | the real Jarvis | the staging test bot |

Every state path derives from `JARVIS_ROOT` (`config.py`); an unset root refuses to
start. **Code flows through GitHub:** develop in the staging tree → push → merge to
`origin/main` → prod pulls with `deploy.sh`. The owner runs every restart.

> Restarts and unit installs need root, done from the Proxmox host via `pct exec 106 -- …`
> or `pct enter 106`. Claude/CI cannot restart the service.

---

## `deploy/deploy.sh` — update prod to `origin/main`

Run **from the prod checkout**. Pulls `origin/main`, tags the new commit, verifies it
imports, and hands off. **It never restarts** — you do that after it prints the hand‑off.

```bash
cd /app/jarvis_code
./deploy/deploy.sh            # normal deploy
./deploy/deploy.sh --force    # deploy past a rolled-back/detached HEAD (see rollback)
```

What it does, and why each step fails closed:

1. **Refuses a detached/rolled‑back HEAD** without `--force` — otherwise its `checkout main` would silently undo a rollback.
2. **Clean‑tree check** — won't fold uncommitted edits into the pull.
3. **Snapshots state** (`backup_state.sh`) keyed to the tag‑to‑be, *before* the pull — so a format‑changing deploy is recoverable (rollback restores code, not data).
4. **`checkout main` + `pull --ff-only origin main`** — fails on divergence.
5. **Tags the incoming commit** `deploy-YYYY-MM-DD-N` and pushes it — this is what makes the startup block's `deploy :` row name the live deploy.
6. **Dep sync** — `pip install` only if `requirements.txt` changed.
7. **Import smoke check** — `JARVIS_ROOT=/app venv/bin/python -c "import main"`. The prefix is mandatory (config is fail‑closed).
8. **Path‑isolation check** (`check_paths.py`) — no module hardcodes a prod path.
9. **Unit assertion** — the installed `jarvis.service` still declares `JARVIS_ROOT` (a lost line would fail‑closed on the next boot, which the smoke check can't see).
10. **Prunes old backups**, then **prints the restart hand‑off**.

Any failure aborts **before** the restart. After it succeeds:

```bash
pct exec 106 -- systemctl restart jarvis.service
scripts/jrestart.sh      # or watch journalctl for the 'Running code:' block
```

---

## `deploy/rollback.sh` — revert prod to a previous deploy

Run **from the prod checkout**. Reverts **code** to a chosen `deploy-*` tag. **Never
restarts** — you do that after.

```bash
deploy/rollback.sh                       # list recent deploy-* tags (newest first)
deploy/rollback.sh deploy-2026-07-23-1 "webhook regression"   # roll back to that tag
```

1. **Lists** recent `deploy-*` tags (no arg), or takes a target tag + optional reason.
2. Refuses a dirty tree; shows the commits being undone.
3. **Checks out the tag — this detaches HEAD on purpose.** That detached state is what
   `deploy/deploy.sh` step 0 refuses (without `--force`), so the next deploy can't
   silently un‑roll‑back you. The startup block shows a loud `HEAD is DETACHED` row on
   every boot until you deploy forward again.
4. Writes **`.rollback-marker`** (what/why/when — audit).
5. Restart to apply: `pct exec 106 -- systemctl restart jarvis.service`.

**Code vs data.** Rollback reverts code only. If a deploy being undone changed a
persisted format, mark its commit message with **`[format-change]`** — then `rollback.sh`
prints a **loud instruction** naming the pre‑change state tarball to restore with
`deploy/backup_state.sh --restore` *before* you restart. It won't restore data for you
(that stops the service — your conscious call).

**Getting back to `main`:** `deploy/deploy.sh --force` returns you to `main` and clears
the marker; the `--force` is required precisely so leaving a rollback is deliberate.

---

## `deploy/backup_state.sh` — snapshot / restore / prune state

Backs up the two state trees (`jarvis_memory` incl. the conversation DB, and
`jarvis_data`). Rooted at `${JARVIS_ROOT:-/app}`. Covers everything Jarvis *owns* —
not code (git), and not writes that already left the box (a real media add).

```bash
# snapshot -> $ROOT/backups/state-<label>-<UTC-ts>.tar.gz  (path printed on stdout)
deploy/backup_state.sh pre-change
JARVIS_ROOT=/app/jarvis_staging deploy/backup_state.sh staging-snap   # a different instance

# restore: stops the service, moves current trees aside to *-superseded-<ts>
# (never deletes), unpacks, hands off the restart
deploy/backup_state.sh --restore /app/backups/state-pre-change-<ts>.tar.gz

# prune: keep the newest N (default 10) plus every deploy-tagged tarball
deploy/backup_state.sh --prune
```

Take a snapshot with the service **stopped** for a consistent DB; a live snapshot may
catch the DB `-wal` mid‑write (fine for restore‑to‑a‑point). `deploy.sh` calls it
automatically; run it by hand before any risky manual edit.

---

## `scripts/check_env.sh` — `.env` key‑set drift

Diffs which **keys** are present across `.env.example` and each instance's
`secrets/.env`. Emits key **names only, never values**. Unset keys degrade silently
(wrong defaults), so this catches drift before it bites — e.g. adding a key to prod's
`.env` and forgetting staging's.

```bash
scripts/check_env.sh      # exit 1 if any instance is missing a template key
```

---

## `scripts/ci/check_paths.py` — path‑isolation guard

Imports the whole app under a throwaway root and fails if any module‑level constant
still hardcodes `/app/jarvis_memory` or `/app/jarvis_data`. This is what keeps the
`JARVIS_ROOT` isolation from silently regressing when a future edit adds a hardcoded
path. Run by `deploy.sh`, the pre‑commit hook, and CI.

```bash
venv/bin/python scripts/ci/check_paths.py    # exit 0 clean, 1 on a leak
```

**The regression gate has three layers** (see the plan's "Regression gate (CI)"):

- **Commit** — `.githooks/pre-commit` runs the check on every `git commit`. Active only
  where `core.hooksPath` points at it, so enable it once per checkout:
  ```bash
  git config core.hooksPath .githooks
  ```
  Fast local feedback; bypassable with `git commit --no-verify`.
- **Merge** — `.github/workflows/ci.yml` runs it on every PR and push to `main`. Make it
  unbypassable by enabling a **branch-protection rule** on `main` (GitHub → Settings →
  Branches) requiring the `path-isolation` check to pass. This is the real gate.
- **Deploy** — `deploy/deploy.sh` runs it before the restart hand-off.

---

## `scripts/jrestart.sh` / `jrestart-staging.sh` — restart and show what booted

Restart one instance and print **that boot's** `Running code:` provenance block (branch,
sha, deploy tag, `root :`, proactive toggles) — so you immediately see what came up.
Owner‑run (needs root); run them from inside the container.

```bash
scripts/jrestart.sh            # prod    → jarvis.service
scripts/jrestart-staging.sh    # staging → jarvis-staging.service (also cold-starts it)
```

Read the printed block first. For staging the `root : /app/jarvis_staging` row is the
isolation check — it confirms you booted against the staging trees, not prod (this is
what replaces the single-writer lock the design dropped).

### Quick commands

**Privilege.** `deploy.sh` and `rollback.sh` need **no** root — they do git + pip only —
and they **self-drop to the checkout owner** (`jarvis_user`) if you invoke them as root, so
running git as root never corrupts the tree's ownership. The **restart** scripts *do* need
root (they call `systemctl`). So: deploy as anyone, restart as root.

**Inside the container** (`pct enter 106`, root shell):

```bash
/app/jarvis_code/deploy/deploy.sh                       # deploy prod (drops to jarvis_user itself)
/app/jarvis_code/scripts/jrestart.sh                    # then restart prod + show the boot block
/app/jarvis_staging/code/scripts/jrestart-staging.sh    # restart / cold-start staging
```

**From the PVE host** — drop these in `/root/.bashrc` (each wraps the in-container script
with `pct exec 106`):

```bash
alias jrestart='pct exec 106 -- /app/jarvis_code/scripts/jrestart.sh'
alias jrestart-staging='pct exec 106 -- /app/jarvis_staging/code/scripts/jrestart-staging.sh'
alias jdeploy='pct exec 106 -- /app/jarvis_code/deploy/deploy.sh'
```

`jdeploy` runs the fail‑closed `deploy.sh` (it never restarts); follow a green deploy with
`jrestart` to bring the new code up. Keeping deploy and restart as two steps is deliberate —
a restart drops in‑flight turns, so it stays a conscious second step.

---

## Common workflows

**Deploy a change** (once it's merged to `origin/main`):
```bash
cd /app/jarvis_code && ./deploy/deploy.sh
pct exec 106 -- systemctl restart jarvis.service
scripts/jrestart.sh
```

**Roll back a bad deploy:** `deploy/rollback.sh` (see above), then restart. If the bad
deploy changed a persisted format, rollback also restores that deploy's pre‑pull tarball.

**Snapshot before a risky manual edit:** `deploy/backup_state.sh <label>` (service
stopped for a clean DB), edit, and `--restore` that tarball if it goes wrong.
