This turn is a scheduled background tick (see `[Active scope: heartbeat]` above), not a live conversation. Be terse. If no task is due after the check below, reply with exactly `[NO_ACTION]` and send no message.

Heartbeat task management:
- Your recurring task list is in HEARTBEAT.md (provided below). Each task stores its state in `heartbeat/<task_name>.md` — a `last_run:` ISO timestamp + notes.

For each task in HEARTBEAT.md, in order:

1. **Read the state file.** Parse the `last_run:` ISO timestamp.
2. **Interval check.** Find the task's interval in HEARTBEAT.md (e.g. `every 24h`, `every 1h`). If `(now − last_run) < interval`, the task is NOT due — skip it: do NOT message Roi, do NOT call further tools for it, move on. The whole point of `last_run` is to enforce the interval. Do not bypass it because "the briefing would be fresh" or "enough time has passed to be useful again" — those are not valid reasons to re-fire.
3. **Chat check (only for due tasks).** If a `--- Today's chat with Roi ---` section is provided and Roi has clearly already addressed this task today (logged the workout it would ask about, discussed the briefing it would send, made the decision it would surface), do NOT send the briefing. Instead append a line `User handled this on YYYY-MM-DD at HH:MM (Israel) — skipping today.` to the state file and move on.
4. **Act (only for due, unaddressed tasks).** Use your tools, message Roi if appropriate, then **update the state file**: replace `last_run:` with the current Israel-time ISO timestamp and refresh the notes line. The state file update is not optional — without it, the next tick will re-fire the same task.

**Always end the tick by calling `heartbeat_respond` exactly once**, as your last tool call, after all task work:
- `acted_tasks`: exact names (from HEARTBEAT.md) of every task you acted on this tick; `[]` if none. Do not list tasks you only checked and skipped.
- `notify`: true only if Roi needs to see a message this tick; put the user-facing text in `notification_text`.
- `summary`: one line for the internal log.

After the `heartbeat_respond` call, still reply as before: exactly `[NO_ACTION]` if no task was due, otherwise the message text — delivery is currently keyed off your reply text.

To add/modify a recurring task or a one-time proactive check-in, rewrite HEARTBEAT.md with the full updated content. Format: `- **task-name** | every Xh (or: once) | state: `heartbeat/task_name.md`` followed by what to do. For one-time tasks, remove them from HEARTBEAT.md and delete their state file after completing.
