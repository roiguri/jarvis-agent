# Tasks — a todo model with due dates, repeats and linked reminders, plus a jarvis-app mini-app

**Date:** 2026-10-03 · **Status:** design settled in a grilling session (decisions below);
slices not started. **Starts after [COLLECTIONS_PLAN.md](COLLECTIONS_PLAN.md) is complete.**
**Depends on:** [TRIGGERS_PLAN.md](TRIGGERS_PLAN.md) S1 (the trigger store) being in `main`
before T2.
**Goal:** give the owner a real todo list — one pool of tasks with due dates, priorities, tags
and repeats, reminders that know their task, a daily-digest-ready view, and an app screen to
tick things off.

---

## Slices

Each slice ships and is verified on its own.

**T1 — `tasks` skill (agent).**
- [ ] `tools/tasks/_db.py`: `jarvis_data/tasks/tasks.sqlite`, schema below.
- [ ] Tools: `add_task`, `update_task`, `list_tasks`, `delete_task`.
- [ ] Repeat rule + roll-forward on completion; completion history recorded.
- [ ] `list_tasks(view="today")` contract (§4) — the digest and the app's Today tab depend on it.
- [ ] `tags` via the shared field-type module from collections C1.
- [ ] `tools/tasks/SKILL.md`.
- [ ] Verify on staging: add / complete / undo / cancel / delete; each view; a from-due and a
      from-completion repeat rolling forward; tag filter; empty `today` line.

**T2 — Task ↔ reminder link (agent).** Requires triggers S1 in `main`.
- [ ] `add_task(remind_at=…)` / `update_task(remind_at=…)` create, move or clear a trigger that
      references the task id.
- [ ] Completing, cancelling or deleting a task cancels its reminder; a repeat re-arms it at the
      same offset from the next due date.
- [ ] The ping uses the task's current title (a `code` action checking the task is still open, once
      triggers has `code`; until then a `send` cancelled on completion).
- [ ] Verify on staging: a reminder fires; completing first suppresses it; a repeating task's
      reminder re-arms.

**T3 — Tasks app (agent + jarvis-app).**
- [ ] Agent: `gateway/apps/tasks.py` — GET entries per tab, POST entries for quick actions;
      import line in `gateway/apps/specs.py`.
- [ ] jarvis-app: Tasks screen (reuses `AppQueryClient.post()` from collections C3).
- [ ] Verify on staging, then prod after deploy.

**T4 — Daily digest (by talking to Jarvis, no code).**
- [ ] Ask Jarvis to create a heartbeat task (morning window) that calls
      `list_tasks(view="today")` and notifies only when it reports something.
- [ ] Verify: a day with nothing due sends nothing.

---

## 1. Why tasks are their own model

Collections (reading, shopping, books…) are piles of things to consider. A todo item is an
action owed, and everything that makes a todo list useful is about **time**: due dates, overdue,
reminders, repeats, a "today" view, and integration with triggers and the heartbeat. Forcing it
into the generic collection model would either drop that or bolt time onto every list. So tasks
are a separate model, skill and database. See COLLECTIONS_PLAN.md §2.

## 2. Model

**One pool of tasks** — "what do I need to do today" spans everything. No projects, no separate
task lists, no link to the `active_projects.md` memory file.

| Field | Notes |
|---|---|
| `title`, `notes` | notes optional |
| `due_date`, `due_time` | both optional; time only with a date. Information, never a ping |
| `priority` | `high` / `normal` / `low`, default `normal` |
| `tags` | the shared `tags` type (lowercased, open vocabulary); grouping and filtering — the "project" view when wanted |
| `repeat` | optional rule — see below |
| `status` | `open` / `done` / `cancelled` |
| `reminder_trigger_id` | T2; the linked trigger, if any |
| timestamps | `created_at`, `updated_at`, `completed_at` |

No subtasks (steps go in notes, as sibling tasks sharing a tag, or as a collection checklist;
addable later as a `parent_id`). No sections — tasks are laid out by time, and a second fixed
grouping would fight that; tags filter without changing the layout.

### Repeats

- Rule: every N days / weeks / months, or a fixed day (the 1st of the month, every Sunday).
- Anchor: **from due date** (rent is due on the 1st regardless of when it was paid) or **from
  completion** (AC filter every 3 months after it was last changed). The agent picks the obvious
  one; the owner can correct it.
- Completing a repeating task **rolls the same task forward** to its next due date rather than
  closing it. Each completion is recorded (a `completions` table: `task_id`, `completed_at`,
  `due_date` it satisfied), so "when did I last change the filter?" is answerable.

### Lifecycle

Done and cancelled tasks are kept, hidden by default — "what did I finish this week?" stays
answerable. Delete is for mistakes, no confirmation, and cancels any linked reminder.

## 3. Due date vs reminder

Two separate, optional things:

