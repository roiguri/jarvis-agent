#!/usr/bin/env python3
"""CI guard: every slash-command reply survives both renderers.

A handler's reply is neutral markdown rendered by whichever channel the command
arrived on, and the two disagree about a bare newline: Telegram's converter is
line-based, CommonMark (the app) treats it as a soft break and flows the lines
into one paragraph. The same defect was fixed three times in isolation (/help,
/usage, ...) before the contract was written down; this asserts it holds for
every command at once, so a fourth recurrence fails here instead of on the
owner's phone.

Runs each registered command against a seeded scratch JARVIS_ROOT and feeds the
reply to `gateway.commands.format.check_reply` — the contract's executable half
(bold header, blank line, real `- ` items; no literal bullets). Replies that are
verbatim file content are marked `raw` below: the handler does not own their
layout, so they are reported as skipped rather than silently passed.

Run:  python3 scripts/ci/check_command_replies.py    (exit 0 = clean, 1 = defect)
Must run in a fresh interpreter — it sets JARVIS_ROOT before the app imports.
"""
import asyncio
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# (command line, mode). "strict" validates the whole reply; "raw" means the reply
# is a file's own content — only its existence is checked. Every registered
# command must appear here at least once (asserted below), so a new command
# cannot join without a decision about how its reply is laid out.
CASES: list[tuple[str, str]] = [
    ("/help", "strict"),
    ("/clear", "strict"),
    ("/skills", "strict"),
    ("/status", "strict"),
    ("/memory", "raw"),            # MEMORY.md verbatim
    ("/memory list", "strict"),
    ("/memory notes.md", "raw"),   # requested file verbatim
    ("/heartbeat", "strict"),      # header + HEARTBEAT.md body — the header is ours
    ("/heartbeat list", "strict"),
    ("/heartbeat morning", "raw"), # per-task notes verbatim
    ("/logs", "raw"),              # daily log verbatim
    ("/logs 32.13", "strict"),     # invalid date → usage help
    ("/usage", "strict"),
    ("/usage week", "strict"),
    ("/usage today heartbeat", "strict"),
    ("/usage 32.13", "strict"),    # invalid date → usage help
    ("/tz", "strict"),
    ("/tz home", "strict"),
    ("/tz Nowhere/Nope", "strict"),  # invalid zone → usage help
    ("/nope", "strict"),           # router's unknown-command reply
]

# Seeded so the list handlers take their non-empty branch and /heartbeat has a
# body to run into. The HEARTBEAT.md seed opens with a plain line on purpose:
# that is exactly what collapses onto the header if the blank line is ever
# dropped again.
MEMORY_SEED = {
    "MEMORY.md": "# Memory index\n\n- notes.md — scratch\n",
    "notes.md": "Some note.\n",
    "HEARTBEAT.md": "Heartbeat tasks.\n\n- morning · every 4h\n",
    "heartbeat/morning.md": "# morning\n\nlast_run: 2026-07-30T06:00:00Z\n",
}

TURNS_SEED = [
    {"scope": "user", "model": "gemini-3-flash-preview", "llm_calls": 2,
     "tool_calls": 1, "input_tokens": 40000, "cache_read_tokens": 12000,
     "output_tokens": 900, "reasoning_tokens": 300, "total_tokens": 40900},
    # A second scope so the rollup has 2+ buckets and renders the breakdown
    # block, and an unpriced model so the '⚠ unpriced' marker is exercised too.
    {"scope": "heartbeat", "model": "gemini-9-not-in-price-table", "llm_calls": 1,
     "tool_calls": 0, "input_tokens": 8000, "cache_read_tokens": 0,
     "output_tokens": 120, "reasoning_tokens": 0, "total_tokens": 8120,
     "no_action": True},
]


