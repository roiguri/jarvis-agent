#!/usr/bin/env bash
# backup_state.sh — snapshot / restore / prune the two state trees.
#
# One script, three call sites:
#   backup_state.sh <label>              snapshot → $ROOT/backups/state-<label>-<UTC-ts>.tar.gz
#   backup_state.sh --restore <tarball>  stop service, move current trees aside, unpack, hand off restart
#   backup_state.sh --prune [N] [--dry-run]  keep newest N (default 10) + the last 5 deploys' tarballs; drop the rest
#
# The state trees are jarvis_memory (including the checkpointer threads.sqlite) and
# jarvis_data, both rooted at ${JARVIS_ROOT:-/app}. State backup covers everything the
# assistant owns; it does not cover code (git handles that) nor writes that already left
# the box (a real media add, a filed request).
#
# For a clean checkpointer copy take the snapshot with the service stopped; a live
# snapshot may capture threads.sqlite-wal mid-write, which is fine for restore-to-a-point
# but not a guaranteed-consistent live copy. The telemetry and episode stores are the
# exception: written on every turn, they go in as SQLite online-backup copies
# (consistent while the service runs) in place of the live files.
#
# Convention: all progress goes to stderr; the single stdout line of a <label> run is the
# tarball path, so callers can capture it — TARBALL="$(backup_state.sh <label>)".

set -euo pipefail

ROOT="${JARVIS_ROOT:-/app}"
MEMORY_DIR="$ROOT/jarvis_memory"
DATA_DIR="$ROOT/jarvis_data"
BACKUP_DIR="$ROOT/backups"
CODE_DIR="$ROOT/jarvis_code"
SERVICE="${JARVIS_SERVICE:-jarvis.service}"
KEEP_DEFAULT=10
KEEP_DEPLOYS=5   # rollback floor: the newest tarballs keyed to a deploy tag that still exists

ts()  { date -u +%Y%m%d-%H%M%SZ; }
log() { echo "backup_state: $*" >&2; }
die() { echo "backup_state: $*" >&2; exit 1; }

usage() {
    cat >&2 <<EOF
usage:
  backup_state.sh <label>              snapshot memory+data → $BACKUP_DIR/state-<label>-<ts>.tar.gz
  backup_state.sh --restore <tarball>  stop $SERVICE, move current trees aside, unpack, hand off restart
  backup_state.sh --prune [N] [--dry-run]
                                       keep newest N (default $KEEP_DEFAULT) + the last $KEEP_DEPLOYS deploys' tarballs;
                                       --dry-run lists what would go and deletes nothing

State root: $ROOT   (override with JARVIS_ROOT=... ; service with JARVIS_SERVICE=...)
EOF
}