- **Due date** — when it should be done by. Drives the today / upcoming / overdue views. Never
  sends a message on its own (most due dates — "renew passport by December" — don't want one).
- **Reminder** — a ping at a specific time, set only when asked ("remind me Thursday at 9").
  Stored as a trigger in the triggers store that **references the task**:
  - completing / cancelling / deleting the task cancels it — no ping about a paid bill;
  - a repeating task's reminder re-arms with the task;
  - the ping uses the task's current title.

Rejected: due dates auto-creating reminders (noise the owner learns to ignore); reminders
unlinked from tasks as today's `manage_reminder` (stale pings after completion).

## 4. Agent tools (`tasks` skill)

Activatable skill (`tools/tasks/`), not core — daily but not every-turn, and same-turn
activation keeps "add X to my todo" one message. No task data is injected into the system prompt.

| Tool | Does |
|---|---|
| `add_task(title, notes?, due_date?, due_time?, priority?, tags?, repeat?, remind_at?)` | One task, everything settable at creation — "remind me Thursday to pay electricity" is one call. |
| `update_task(task_id, …)` | Edit any field; `status=done/cancelled/open` (completion rolls repeats forward); set / clear `remind_at` and `repeat`. Empty string clears. |
| `list_tasks(view, tag?)` | `today` / `upcoming` / `someday` / `done` / `overdue`; returns ids. |
| `delete_task(task_id)` | Mistakes only; cancels the linked reminder. |

**`view="today"` contract** — the digest and the app's Today tab both read it, so they always
agree:
1. **Overdue** (with days late), then **due today** (timed first), then **reminders firing today**.
2. When there is nothing, the first line says so explicitly ("Nothing due or overdue today.") —
   which is what lets a heartbeat task decide not to notify.
3. Undated tasks never appear in today, regardless of priority — otherwise they'd appear every
   morning forever and the digest stops being read.

Other views: `upcoming` = next 14 days grouped by date (matches the app tab); `someday` = open,
undated, high priority first; `done` = recently closed, newest first; `overdue` alone.

Conventions: tasks addressed by id; list before acting, never guess a title. No read-only SQL
query tool (add if a real question can't be answered by the views).

## 5. Daily digest

The digest is a **heartbeat task the owner creates by asking Jarvis** — no code. This plan's job
is only to supply the tool that makes it easy: `list_tasks(view="today")` with its explicit
empty line. If the trigger gates of TRIGGERS_PLAN land later, the digest is a natural candidate
to move to a code-gated trigger (no model call on empty days); not required here.

## 6. jarvis-app mini-app (`tasks`)

Read + **quick actions only** (same rule as collections; full editing is future work).

```
┌─────────────────────────────────┐
│ Tasks                           │
│ [Today] [Upcoming] [Someday] [Done] │
│ (all) (home) (errand) (jarvis) …│  ← tag filter chips
├─────────────────────────────────┤
│ OVERDUE                         │
│ ☐ Renew passport   ⚠ 2d late    │
│ TODAY                           │
│ ☐ Pay electricity  🔔 09:00 ‼   │
│ ☐ Water plants     🔁           │
├─────────────────────────────────┤
│ [ + Add a task…            ]    │  ← quick-add
└─────────────────────────────────┘
```

- **Today** = `list_tasks(view="today")` content. **Upcoming** = next 14 days by date.
  **Someday** = undated, high priority first. **Done** = recent, newest first, with undo.
- Row: checkbox, title, markers only when relevant (late/due badge, 🔔, 🔁, ‼, tags). Tap →
  read-only detail sheet (notes, due, repeat, reminder, tags) with Done / Delete.
- Quick actions: tick done / undo, delete, **quick-add title only** (no due → lands in Someday;
  no date parsing in the app — "tomorrow" stays a chat request).
- Ticking a repeating task animates it to its next due date.
- Everything else (due dates, reminders, repeats, tags, priority) through chat.
- App POST handlers call the tools' code paths, so a tick from the app rolls repeats forward and
  cancels / re-arms reminders exactly as through Jarvis.
- The app module reads the DB read-only, never parses tool output (as `gateway/apps/travel.py`).

## 7. Decisions (grilling session, 2026-10-03)

1. Tasks are a separate model from collections (lifecycle, not fields).
2. Due date and reminder are separate; reminders are triggers linked to the task.
3. One pool; grouping by **tags**; no projects, no sections, no `active_projects.md` link.
4. Simple repeats from the start, completion rolls forward, anchor from-due or from-completion.
5. Priority in (three levels); subtasks out.
6. Keep finished tasks; delete for mistakes without confirmation.
7. Separate skill and DB from collections; not core; no context injection.
8. Digest = owner-created heartbeat task; the plan supplies `list_tasks(view="today")`, undated
   tasks excluded.
9. Separate Tasks app, quick actions only, layout as §6.
10. Ships after collections is complete (collections C1–C3, then T1–T4).

## 8. Not in scope

Subtasks, projects, natural-language dates in quick-add, in-app full editing, a query tool,
code-gated digest triggers, task ↔ collection conversions.
