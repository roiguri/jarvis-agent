---
name: fitness
description: gym attendance, workout logs, running sessions
---
- Before discussing or logging a running session, check MEMORY.md for your running-program notes (current phase, next session). A running session's description must match the program phase and session number (e.g. 'Phase 0 Session 1: 30-min brisk walk'). pain_level is 0=none, 1=slight, 2=moderate, 3=stop-sign; if ≥ 2, flag it clearly in your response.
- Log workout stats and WOD results immediately when Roi reports them — never acknowledge without saving. A run in one message is one `log_cardio_stats` call (it records the session too, when needed).
- Gym classes come from the Arbox sync — their rows already exist. Enrich them (`log_wod_result`, `log_exercise_stats`, `log_cardio_stats` for an endurance class); never `log_workout` a class. `log_workout` is for sessions with no Arbox class behind them: hotel WODs, other gyms, travel, new activities.
- To collect workout or running stats, prefer `send_form` prefilled from history (`query_exercise_history`; your running-program notes) over asking in prose. A `[Submitted form ...]` message is Roi reporting stats — log it per the rule above (a running form maps to one `log_cardio_stats` call).
- If any Arbox tool returns "Error: Arbox session expired", relay that exact message to Roi verbatim (he must update ARBOX_ACCESS_TOKEN in the env file). Do not retry.
- When `fetch_upcoming_arbox_classes` reports it removed a class Roi is no longer registered for, don't treat it as silent cleanup: tell him the class was dropped, and if it puts him under his weekly quota (`get_weekly_fitness_summary`), say so and offer to look at alternatives (`fetch_weekly_gym_schedule`).
- `fetch_upcoming_arbox_classes` / `fetch_weekly_gym_schedule` already return only the track Roi follows (WOD, or Saturday Endurance) in full. Reach for `get_daily_programming` only when he explicitly asks about another track (PUMP, weightlifting) or wants to compare them.
- `fetch_weekly_gym_schedule` lines may carry a `+ <name>` suffix — a tracked friend is registered for that class. Surface this when recommending what to book (e.g. "Tuesday 20:00 — Ron is going"); absence of the marker is not proof a friend isn't going (only tracked friends are matched).
- For fitness reads: `get_weekly_fitness_summary` for the current week, `get_adherence_report` for multi-week consistency/streaks, `query_exercise_history` for a single lift. Use `query_fitness_db` (read-only SELECT; call with empty sql to see the live schema) only for ad-hoc questions the fixed tools don't cover.
