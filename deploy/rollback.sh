#!/usr/bin/env bash
# rollback.sh — revert the prod checkout to a previous deploy tag.
#
# Run FROM the prod checkout. Like deploy.sh it NEVER restarts — you do that after.
# Checking out a tag DETACHES HEAD on purpose: that detached state is what deploy.sh's
# step-0 guard keys on, so the next deploy can't silently un-roll-back you. It also
# writes .rollback-marker (audit + the startup block's warning row).
#
# Code vs data: this reverts CODE only. If any deploy being undone changed a persisted
# format (its commit message contains [format-change]), old code will read new-format
# data — so rollback prints a LOUD instruction to restore that deploy's pre-pull state
# tarball with backup_state.sh --restore BEFORE you restart. It does not restore data
# for you (that stops the service and is the owner's conscious call).
#
# Usage:
#   deploy/rollback.sh                     list recent deploy-* tags, newest first
#   deploy/rollback.sh <deploy-tag> [why]  roll back to that tag

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="/app"
SERVICE="jarvis.service"
BACKUP_DIR="$ROOT/backups"
MARKER="$REPO/.rollback-marker"

# Run as the checkout's owner, never root (same rationale as deploy.sh): this does
# git only and never restarts, git refuses a differently-owned tree, and a root git
# write would corrupt ownership. If invoked as root (`pct enter`/`pct exec`), drop to
# the tree's owner and re-exec the identical command. su-from-root needs no password.
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    owner="$(stat -c %U "$REPO")"
    if [[ "$owner" != "root" ]]; then
        echo "rollback: invoked as root — re-exec as checkout owner '$owner'" >&2
        exec su "$owner" -c "exec $(printf '%q ' "$REPO/deploy/rollback.sh" "$@")"
    fi
fi

say() { echo "rollback: $*" >&2; }
die() { echo "rollback: ERROR: $*" >&2; exit 1; }

cd "$REPO"
git fetch --quiet --tags origin 2>/dev/null || true

# No arg: list recent deploy tags and exit.
if [[ $# -eq 0 ]]; then
    echo "Recent deploy tags (newest first):" >&2
    git for-each-ref --sort=-creatordate \
        --format='  %(refname:short)  %(creatordate:short)  %(subject)' \
        'refs/tags/deploy-*' | head -15 >&2
    echo >&2
    echo "Roll back with:  deploy/rollback.sh <tag> [reason]" >&2
    exit 0
fi

TAG="$1"; shift
REASON="${*:-(no reason given)}"

[[ "$TAG" == deploy-* ]] || die "'$TAG' is not a deploy-* tag."
git rev-parse -q --verify "refs/tags/$TAG^{commit}" >/dev/null || die "no such tag: $TAG"
[[ -z "$(git status --porcelain)" ]] || die "working tree is dirty — commit or stash first."

old_head="$(git rev-parse --short HEAD)"
undone="$(git log --oneline "$TAG"..HEAD || true)"
[[ -n "$undone" ]] || die "HEAD is already at or behind $TAG — nothing to roll back."

say "rolling back to $TAG; this undoes:"
echo "$undone" | sed 's/^/    /' >&2

# Detect a persisted-format change among the commits being undone, and find the
# tarball that predates the FIRST undone deploy (its pre-pull snapshot) — computed
# BEFORE the checkout moves HEAD.
FORMAT_CHANGE=0
restore_tarball=""
if echo "$undone" | grep -qiE '\[format-change\]'; then
    FORMAT_CHANGE=1
    first_undone="$(git tag -l 'deploy-*' --merged HEAD --no-merged "$TAG" 2>/dev/null | sort | head -1 || true)"
    [[ -n "$first_undone" ]] && \
        restore_tarball="$(ls -1t "$BACKUP_DIR"/state-"$first_undone"-*.tar.gz 2>/dev/null | head -1 || true)"
fi

# Check out the tag — detaches HEAD (intended).
git checkout --quiet "$TAG"
new_head="$(git rev-parse --short HEAD)"
say "checked out $TAG ($new_head) — HEAD is now DETACHED."

# Record the rollback (audit + startup warning + deploy.sh guard context).
cat > "$MARKER" <<EOF
rolled_back_to=$TAG ($new_head)
rolled_back_from=$old_head
at_utc=$(date -u +%FT%TZ)
reason=$REASON
EOF
say "wrote marker $MARKER"

cat >&2 <<EOF

rollback: done — service NOT restarted (that's yours).
    now at : $TAG ($new_head)  [DETACHED]
    undid  : $(echo "$undone" | grep -c .) commit(s) back from $old_head
    restart: pct exec 106 -- systemctl restart $SERVICE
    note   : the tree is detached; deploy/deploy.sh refuses until you pass --force
             (which returns you to main and clears the rollback marker).
EOF

# Loud data-restore instruction if a format change was undone.
if [[ $FORMAT_CHANGE -eq 1 ]]; then
    cat >&2 <<EOF

rollback: !! FORMAT CHANGE UNDONE — the old code will read new-format data.
       !! Restore the matching state BEFORE restarting:
EOF
    if [[ -n "$restore_tarball" ]]; then
        echo "       !!   deploy/backup_state.sh --restore $restore_tarball" >&2
    else
        echo "       !!   couldn't auto-pick the tarball; choose the pre-change one from:" >&2
        ls -1t "$BACKUP_DIR"/state-*.tar.gz 2>/dev/null | head -6 | sed 's/^/       !!     /' >&2
    fi
fi
