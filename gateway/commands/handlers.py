"""Slash-command handlers. Each function returns the reply text shown to
the user; the router (`gateway/commands/router.py`) drives dispatch.

Handlers reach into `agent` for the LangGraph executor and the shared sqlite
connection — that's the gateway↔agent boundary, not the channel↔gateway
boundary, so it's fine. No handler may import a concrete channel module.
"""

from __future__ import annotations

import asyncio
import logging

from gateway.base import InboundMessage
from gateway.commands.router import command, list_commands

logger = logging.getLogger(__name__)


@command("help", "List available slash commands")
async def _help(inbound: InboundMessage, args: list[str]) -> str:
    lines = ["**Available commands:**"]
    for c in list_commands():
        lines.append(f"/{c.name} — {c.description}")
    return "\n".join(lines)


@command("clear", "Reset this conversation (wipe agent memory for this thread)")
async def _clear(inbound: InboundMessage, args: list[str]) -> str:
    import agent

    def _wipe() -> None:
        with agent.conn:
            agent.conn.execute(
                "DELETE FROM checkpoints WHERE thread_id = ?", (inbound.thread_id,)
            )
            agent.conn.execute(
                "DELETE FROM writes WHERE thread_id = ?", (inbound.thread_id,)
            )

    await asyncio.to_thread(_wipe)
    return "Conversation reset. Starting fresh."


def _active_skills(thread_id: str) -> set[str]:
    import agent

    try:
        state = agent.agent_executor.get_state(
            {"configurable": {"thread_id": thread_id}}
        )
    except Exception:
        logger.exception("get_state failed for thread %s", thread_id)
        return set()
    values = getattr(state, "values", {}) or {}
    return set(values.get("active_skills") or [])


@command("skills", "Show active and available skills")
async def _skills(inbound: InboundMessage, args: list[str]) -> str:
    """Mirrors registry.compact_skill_list's discovery convention: top-level
    skills are always listed; sub-skills indent under their parent only when
    the parent is active. Active sub-skills with an inactive parent appear as
    their own top-level entry in the Active section so nothing active is
    invisible to the user."""
    from tools import registry

    active = _active_skills(inbound.thread_id)
    all_ns = sorted(registry.skill_namespaces())
    top = [n for n in all_ns if "/" not in n]
    children: dict[str, list[str]] = {}
    for n in all_ns:
        if "/" in n:
            children.setdefault(n.split("/", 1)[0], []).append(n)

    def _line(ns: str, indent: int = 0) -> str:
        desc, _ = registry._skill_meta(ns)
        return f"{'  ' * indent}- {ns}: {desc or '(no description)'}"

    # Active section — every active namespace must appear here.
    active_lines: list[str] = []
    for ns in top:
        if ns in active:
            active_lines.append(_line(ns))
            for child in children.get(ns, []):
                if child in active:
                    active_lines.append(_line(child, indent=1))
    # Standalone-active sub-skills (parent not active) shown flat so they
    # aren't hidden from the user.
    for ns in all_ns:
        if "/" in ns and ns in active and ns.split("/", 1)[0] not in active:
            active_lines.append(_line(ns))

    # Available section — top-level always; sub-skills only under active parents
    # (mirroring compact_skill_list's two-step discovery). Active top-level
    # skills get a back-reference instead of being re-listed in full.
    available_lines: list[str] = []
    for ns in top:
        if ns in active:
            available_lines.append(f"- {ns} (active — see above)")
            for child in children.get(ns, []):
                if child not in active:
                    available_lines.append(_line(child, indent=1))
        else:
            available_lines.append(_line(ns))

    parts = ["**Active skills:**"]
    parts.append("\n".join(active_lines) if active_lines else "(none)")
    parts.append("")
    parts.append("**Available skills:**")
    parts.append("\n".join(available_lines) if available_lines else "(none)")
    return "\n".join(parts)


@command("status", "Show runtime status (model, scope, active skills, tools)")
async def _status(inbound: InboundMessage, args: list[str]) -> str:
    import agent
    from tools import registry

    core_n, skill_n, ns_n = registry.registered_counts()
    active = _active_skills(inbound.thread_id)
    model_name = getattr(agent.llm, "model", "unknown")
    lines = [
        "**Jarvis status:**",
        f"- model: {model_name}",
        f"- scope: user",
        f"- thread: {inbound.thread_id}",
        f"- active skills: {', '.join(sorted(active)) if active else 'none'}",
        f"- tools registered: {core_n} core + {skill_n} skill across {ns_n} namespace(s)",
    ]
    return "\n".join(lines)


def _memory_list_top_level() -> str:
    """Top-level memory files only — excludes daily/ (use /logs) and heartbeat/
    (use /heartbeat list) subdirs, which have their own surfaces."""
    import os
    import config

    try:
        entries = sorted(
            f for f in os.listdir(config.MEMORY_DIR)
            if f.endswith((".txt", ".md"))
            and os.path.isfile(os.path.join(config.MEMORY_DIR, f))
        )
    except OSError as e:
        return f"Error listing memory: {e}"
    if not entries:
        return "No top-level memory files."
    return "**Memory files:**\n" + "\n".join(f"- {e}" for e in entries)


@command(
    "memory",
    "Show MEMORY.md index. Sub: /memory list, /memory <filename>",
)
async def _memory(inbound: InboundMessage, args: list[str]) -> str:
    from tools.core.memory import read_memory

    if args == ["list"]:
        return await asyncio.to_thread(_memory_list_top_level)
    if args:
        return await asyncio.to_thread(read_memory.invoke, {"filename": " ".join(args)})
    return await asyncio.to_thread(read_memory.invoke, {"filename": "MEMORY.md"})


