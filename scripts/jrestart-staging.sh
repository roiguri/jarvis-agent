#!/usr/bin/env bash
# Restart the STAGING Jarvis and print THIS boot's provenance block to the terminal.
#
# The staging companion to scripts/jrestart.sh. Same one-command "restart and see
# what booted" flow, pointed at jarvis-staging.service. Because staging is started
# on demand (not enabled), a `restart` also serves as a cold `start`.
#
# Read the printed block FIRST — the `root : /app/jarvis_staging` row is the
# isolation guard: it confirms you booted staging against the staging trees, not
# prod. This replaces the single-writer lock the design deliberately dropped.
#
# Robustness contract (see main.py:_provenance_block):
#   - anchors on the stable "Running code:" header, and prints to the LAST line
#     (`sed '/Running code:/,$p'`) — so rows added later are captured with no edit;
#   - scopes to this boot with `--since` captured BEFORE the restart, so it never
#     shows an accumulation of past restarts' blocks.
#
# Rendering: a multi-line log message becomes one journal entry per line, each
# stamped with journald's syslog prefix. `-o cat` drops that prefix so the block
# reads as the clean indented panel; the trailing sed strips the Python logging
# prefix ("<ts> - __main__ - INFO - ") from the header line only.
set -u

since=$(date '+%F %T')
systemctl restart jarvis-staging.service

# Poll (up to ~15s) until this boot has logged the block, rather than a blind
# sleep that a slow boot could outrun.
for _ in $(seq 1 15); do
    sleep 1
    if journalctl -u jarvis-staging --since "$since" --no-pager -q | grep -q "Running code:"; then
        break
    fi
done

journalctl -u jarvis-staging --since "$since" -o cat --no-pager -q \
    | sed -n '/Running code:/,$p' \
    | sed 's/.*Running code:/Running code:/'
