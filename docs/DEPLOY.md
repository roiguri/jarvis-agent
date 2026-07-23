# Deploy & operations runbook

How to run the operator tooling for the two Jarvis instances. Design rationale lives
in [plans/STAGING_AND_DEPLOY.md](plans/STAGING_AND_DEPLOY.md); this is the how‑to.

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

> **Coming next step.** Lists recent `deploy-*` tags, checks one out (detaching HEAD),
> writes a rollback marker, and — if that deploy's commit flagged a persisted‑format
> change — restores its state tarball with `backup_state.sh --restore`. Then hands off
> the restart. A rolled‑back tree is detached, so the next `deploy.sh` needs `--force`,
> and the startup block shows a loud "detached/rolled‑back" row.

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

---

## `scripts/jrestart.sh` — restart prod and show what booted

Restarts `jarvis.service` and prints **this boot's** `Running code:` provenance block
(branch, sha, deploy tag, root, proactive toggles) — so you immediately see what came
up. Owner‑run (needs root).

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
