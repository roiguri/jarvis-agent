#!/usr/bin/env bash
# check_env.sh — compare the KEY SETS of .env files across instances.
#
# Unset keys degrade *gracefully* (e.g. Jellyfin URLs fall back to defaults), so a
# drifted .env yields a plausible wrong answer instead of an error. This surfaces
# that drift by diffing which keys are present. It reads the files but only ever
# emits key NAMES — never a single value — so the secrets themselves stay unseen.
#
# Usage: scripts/check_env.sh
#   Diffs .env.example (the repo template) against each instance's secrets/.env.
#   Exit 1 if any instance is missing a template key (the silent-fallback risk).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$REPO_ROOT/.env.example"
[[ -f "$EXAMPLE" ]] || { echo "check_env: template not found: $EXAMPLE" >&2; exit 1; }

# Print the sorted key names of an env file (nothing if it is absent). The regex
# captures only the `KEY=` prefix, so a value can never reach stdout.
keys_of() { [[ -f "$1" ]] && grep -oE '^[A-Za-z_][A-Za-z0-9_]*=' "$1" | sed 's/=$//' | sort -u || true; }

example_keys="$(keys_of "$EXAMPLE")"
echo "template .env.example — $(printf '%s\n' "$example_keys" | grep -c .) keys"
echo

status=0
for entry in "prod:/app/secrets/.env" "staging:/app/jarvis_staging/secrets/.env"; do
    label="${entry%%:*}"; file="${entry#*:}"
    echo "=== $label — $file ==="
    if [[ ! -f "$file" ]]; then
        echo "  (absent)"; echo; continue
    fi
    inst_keys="$(keys_of "$file")"
    missing="$(comm -23 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$inst_keys") | grep -c . || true)"
    missing_list="$(comm -23 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$inst_keys"))"
    extra_list="$(comm -13 <(printf '%s\n' "$example_keys") <(printf '%s\n' "$inst_keys"))"
    if [[ -n "$missing_list" ]]; then
        echo "  MISSING (in template, not here — silent-fallback risk):"
        printf '%s\n' "$missing_list" | sed 's/^/    - /'
        status=1
    fi
    if [[ -n "$extra_list" ]]; then
        echo "  EXTRA (here, not in template — undocumented):"
        printf '%s\n' "$extra_list" | sed 's/^/    + /'
    fi
    [[ -z "$missing_list$extra_list" ]] && echo "  OK — key set matches the template"
    echo
done
exit $status
