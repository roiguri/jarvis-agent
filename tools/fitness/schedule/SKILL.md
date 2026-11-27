---
name: schedule
description: Arbox class bookings, weekly gym schedule, daily programming
---
- **Arbox token expired:** If any Arbox tool returns `"Error: Arbox session expired"`, relay that exact message verbatim (Roi must update `ARBOX_ACCESS_TOKEN`). Do not retry.
- **Dropped classes:** When `fetch_upcoming_arbox_classes` removes a class Roi is no longer booked for, tell him it was dropped; if it puts him under quota (`get_weekly_fitness_summary`), offer alternatives via `fetch_weekly_gym_schedule`.
- **Default track vs other tracks:** `fetch_upcoming_arbox_classes` and `fetch_weekly_gym_schedule` already return Roi's track (WOD / Saturday Endurance) in full. Call `get_daily_programming` only when he explicitly asks about another track (PUMP, weightlifting) or wants to compare tracks.
- **Tracked friends:** Lines with `+ <name>` mean a tracked friend is booked — surface this when recommending classes (e.g. "Tuesday 20:00 — Ron is going"). Absence of the marker doesn't mean a friend isn't going.