def _seed(scratch: str) -> None:
    import agent
    import config
    from observability.telemetry import TURNS_LOG

    # SqliteSaver creates its tables lazily on first use; /clear deletes from
    # them directly, so a never-used scratch db would fail there for a reason
    # that has nothing to do with layout.
    agent.memory.setup()

    for rel, body in MEMORY_SEED.items():
        path = os.path.join(config.MEMORY_DIR, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)

    israel_today = dt.datetime.now(dt.timezone.utc).astimezone(
        ZoneInfo("Asia/Jerusalem")
    ).date()
    daily = os.path.join(config.MEMORY_DIR, "daily", f"daily_{israel_today}.md")
    os.makedirs(os.path.dirname(daily), exist_ok=True)
    with open(daily, "w", encoding="utf-8") as f:
        f.write(f"# {israel_today}\n\n- seeded by check_command_replies\n")

    # Mid-day UTC so the record lands on the same Israel day the rollup asks for
    # regardless of when CI runs.
    ts = dt.datetime.combine(israel_today, dt.time(9, 0), tzinfo=dt.timezone.utc)
    os.makedirs(os.path.dirname(TURNS_LOG), exist_ok=True)
    with open(TURNS_LOG, "w", encoding="utf-8") as f:
        for rec in TURNS_SEED:
            f.write(json.dumps({"ts": ts.isoformat(), **rec}) + "\n")


def _run_cases() -> list[str]:
    from gateway.base import InboundMessage
    from gateway.commands import list_commands, try_handle_command
    from gateway.commands.format import check_reply

    failures: list[str] = []
    skipped: list[str] = []

    registered = {c.name for c in list_commands()}
    covered = {line.split()[0].lstrip("/") for line, _ in CASES}
    for missing in sorted(registered - covered):
        failures.append(
            f"/{missing}: registered but has no case in CASES — add one "
            f"(mode 'strict', or 'raw' if the reply is verbatim file content)"
        )

    for line, mode in CASES:
        inbound = InboundMessage(
            user_id=1, chat_id=1, thread_id="check_command_replies", user_text=line
        )
        reply = asyncio.run(try_handle_command(inbound))
        if not isinstance(reply, str) or not reply.strip():
            failures.append(f"{line}: handler returned {reply!r}")
            continue
        if "failed — check logs" in reply:
            failures.append(f"{line}: handler raised (router caught it) — {reply!r}")
            continue
        if mode == "raw":
            skipped.append(line)
            continue
        for problem in check_reply(reply):
            failures.append(f"{line}: {problem}")

    if skipped:
        print(f"note: layout not checked for verbatim-file replies: {', '.join(skipped)}")
    # A cold scratch root has no active skills, so /skills only ever renders its
    # empty branch here — the nested (active-parent) layout is the helper's
    # concern, not this guard's.
    return failures


def main() -> int:
    assert "config" not in sys.modules and "agent" not in sys.modules, \
        "check_command_replies must run in a fresh interpreter (config/agent not yet imported)"

    scratch = tempfile.mkdtemp(prefix="jarvis-check-replies-")
    try:
        os.makedirs(os.path.join(scratch, "secrets"))
        # agent.py asserts GOOGLE_API_KEY at import; a dummy lets the import
        # complete without real secrets. No handler makes a network call.
        with open(os.path.join(scratch, "secrets", ".env"), "w") as f:
            f.write("GOOGLE_API_KEY=dummy-for-check-command-replies\n")
        os.environ["JARVIS_ROOT"] = scratch
        sys.path.insert(0, REPO_ROOT)

        import tools  # noqa: F401 — populates the registry so /skills and /status see it

        _seed(scratch)
        failures = _run_cases()
        if failures:
            print("FAIL: slash-command replies break the layout contract:")
            for f in failures:
                print("   ", f)
            print(
                "\nBuild replies with gateway/commands/format.py "
                "(section / kv_section / document / join). "
                "Contract: docs/architecture/GATEWAY.md § Reply formatting."
            )
            return 1
        print(f"OK: {len(CASES)} slash-command replies conform to the layout contract")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
