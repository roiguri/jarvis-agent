#!/usr/bin/env bash
# deploy-staging.sh — update the staging checkout to origin/<branch>, verify it imports.
#
# Run FROM the staging checkout on the server (or via `pct exec 106 -- ...`). It NEVER
# restarts the service — that stays with the owner. It fails closed at every step so a
# broken branch is caught before restarting staging.
#
# Flow:
#   clean-tree check → fetch origin → verify origin/<branch> exists →
#   checkout -B <branch> origin/<branch> → sync deps if requirements.txt moved →
#   import smoke check under JARVIS_ROOT=/app/jarvis_staging → path-isolation check →
#   assert unit declares JARVIS_ROOT=/app/jarvis_staging → print restart hand-off.
#
# Usage: deploy/deploy-staging.sh <branch>
#   <branch>  pushed branch name on origin (e.g. feat/quote-reply, or main).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="/app/jarvis_staging"                  # staging instance root (smoke check + unit check)
SERVICE="jarvis-staging.service"
VENV="$REPO/venv"
REQ="$REPO/requirements.txt"
COMMITTED_UNIT="$REPO/deploy/jarvis-staging.service"
CHECK_PATHS="$REPO/scripts/ci/check_paths.py"
CHECK_ENV="$REPO/scripts/check_env.sh"

# Run as the checkout's owner, never root. Same pattern as deploy/deploy.sh: git/pip
# writes as root would scatter root-owned files through a jarvis_user tree.
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    owner="$(stat -c %U "$REPO")"
    if [[ "$owner" != "root" ]]; then
        echo "deploy-staging: invoked as root — re-exec as checkout owner '$owner' (no privilege needed)" >&2
        exec su "$owner" -c "exec $(printf '%q ' "$REPO/deploy/deploy-staging.sh" "$@")"
    fi
fi

case "${1:-}" in
    --help | -h) grep '^#' "$0" | grep -v '^#!' | sed 's/^# \?//'; exit 0 ;;
    "")          echo "deploy-staging: ERROR: branch name required (e.g. deploy/deploy-staging.sh feat/my-branch)" >&2; exit 2 ;;
    -*)          echo "deploy-staging: unknown arg '$1' (see --help)" >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
    echo "deploy-staging: ERROR: expected exactly one argument (<branch>), got $#" >&2
    exit 2
fi
BRANCH="$1"

say() { echo "deploy-staging: $*" >&2; }
die() { echo "deploy-staging: ERROR: $*" >&2; exit 1; }

cd "$REPO"

# 1. Clean working tree — staging is a deploy target, not an in-place edit tree.
#    Refusing a dirty tree prevents wiping ad-hoc edits or silently testing uncommitted code.
[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty — commit, stash, or discard local edits in $REPO first."

# 2. Fetch and verify the requested branch exists on origin.
say "fetching origin"
git fetch --quiet --prune origin
git rev-parse -q --verify "refs/remotes/origin/${BRANCH}" >/dev/null \
    || die "branch 'origin/${BRANCH}' not found — push it first."

req_before="$(sha256sum "$REQ" 2>/dev/null | cut -d' ' -f1 || true)"

# 3. Point the local branch directly at origin/<branch> (handles both fast-forwards and rebases).
say "checkout -B ${BRANCH} origin/${BRANCH}"
git checkout --quiet -B "$BRANCH" "origin/${BRANCH}"
new_sha="$(git rev-parse --short HEAD)"
say "staging is now at ${new_sha} on ${BRANCH}"

# 4. Sync deps only if requirements.txt changed.
req_after="$(sha256sum "$REQ" 2>/dev/null | cut -d' ' -f1 || true)"
if [[ "$req_before" != "$req_after" ]]; then
    say "requirements.txt changed — pip install"
    "$VENV/bin/pip" install --quiet -r "$REQ" || die "pip install failed — NOT safe to restart."
else
    say "requirements.txt unchanged — skipping pip"
fi

# 5. Import smoke check under the staging root.
say "smoke check: import main under JARVIS_ROOT=$ROOT"
JARVIS_ROOT="$ROOT" "$VENV/bin/python" -c "import main" \
    || die "smoke check FAILED — main does not import. NOT restarting; investigate first."

# 6. Path-isolation guard — no module may hardcode a prod state path.
if [[ -f "$CHECK_PATHS" ]]; then
    say "path-isolation check"
    "$VENV/bin/python" "$CHECK_PATHS" >/dev/null \
        || die "check_paths FAILED — a module hardcodes a prod state path. NOT restarting."
fi

# 7. Assert the installed staging unit declares JARVIS_ROOT=/app/jarvis_staging.
systemctl cat "$SERVICE" 2>/dev/null | grep -q "JARVIS_ROOT=${ROOT}" \
    || die "installed $SERVICE does not declare JARVIS_ROOT=${ROOT} — refusing hand-off. Reinstall $COMMITTED_UNIT."
if ! diff -q <(systemctl cat "$SERVICE" 2>/dev/null | sed '1d') "$COMMITTED_UNIT" >/dev/null 2>&1; then
    say "NOTE: installed $SERVICE differs from $COMMITTED_UNIT — review it (not fatal)."
fi

# 8. Advisory: warn (don't block) if staging's .env drifted from the template.
[[ -x "$CHECK_ENV" ]] && { "$CHECK_ENV" >/dev/null 2>&1 || say "NOTE: check_env reports .env key drift — run scripts/check_env.sh."; }

# 9. Hand off — the OWNER restarts (deploy never does).
cat >&2 <<EOF

deploy-staging: ready — service NOT restarted (that's yours).
    landed : $new_sha on $BRANCH
    root   : $ROOT
    restart: pct exec 106 -- $REPO/scripts/jrestart-staging.sh
    logs   : pct exec 106 -- journalctl -u $SERVICE -f
EOF