def _heartbeat_list_tasks() -> str:
    """Per-task state files under heartbeat/ — the curated task list lives in
    HEARTBEAT.md (shown by /heartbeat with no args)."""
    import os
    import config

    hb_dir = os.path.join(config.MEMORY_DIR, "heartbeat")
    try:
        entries = sorted(
            f for f in os.listdir(hb_dir)
            if f.endswith(".md") and os.path.isfile(os.path.join(hb_dir, f))
        )
    except FileNotFoundError:
        return "No heartbeat task state files yet."
    except OSError as e:
        return f"Error listing heartbeat tasks: {e}"
    if not entries:
        return "No heartbeat task state files yet."
    return "**Heartbeat task state files:**\n" + "\n".join(f"- {e}" for e in entries)


@command(
    "heartbeat",
    "Show HEARTBEAT.md task list. Sub: /heartbeat list, /heartbeat <task>",
)
async def _heartbeat(inbound: InboundMessage, args: list[str]) -> str:
    import agent
    from tools.core.memory import read_memory

    if args == ["list"]:
        return await asyncio.to_thread(_heartbeat_list_tasks)
    if args:
        name = " ".join(args)
        if not name.endswith(".md"):
            name = f"{name}.md"
        return await asyncio.to_thread(
            read_memory.invoke, {"filename": f"heartbeat/{name}"}
        )

    body = await asyncio.to_thread(agent.load_or_blank, agent._HEARTBEAT_MD_PATH)
    if not body:
        return "HEARTBEAT.md is empty or unreadable."
    return f"**HEARTBEAT.md:**\n{body}"


def _parse_log_date(arg: str) -> str | None:
    """Resolve a /logs date arg to an ISO date string (YYYY-MM-DD).

    Accepts:
      - 'today' / 'yesterday'
      - EU format with day first: D, D.M, D.M.Y (also accepts '/' for '.')
      - Missing components default to the current month/year (Israel TZ).

    Returns None on invalid input."""
    import datetime as _dt
    from zoneinfo import ZoneInfo

    israel_today = _dt.datetime.now(_dt.timezone.utc).astimezone(
        ZoneInfo("Asia/Jerusalem")
    ).date()

    lowered = arg.lower()
    if lowered == "today":
        return israel_today.isoformat()
    if lowered == "yesterday":
        return (israel_today - _dt.timedelta(days=1)).isoformat()

    # Bare day (no delimiter): use current month + year.
    if arg.isdigit():
        try:
            return _dt.date(israel_today.year, israel_today.month, int(arg)).isoformat()
        except ValueError:
            return None

    # Delimited forms: D.M, D.M.Y, D/M, D/M/Y. Missing month → current month;
    # missing year → current year. Empty components are rejected as malformed.
    for sep in (".", "/"):
        if sep in arg:
            parts = arg.split(sep)
            if len(parts) not in (2, 3) or any(not p for p in parts):
                return None
            try:
                nums = [int(p) for p in parts]
            except ValueError:
                return None
            if len(nums) == 2:
                d, m = nums
                y = israel_today.year
            else:
                d, m, y = nums
            try:
                return _dt.date(y, m, d).isoformat()
            except ValueError:
                return None
    return None


@command(
    "logs",
    "Show today's daily log. Sub: /logs today|yesterday|D[.M[.Y]]",
)
async def _logs(inbound: InboundMessage, args: list[str]) -> str:
    from tools.core.memory import read_memory

    if not args:
        iso = _parse_log_date("today")
    else:
        iso = _parse_log_date(" ".join(args).strip())
        if iso is None:
            return (
                "Invalid date. Try /logs, /logs today, /logs yesterday, "
                "/logs 21 (this month), /logs 21.5, or /logs 21.5.2026 "
                "(also accepts '/' as the separator)."
            )

    return await asyncio.to_thread(
        read_memory.invoke, {"filename": f"daily/daily_{iso}.md"}
    )


_USAGE_USAGE = (
    "Usage: /usage [today|yesterday|week|D[.M[.Y]]] [user|heartbeat]\n"
    "Examples: /usage, /usage yesterday, /usage week, /usage week user, /usage 21.5"
)


@command(
    "usage",
    "Show LLM token / cost rollup. Sub: /usage today|yesterday|week|D[.M[.Y]] [user|heartbeat]",
)
async def _usage(inbound: InboundMessage, args: list[str]) -> str:
    """Rollup of turns.jsonl over a date range, optionally scope-filtered.

    Trailing 'user' or 'heartbeat' token narrows the rollup to that scope.
    Range token: today (default), yesterday, week (last 7 days incl. today),
    or a /logs-style date (D, D.M, D.M.Y, '/'-or-'.' separator).
    """
    from observability import (
        summarize_usage,
        format_usage_table,
        israel_day_range,
        israel_last_n_days,
    )

    tokens = [a.lower() for a in args]
    scope_filter: str | None = None
    if tokens and tokens[-1] in ("user", "heartbeat"):
        scope_filter = tokens.pop()
    range_token = tokens[0] if tokens else "today"

    if range_token == "week":
        since, until = israel_last_n_days(7)
        group_by = "day" if scope_filter else "day+scope"
        title = "**Usage — last 7 days**" + (f" ({scope_filter})" if scope_filter else "")
    else:
        iso = _parse_log_date(range_token)
        if iso is None:
            return f"Invalid date.\n{_USAGE_USAGE}"
        since, until = israel_day_range(iso)
        group_by = "day" if scope_filter else "scope"
        title = f"**Usage — {iso}**" + (f" ({scope_filter})" if scope_filter else "")

    rows = await asyncio.to_thread(
        summarize_usage,
        since=since, until=until,
        group_by=group_by, scope_filter=scope_filter,
    )
    return format_usage_table(rows, title=title)
