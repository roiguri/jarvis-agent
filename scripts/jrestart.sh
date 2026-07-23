#!/usr/bin/env bash
# Restart Jarvis and print THIS boot's provenance block to the terminal.
#
# systemd routes a service's own stdout to the journal, not the invoking
# terminal, so "see it on restart" means peeking the journal after the restart.
# This does that in one command.
#
# Robustness contract (see main.py:_provenance_block):
#   - anchors on the stable "Running code:" header, and prints to the LAST line
#     (`sed '/Running code:/,$p'`) — so rows added in later slices are captured
#     with no edit here;
#   - scopes to this boot with `--since` captured BEFORE the restart, so it never
#     shows an accumulation of past restarts' blocks.
#
# Rendering: a multi-line log message becomes one journal entry per line, each
# stamped with journald's syslog prefix. `-o cat` drops that prefix so the block
# reads as the clean indented panel; the trailing sed strips the Python logging
# prefix ("<ts> - __main__ - INFO - ") from the header line only.
set -u

since=$(date '+%F %T')
systemctl restart jarvis.service

# Poll (up to ~15s) until this boot has logged the block, rather than a blind
# sleep that a slow boot could outrun.
for _ in $(seq 1 15); do
    sleep 1
    if journalctl -u jarvis --since "$since" --no-pager -q | grep -q "Running code:"; then
        break
    fi
done

journalctl -u jarvis --since "$since" -o cat --no-pager -q \
    | sed -n '/Running code:/,$p' \
    | sed 's/.*Running code:/Running code:/'
