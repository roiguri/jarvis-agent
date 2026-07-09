"""Heartbeat management tools.

``heartbeat_respond`` is the structured end-of-tick acknowledgement: the
heartbeat runner reads its payload (which tasks were acted on, whether Roi
should be notified) rather than inferring the tick's outcome from reply
text. Bound only in heartbeat scope — a user turn has no tick to
acknowledge.

``manage_heartbeat_task`` is the validated write path for the task list in
HEARTBEAT.md: it parses, mutates one task block, re-validates the whole
candidate before anything touches disk, and gates every change behind an
owner confirmation tap. The read side (heartbeat_state.parse_tasks) stays
lenient; this write side fails loud — a malformed task must be rejected at
authoring time, never silently dropped by the gate later.
"""

from __future__ import annotations

import asyncio
import re

from langchain_core.tools import tool

import heartbeat_state
from observability import telemetry
from tools.registry import tool_register

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CADENCE_INPUT_RE = re.compile(
    r"^(?:every\s+)?(?P<n>\d+)\s*(?P<unit>hours?|days?|h|d)$", re.IGNORECASE
)


def _current_scope() -> str:
    """The running turn's scope, best-effort. Unknown → 'user' (the
    confirmation tap is the backstop either way)."""
    acc = telemetry.TURN_ACC.get()
    return (acc or {}).get("scope") or "user"


def _normalize_cadence(cadence: str) -> str | None:
    """'1h' / 'every 24h' / '7 days' → canonical 'every Nh'/'every Nd',
    or None if unrecognizable."""
    m = _CADENCE_INPUT_RE.match(cadence.strip())
    if not m:
        return None
    n = int(m.group("n"))
    if n <= 0:
        return None
    unit = "d" if m.group("unit").lower().startswith("d") else "h"
    return f"every {n}{unit}"


def _build_block(name: str, cadence: str, due: str, instruction: str) -> list[str]:
    """A canonical task block: header line + two-space-indented body."""
    fields = [f"- **{name}**", cadence]
    if due:
        fields.append(f"due: {due}")
    fields.append(f"notes: `heartbeat/{name.replace('-', '_')}.md`")
    block = [" | ".join(fields)]
    block += [f"  {line}".rstrip() for line in instruction.strip().splitlines()]
    return block


def _render(preamble: list[str], blocks: list[tuple[str, list[str]]]) -> str:
    """Reassemble file text from split_blocks parts, one blank line between
    blocks, preserving the preamble verbatim."""
    parts = []
    if preamble:
        parts.append("\n".join(preamble).rstrip())
    for _, block_lines in blocks:
        parts.append("\n".join(block_lines).rstrip())
    return "\n\n".join(p for p in parts if p) + "\n"


@tool_register(namespace="core", scopes=("heartbeat",))
@tool
def heartbeat_respond(
    acted_tasks: list[str],
    notify: bool,
    summary: str,
    notification_text: str = "",
) -> dict:
    """Report the outcome of this heartbeat tick. Call exactly once, as your
    last tool call of the tick, after all task work is done.

    Args:
        acted_tasks: Exact names (from HEARTBEAT.md) of every task you acted
            on this tick — completed its work and updated its state file.
            Empty list if nothing was due or nothing was done. Do NOT list
            tasks you only checked and skipped.
        notify: True only if Roi should receive a message from this tick.
        summary: One line for the internal log — what this tick did (or why
            nothing was done). Always required.
        notification_text: The user-facing message, required when notify is
            True. Ignored when notify is False. Defaults to summary.
    """
    acted = [name.strip() for name in acted_tasks if name and name.strip()]
    payload = {
        "acted_tasks": acted,
        "notify": bool(notify),
        "summary": summary.strip(),
        "notification_text": (notification_text or summary).strip(),
    }
    return payload


