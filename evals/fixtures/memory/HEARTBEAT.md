# Heartbeat tasks (synthetic, for evals)

- **morning-readiness-check** | every 1d | due: 08:00-10:00 | notes: `heartbeat/readiness_check.md`
  1. Run `health_status(day="", days=1)` for last night's sleep and recovery.
  2. Send a short "Daily Readiness" message correlating recovery with today's planned training.
  3. If Roi already discussed readiness in today's chat, don't send it again — mark the task done.

- **step-challenge-tracker** | every 4h | due: 08:00-23:00 | notes: `heartbeat/step_challenge_tracker.md`
  Check today's steps with `health_status`. Update the notes file with the count.
  In the afternoon (after 15:00), if under 5,000 steps, send one short nudge — only one nudge per day.
  After 20:00, record the day's final count in the notes. Never message just to report progress.

- **daily-log** | every 3h | due: 05:00-23:30 | notes: `heartbeat/daily_log.md`
  Update today's daily log (the filename is given in the tick message). Fold in today's conversations with Roi and today's heartbeat activity. If the file exists, read it first and update rather than overwrite. Format: `## Conversations (today)` / `## Heartbeat Activity` / `## Notes`. Never message Roi about the log.

- **weekly-summary** | every 7d | due: sun 18:00-21:00 | notes: `heartbeat/weekly_summary.md`
  Only on Sunday evening: send a "Week in Review" with the week's step total. On any other day, do nothing and leave the task for Sunday.
