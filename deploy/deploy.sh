#!/usr/bin/env bash
# deploy.sh — update the prod checkout to origin/main, tag it, verify it imports.
#
# Run FROM the prod checkout. It NEVER restarts the service — that stays with the
# owner (a restart drops in-flight turns). It fails closed at every step, so a bad
# deploy is caught here, before the restart, not after.
#
# Flow:
#   refuse if rolled-back/detached (unless --force) → clean-tree check → snapshot
#   state keyed to the tag-to-be → fetch → checkout main → pull --ff-only origin
#   main → tag deploy-YYYY-MM-DD-N (push) → sync deps if requirements.txt moved →
#   import smoke check → path-isolation check → assert the unit declares
#   JARVIS_ROOT → prune old backups → print the restart hand-off.
#
# Usage: deploy/deploy.sh [--force]
#   --force   deploy even from a detached/rolled-back HEAD (see step 0).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="/app"                                 # prod instance root (smoke check + backups)
SERVICE="jarvis.service"
VENV="$REPO/venv"
REQ="$REPO/requirements.txt"
COMMITTED_UNIT="$REPO/deploy/jarvis.service"
BACKUP="$REPO/deploy/backup_state.sh"
CHECK_PATHS="$REPO/scripts/ci/check_paths.py"
CHECK_ENV="$REPO/scripts/check_env.sh"
MARKER="$REPO/.rollback-marker"

FORCE=0
case "${1:-}" in
    --force)     FORCE=1 ;;
    --help | -h) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \?//'; exit 0 ;;
    "")          ;;
    *)           echo "deploy: unknown arg '$1' (see --help)" >&2; exit 2 ;;
esac

say() { echo "deploy: $*" >&2; }
die() { echo "deploy: ERROR: $*" >&2; exit 1; }

cd "$REPO"

# 0. Refuse a rolled-back / detached HEAD unless forced — otherwise the `checkout
#    main` in step 3 would silently discard the rollback and bring back the code it
#    moved away from.
if ! git symbolic-ref -q HEAD >/dev/null; then
    [[ $FORCE -eq 1 ]] || die "HEAD is detached (rolled back?). Re-run with --force to deploy past it."
    say "WARNING: HEAD was detached; --force given — proceeding onto main."
fi

# 1. Clean working tree — a deploy must not fold uncommitted edits into the pull.
[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty — commit or stash first."

# 2. Compute the tag-to-be (deploy-DATE-N) and snapshot state under that label,
#    BEFORE the pull. Rollback restores code; this tarball restores data if the new
#    code changed a persisted format (see docs/DEPLOY.md).
git fetch --quiet --tags origin
today="$(date -u +%Y-%m-%d)"
n=1
while git rev-parse -q --verify "refs/tags/deploy-${today}-${n}" >/dev/null; do n=$((n + 1)); done
TAG="deploy-${today}-${n}"
say "tag-to-be: $TAG"
req_before="$(sha256sum "$REQ" 2>/dev/null | cut -d' ' -f1 || true)"

say "snapshotting state (label $TAG)"
JARVIS_ROOT="$ROOT" "$BACKUP" "$TAG" >/dev/null

# 3. Fast-forward the checkout to origin/main. --ff-only fails (closed) on divergence.
say "checkout main + pull --ff-only origin/main"
git checkout --quiet main
rm -f "$MARKER"   # back on main — any prior rollback is now resolved
git pull --ff-only --quiet origin main
new_sha="$(git rev-parse --short HEAD)"
say "main is now at $new_sha"

# 4. Tag the commit we landed ON (the incoming one) and push it. Tagging what you
#    land on is what makes `deploy :` in the startup block name the live deploy.
git tag "$TAG"
git push --quiet origin "$TAG" || say "WARNING: tag push failed — push '$TAG' manually later."
say "tagged $new_sha as $TAG"

# 5. Sync deps only if requirements.txt changed across the pull.
req_after="$(sha256sum "$REQ" 2>/dev/null | cut -d' ' -f1 || true)"
if [[ "$req_before" != "$req_after" ]]; then
    say "requirements.txt changed — pip install"
    "$VENV/bin/pip" install --quiet -r "$REQ" || die "pip install failed — NOT safe to restart."
else
    say "requirements.txt unchanged — skipping pip"
fi

# 6. Import smoke check — exercises every import, the dotenv load, and the API-key
#    assert without starting the bot. JARVIS_ROOT=/app is MANDATORY: config is
#    fail-closed on an unset root and this deploy shell carries no Environment=.
say "smoke check: import main under JARVIS_ROOT=$ROOT"
JARVIS_ROOT="$ROOT" "$VENV/bin/python" -c "import main" \
    || die "smoke check FAILED — main does not import. NOT restarting; investigate first."

# 7. Path-isolation guard (the same check CI runs) — no module may hardcode a prod path.
if [[ -f "$CHECK_PATHS" ]]; then
    say "path-isolation check"
    "$VENV/bin/python" "$CHECK_PATHS" >/dev/null \
        || die "check_paths FAILED — a module hardcodes a prod state path. NOT restarting."
fi

# 8. Assert the installed unit still declares JARVIS_ROOT. The step-6 smoke check
#    can't catch this — its own JARVIS_ROOT=/app prefix masks a unit that lost the
#    line, which would then fail closed on the very next boot.
systemctl cat "$SERVICE" 2>/dev/null | grep -q 'JARVIS_ROOT=' \
    || die "installed $SERVICE does not declare JARVIS_ROOT — a restart would fail closed. Reinstall $COMMITTED_UNIT."
if ! diff -q <(systemctl cat "$SERVICE" 2>/dev/null | sed '1d') "$COMMITTED_UNIT" >/dev/null 2>&1; then
    say "NOTE: installed $SERVICE differs from $COMMITTED_UNIT — review it (not fatal)."
fi

# 9. Advisory: warn (don't block) if prod's .env drifted from the template.
[[ -x "$CHECK_ENV" ]] && { "$CHECK_ENV" >/dev/null 2>&1 || say "NOTE: check_env reports .env key drift — run scripts/check_env.sh."; }

# 10. Trim old backups (keeps deploy-tagged ones).
JARVIS_ROOT="$ROOT" "$BACKUP" --prune >/dev/null || true

# 11. Hand off — the OWNER restarts (deploy never does).
cat >&2 <<EOF

deploy: ready — service NOT restarted (that's yours).
    landed : $new_sha on main
    tag    : $TAG
    restart: pct exec 106 -- systemctl restart $SERVICE
    verify : scripts/jrestart.sh   (or watch journalctl for the 'Running code:' block)
EOF
