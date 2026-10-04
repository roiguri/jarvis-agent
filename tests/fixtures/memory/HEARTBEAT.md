# Heartbeat Tasks

Fixture preamble: kept verbatim above the task blocks.

- **due-task** | every 1h | notes: `heartbeat/due-task.md`
  Body of the task that is due this tick.

- **not-due-task** | every 24h | due: 08:00-10:00 | notes: `heartbeat/not-due-task.md`
  Body that must collapse to a one-line note.

- **gated-task** | every 1h | gate: fixture-gate | notes: `heartbeat/gated-task.md`
  Body that code runs; never shown to the tick.

- **paused-task** | every 7d | paused | notes: `heartbeat/paused-task.md`
  Body of a task the owner paused.
