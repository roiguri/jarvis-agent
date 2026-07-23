#!/usr/bin/env bash
# backup_state.sh — snapshot / restore / prune the two state trees.
#
# One script, three call sites:
#   backup_state.sh <label>              snapshot → $ROOT/backups/state-<label>-<UTC-ts>.tar.gz
#   backup_state.sh --restore <tarball>  stop service, move current trees aside, unpack, hand off restart
#   backup_state.sh --prune [N]          keep newest N (default 10) + every deploy-tag-keyed tarball; drop the rest
#
# The state trees are jarvis_memory (including the checkpointer threads.sqlite) and
# jarvis_data, both rooted at ${JARVIS_ROOT:-/app}. State backup covers everything the
# assistant owns; it does not cover code (git handles that) nor writes that already left
# the box (a real media add, a filed request).
#
# For a clean checkpointer copy take the snapshot with the service stopped; a live
# snapshot may capture threads.sqlite-wal mid-write, which is fine for restore-to-a-point
# but not a guaranteed-consistent live copy.
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

ts()  { date -u +%Y%m%d-%H%M%SZ; }
log() { echo "backup_state: $*" >&2; }
die() { echo "backup_state: $*" >&2; exit 1; }

usage() {
    cat >&2 <<EOF
usage:
  backup_state.sh <label>              snapshot memory+data → $BACKUP_DIR/state-<label>-<ts>.tar.gz
  backup_state.sh --restore <tarball>  stop $SERVICE, move current trees aside, unpack, hand off restart
  backup_state.sh --prune [N]          keep newest N (default $KEEP_DEFAULT) + every deploy-tagged tarball

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
    tar -czf "$out" -C "$ROOT" jarvis_memory jarvis_data
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
    local keep="${1:-$KEEP_DEFAULT}"
    [[ "$keep" =~ ^[0-9]+$ ]] || die "prune count must be an integer (got: '$keep')"
    [[ -d "$BACKUP_DIR" ]] || { log "no backup dir at $BACKUP_DIR — nothing to prune"; return 0; }

    # deploy-* tags that still exist → their keyed tarballs are always kept (rollback floor).
    local tags=""
    if git -C "$CODE_DIR" rev-parse >/dev/null 2>&1; then
        tags="$(git -C "$CODE_DIR" tag -l 'deploy-*')"
    fi

    local all=()
    mapfile -t all < <(ls -1t "$BACKUP_DIR"/state-*.tar.gz 2>/dev/null || true)
    [[ ${#all[@]} -gt 0 ]] || { log "no state tarballs to prune"; return 0; }

    # Keep set = (newest N by position) ∪ (every deploy-tag-keyed tarball). Additive:
    # a tag-keyed tarball older than N still survives; the first N survive regardless.
    local idx=0 kept=0 removed=0 f base reason tag
    for f in "${all[@]}"; do
        base="$(basename "$f")"
        reason=""
        if [[ $idx -lt $keep ]]; then
            reason="recent"
        elif [[ -n "$tags" ]]; then
            while IFS= read -r tag; do
                [[ -n "$tag" ]] || continue
                if [[ "$base" == state-"$tag"-*.tar.gz ]]; then reason="tag:$tag"; break; fi
            done <<<"$tags"
        fi
        if [[ -n "$reason" ]]; then
            kept=$((kept + 1))
        else
            rm -f "$f"; log "pruned $base"; removed=$((removed + 1))
        fi
        idx=$((idx + 1))
    done
    log "kept $kept, pruned $removed"
}

case "${1:-}" in
    --restore)    shift; cmd_restore "${1:-}" ;;
    --prune)      shift; cmd_prune   "${1:-}" ;;
    --help | -h | "") usage ;;
    --*)          die "unknown option: $1  (see --help)" ;;
    *)            cmd_backup "$1" ;;
esac