@tool_register(namespace="core", destructive=True)
@tool
def manage_heartbeat_task(
    action: str,
    name: str = "",
    cadence: str = "",
    due: str = "",
    instruction: str = "",
) -> str:
    """Create, update, delete or list the recurring heartbeat tasks in
    HEARTBEAT.md. Use this — never write_memory — to change the task list.

    Use for RECURRING or CONDITIONAL proactive wishes ("check in after my
    workouts", "every Sunday summarize my week"). For a one-shot ping at a
    fixed moment ("remind me at 15:00 to call") use manage_reminder instead.

    Every create/update/delete shows Roi a confirmation with the exact
    change and only lands after he approves. Invalid input (bad cadence,
    bad due window, duplicate/unknown name) is rejected with the reason and
    the file stays untouched.

    Args:
        action: "create" | "update" | "delete" | "list".
        name: kebab-case task name, e.g. "post-class-checkin". Required for
            create/update/delete.
        cadence: how often the task should be considered, e.g. "1h", "24h",
            "7d". Required for create; optional on update (empty = keep).
        due: optional time window (Israel time) outside which the task never
            runs, e.g. "06:00-22:00", "Tue,Sat 20:30±3h", "09:00±2h".
            Empty on update keeps the current window; "none" removes it.
        instruction: what to do when the task is due — free prose, may be
            multi-line. Required for create; optional on update (empty = keep).
    """
    action = action.strip().lower()
    if action == "list":
        tasks = heartbeat_state.parse_tasks()
        if not tasks:
            return "No heartbeat tasks found."
        lines = []
        for t in tasks:
            cadence_s = "?" if t.cadence is None else str(t.cadence)
            due_s = f" | due: {t.due}" if t.due else ""
            lines.append(f"- {t.name} | every {cadence_s}{due_s}")
        return "Current heartbeat tasks:\n" + "\n".join(lines)

    if action not in ("create", "update", "delete"):
        return f"Error: unknown action '{action}'. Use create, update, delete or list."
    if not _NAME_RE.match(name.strip()):
        return (
            f"Error: invalid task name {name!r} — use kebab-case "
            "(lowercase letters/digits and single hyphens), e.g. 'post-class-checkin'."
        )
    name = name.strip()
    if action == "create" and _current_scope() == "heartbeat":
        return (
            "Error: heartbeat ticks may not create new tasks (update/delete/list "
            "only). Propose the new task to Roi in chat instead."
        )

    try:
        with open(heartbeat_state.HEARTBEAT_PATH, encoding="utf-8") as f:
            current_text = f.read()
    except FileNotFoundError:
        current_text = "# Heartbeat Tasks\n"
    except OSError as e:
        return f"Error reading HEARTBEAT.md: {e}"
    preamble, blocks = heartbeat_state.split_blocks(current_text)
    existing = {n for n, _ in blocks}

    if action == "delete":
        if name not in existing:
            return f"Error: no task named '{name}' in HEARTBEAT.md."
        removed = next(b for n, b in blocks if n == name)
        new_blocks = [(n, b) for n, b in blocks if n != name]
        new_text = _render(preamble, new_blocks)
        description = (
            f"Delete heartbeat task '{name}':\n\n" + "\n".join(removed)
            + "\n\n(Its notes file in heartbeat/ is kept and can be cleaned up separately.)"
        )
        ok_text = f"Heartbeat task '{name}' deleted."
        cancel_text = f"Deletion cancelled — '{name}' unchanged."
    else:
        if action == "create":
            if name in existing:
                return f"Error: task '{name}' already exists. Use action='update'."
            if not instruction.strip():
                return "Error: 'instruction' is required for create."
            norm_cadence = _normalize_cadence(cadence)
            if norm_cadence is None:
                return (
                    f"Error: unrecognizable cadence {cadence!r}. "
                    "Use forms like '1h', '24h', '7d'."
                )
            new_due = due.strip()
        else:  # update
            if name not in existing:
                return f"Error: no task named '{name}' in HEARTBEAT.md."
            prior = next(
                t for t in heartbeat_state.parse_tasks_text(current_text) if t.name == name
            )
            prior_block = next(b for n, b in blocks if n == name)
            if cadence.strip():
                norm_cadence = _normalize_cadence(cadence)
                if norm_cadence is None:
                    return (
                        f"Error: unrecognizable cadence {cadence!r}. "
                        "Use forms like '1h', '24h', '7d'."
                    )
            elif prior.cadence is not None:
                total_h = int(prior.cadence.total_seconds() // 3600)
                norm_cadence = (
                    f"every {total_h // 24}d" if total_h % 24 == 0 and total_h >= 24
                    else f"every {total_h}h"
                )
            else:
                return (
                    f"Error: '{name}' has no parseable cadence to keep — "
                    "pass an explicit cadence."
                )
            if due.strip().lower() == "none":
                new_due = ""
            elif due.strip():
                new_due = due.strip()
            else:
                new_due = prior.due or ""
            if not instruction.strip():
                instruction = "\n".join(
                    line[2:] if line.startswith("  ") else line
                    for line in prior_block[1:]
                ).strip()
                if not instruction:
                    return f"Error: '{name}' has no body to keep — pass an instruction."
        if new_due and heartbeat_state.parse_window(new_due) is None:
            return (
                f"Error: unparseable due window {new_due!r}. Use forms like "
                "'06:00-22:00', 'Tue,Sat 20:30±3h', '09:00±2h' (or 'none' to clear)."
            )

        new_block = _build_block(name, norm_cadence, new_due, instruction)
        if action == "create":
            new_blocks = blocks + [(name, new_block)]
        else:
            new_blocks = [(n, b if n != name else new_block) for n, b in blocks]
        new_text = _render(preamble, new_blocks)
        description = (
            f"{'Create' if action == 'create' else 'Update'} heartbeat task "
            f"'{name}':\n\n" + "\n".join(new_block)
        )
        ok_text = f"Heartbeat task '{name}' {'created' if action == 'create' else 'updated'}."
        cancel_text = f"{action.capitalize()} cancelled — HEARTBEAT.md unchanged."

    # Validate the full candidate exactly as the gate will read it: the
    # mutated task must round-trip with a parseable cadence (+ window when
    # set), and every other task must survive untouched.
    parsed = {t.name: t for t in heartbeat_state.parse_tasks_text(new_text)}
    if action == "delete":
        expected_names = existing - {name}
    else:
        expected_names = existing | {name}
        t = parsed.get(name)
        if t is None or t.cadence is None or (new_due and t.window is None):
            return (
                "Error: internal validation failed — the resulting task would not "
                "parse cleanly. HEARTBEAT.md was not modified."
            )
    if set(parsed) != expected_names:
        return "Error: internal validation failed — other tasks would be disturbed."

    from gateway.factory import get_confirmation
    from tools.core.memory import _exec_write_memory

    async def _do_write() -> str:
        return await asyncio.to_thread(_exec_write_memory, "HEARTBEAT.md", new_text)

    try:
        return get_confirmation().request_confirmation_sync(
            description=description,
            action_fn=_do_write,
            result_ok_text=ok_text,
            result_cancel_text=cancel_text,
        )
    except Exception as e:
        return f"Error requesting confirmation: {e}"