cmd_backup() {
    local label="$1"
    [[ -n "$label" ]] || { usage; exit 2; }
    [[ "$label" =~ ^[A-Za-z0-9._-]+$ ]] \
        || die "label must match [A-Za-z0-9._-]+ (got: '$label')"
    [[ -d "$MEMORY_DIR" ]] || die "memory dir not found: $MEMORY_DIR"
    [[ -d "$DATA_DIR"   ]] || die "data dir not found: $DATA_DIR"
    mkdir -p "$BACKUP_DIR"
    local out="$BACKUP_DIR/state-${label}-$(ts).tar.gz"
    [[ -e "$out" ]] && die "refusing to overwrite existing $out"

    # -C "$ROOT" so archived paths are jarvis_memory/... and jarvis_data/... (root-relative),
    # which is exactly what --restore unpacks back into $ROOT.
    log "archiving $MEMORY_DIR + $DATA_DIR"
    # Written on every turn, so these go in as SQLite online-backup copies
    # (consistent while the service runs) in place of the live files.
    local stores=() rel
    for rel in jarvis_data/observability/telemetry.sqlite jarvis_data/episodes/episodes.sqlite; do
        [[ -f "$ROOT/$rel" ]] && stores+=("$rel")
    done
    if (( ${#stores[@]} )); then
        # Global, not local: the EXIT trap must still see it when set -e aborts.
        BACKUP_TMP="$(mktemp -d)"
        trap 'rm -rf "$BACKUP_TMP"' EXIT
        local tmp="$BACKUP_TMP" excludes=()
        for rel in "${stores[@]}"; do
            mkdir -p "$tmp/copy/$(dirname "$rel")"
            if command -v sqlite3 >/dev/null; then
                sqlite3 "$ROOT/$rel" ".backup '$tmp/copy/$rel'"
            else
                python3 -c 'import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)' "$ROOT/$rel" "$tmp/copy/$rel"
            fi
            excludes+=(--exclude="$rel" --exclude="$rel-wal" --exclude="$rel-shm")
        done
        # Excluded from the live pass, appended as the copies: an uncompressed tar
        # is appendable, so the excludes never see the copies' identical names.
        tar -cf "$tmp/state.tar" -C "$ROOT" "${excludes[@]}" jarvis_memory jarvis_data
        tar -rf "$tmp/state.tar" -C "$tmp/copy" "${stores[@]}"
        gzip -c "$tmp/state.tar" > "$out"
    else
        tar -czf "$out" -C "$ROOT" jarvis_memory jarvis_data
    fi
    log "wrote $out ($(du -h "$out" | cut -f1))"
    log "top entries:"
    tar -tzf "$out" | sed -n '1,15p' | sed 's/^/  /' >&2
    echo "$out"   # sole stdout line: the tarball path
}

cmd_restore() {
    local tarball="${1:-}"
    [[ -n "$tarball" ]] || { usage; exit 2; }
    [[ -f "$tarball" ]] || die "tarball not found: $tarball"
    tar -tzf "$tarball" >/dev/null 2>&1 || die "not a readable tar.gz: $tarball"

    # Verify both trees are present BEFORE moving anything aside — a truncated or
    # wrong-shaped tarball must not leave prod with its state half-removed.
    local tops; tops="$(tar -tzf "$tarball" | cut -d/ -f1 | sort -u)"
    grep -qx jarvis_memory <<<"$tops" || die "tarball has no jarvis_memory/ — refusing: $tarball"
    grep -qx jarvis_data   <<<"$tops" || die "tarball has no jarvis_data/ — refusing: $tarball"

    log "stopping $SERVICE"
    systemctl stop "$SERVICE" \
        || die "could not stop $SERVICE (privilege?) — aborted before touching state"

    local stamp; stamp="$(ts)"
    local tree aside
    for tree in jarvis_memory jarvis_data; do
        if [[ -e "$ROOT/$tree" ]]; then
            aside="$ROOT/${tree}-superseded-${stamp}"
            mv "$ROOT/$tree" "$aside"
            log "moved current $tree aside → $aside"
        fi
    done
    tar -xzf "$tarball" -C "$ROOT"
    log "unpacked $tarball into $ROOT"
    log "current trees preserved as *-superseded-${stamp} — delete once the restore checks out"
    log "HAND-OFF: restart the service —  systemctl restart $SERVICE"
}

cmd_prune() {
    local keep="$KEEP_DEFAULT" dry=0 arg
    for arg in "$@"; do
        case "$arg" in
            --dry-run) dry=1 ;;
            *) [[ "$arg" =~ ^[0-9]+$ ]] || die "prune count must be an integer (got: '$arg')"; keep="$arg" ;;
        esac
    done
    [[ -d "$BACKUP_DIR" ]] || { log "no backup dir at $BACKUP_DIR — nothing to prune"; return 0; }

    # deploy-* tags that still exist: the newest KEEP_DEPLOYS tarballs keyed to one are kept,
    # so rollback.sh can auto-pick the matching state for the last few deploys. Older deploys
    # fall back to the container backup (rollback.sh lists what is left when none matches).
    local tags=""
    if git -C "$CODE_DIR" rev-parse >/dev/null 2>&1; then
        tags="$(git -C "$CODE_DIR" tag -l 'deploy-*')"
    fi

    local all=()
    mapfile -t all < <(ls -1t "$BACKUP_DIR"/state-*.tar.gz 2>/dev/null || true)
    [[ ${#all[@]} -gt 0 ]] || { log "no state tarballs to prune"; return 0; }

    # Keep set = (newest N by position) ∪ (newest KEEP_DEPLOYS deploy-tag-keyed tarballs).
    local idx=0 kept=0 removed=0 deploys=0 f base reason tag is_deploy
    for f in "${all[@]}"; do
        base="$(basename "$f")"
        reason="" is_deploy=0
        if [[ -n "$tags" ]]; then
            while IFS= read -r tag; do
                [[ -n "$tag" ]] || continue
                if [[ "$base" == state-"$tag"-*.tar.gz ]]; then is_deploy=1; break; fi
            done <<<"$tags"
        fi
        if [[ $idx -lt $keep ]]; then
            reason="recent"
        elif [[ $is_deploy -eq 1 && $deploys -lt $KEEP_DEPLOYS ]]; then
            reason="deploy"
        fi
        [[ $is_deploy -eq 1 ]] && deploys=$((deploys + 1))
        if [[ -n "$reason" ]]; then
            kept=$((kept + 1))
        elif [[ $dry -eq 1 ]]; then
            log "would prune $base"; removed=$((removed + 1))
        else
            rm -f "$f"; log "pruned $base"; removed=$((removed + 1))
        fi
        idx=$((idx + 1))
    done
    if [[ $dry -eq 1 ]]; then
        log "dry run: would keep $kept, prune $removed — nothing deleted"
    else
        log "kept $kept, pruned $removed"
    fi
}

case "${1:-}" in
    --restore)    shift; cmd_restore "${1:-}" ;;
    --prune)      shift; cmd_prune   "$@" ;;
    --help | -h | "") usage ;;
    --*)          die "unknown option: $1  (see --help)" ;;
    *)            cmd_backup "$1" ;;
esac
