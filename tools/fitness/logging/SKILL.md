---
name: logging
description: log workouts, lifts, WOD results, running and cardio sessions
---
- **Always save:** Log workout stats and WOD results immediately when Roi reports them — never acknowledge without saving.
- **Runs:** Log a run in one `log_cardio_stats` call (it creates the session if needed). Flag `pain_level >= 2` clearly in your reply.
- **Arbox classes vs standalone workouts:** Gym classes come from Arbox sync — enrich them with `log_wod_result`, `log_exercise_stats`, or `log_cardio_stats`; never call `log_workout` for an Arbox class. Use `log_workout` only for sessions outside Arbox (hotel WODs, travel, other gyms).
- **Forms:** To collect workout or running stats, prefer `send_form` prefilled from history (`query_exercise_history`) over asking in prose. A `[Submitted form ...]` message is Roi reporting stats — log it immediately.
