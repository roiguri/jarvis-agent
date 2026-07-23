import asyncio
import difflib
import os
import tempfile
import threading

from langchain_core.tools import tool

import config
from tools.registry import tool_register

# Serializes concurrent memory writes. Jarvis's racing writers are the user
# turn and the heartbeat turn — both `asyncio.to_thread(ask_jarvis, …)` in the
# SAME process — so a process-wide threading.Lock is correct and sufficient.
# INVARIANT: this holds only while memory has a single writer *process*. If a
# second memory-writing process is ever introduced (e.g. heartbeat split out,
# multi-worker), this must become an fcntl.flock on a lock file under
# /app/jarvis_data/locks/ — a threading.Lock does not cross processes.
_WRITE_LOCK = threading.Lock()

# Sandbox root is read from config per call (config.MEMORY_DIR), not bound to a
# module constant here — that keeps the seam a test can repoint with one setattr.

# Files the agent cannot delete — structural/identity files.
_PROTECTED_FILES = {"SOUL.md", "HEARTBEAT.md", "MEMORY.md", "USER.md"}

# The LangGraph checkpointer DB lives in MEMORY_DIR because LangGraph owns its
# path and it cannot be relocated. It is NOT memory: the agent must never read,
# overwrite, or delete its own conversation-state DB. Blocked here so every
# memory tool (read/write/delete all route through _get_safe_path) is covered;
# the -wal/-shm/journal sidecars share the prefix.
_DENIED_PREFIX = "threads.sqlite"


def _get_safe_path(filename: str) -> str:
    """Validate filename and return absolute path within MEMORY_DIR.

    Prevents directory traversal attacks. Subdirectories are allowed as long as
    the resolved path stays within MEMORY_DIR. The checkpointer DB
    (``threads.sqlite*``) is deny-listed even though it lives in MEMORY_DIR.
    """
    safe_path = os.path.abspath(os.path.join(config.MEMORY_DIR, filename))
    if not safe_path.startswith(config.MEMORY_DIR + os.sep) and safe_path != config.MEMORY_DIR:
        raise ValueError("Security Violation: Attempted access outside of sandboxed memory directory.")
    if os.path.basename(safe_path).startswith(_DENIED_PREFIX):
        raise ValueError("'threads.sqlite' is the conversation-state database, not memory — access denied.")
    return safe_path


def _canonical_name(filename: str) -> str:
    """The MEMORY_DIR-relative form of ``filename``.

    Protection checks must compare against this, not the raw argument —
    aliases like './SOUL.md' or 'daily/../SOUL.md' resolve to a protected
    file without spelling its name. Raises ValueError like _get_safe_path.
    """
    return os.path.relpath(_get_safe_path(filename), config.MEMORY_DIR)


# A preview is surfaced to the owner in a single confirmation prompt and is
# also echoed into the LLM context several times, so it must stay bounded
# regardless of which channel renders it. The cap is intentionally
# conservative; channel-specific delivery limits (message size, escaping,
# formatting) are the channel's concern, not this tool's.
_PREVIEW_BUDGET = 2500


def _truncate(text: str, budget: int = _PREVIEW_BUDGET) -> str:
    if len(text) <= budget:
        return text
    shown = text[:budget].rstrip()
    hidden = text[budget:].count("\n") + 1
    return f"{shown}\n… (truncated — {hidden} more line(s))"


def _sanitize_fence(s: str) -> str:
    """Neutralize triple-backticks inside previewed content.

    The preview wraps content in a ``` fenced block so the channel renders it
    monospace. If the content itself contains ``` it would close our fence
    early and corrupt the prompt. Insert a zero-width space between backticks
    so it can no longer match a fence line — visually ~unchanged. Only the
    on-screen preview is affected; the file/body actually written is the
    untouched original.
    """
    zwsp = chr(0x200B)  # zero-width space
    return s.replace("```", f"`{zwsp}`{zwsp}`")


def _fenced(body: str, lang: str = "") -> str:
    """Wrap previewed content in a neutral Markdown fence (channel renders it
    monospace). Backtick-safe; trailing newlines are trimmed so a file that
    ends in '\\n' doesn't add a blank line before the closing fence."""
    return f"```{lang}\n{_sanitize_fence(body).rstrip(chr(10))}\n```"


def _diff_preview(filename: str, new_content: str) -> str:
    """Unified diff between the on-disk file and the proposed new content.

    Lets the owner review the exact change before approving an overwrite.
    Best-effort: never raises — a preview failure must not block the action.
    """
    try:
        path = _get_safe_path(filename)
        existed = os.path.exists(path)
        old = ""
        if existed:
            with open(path, "r", encoding="utf-8") as f:
                old = f.read()
    except Exception as e:  # noqa: BLE001 — preview is best-effort
        return f"(could not read current '{filename}' for diff: {e})"

    if not existed:
        return (
            "New file (no prior content). Full new content:\n"
            + _fenced(_truncate(new_content))
        )
    if old == new_content:
        return "No changes — proposed content is identical to the current file."
    diff = difflib.unified_diff(
        old.splitlines(),
        new_content.splitlines(),
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
        lineterm="",
    )
    return _fenced(_truncate("\n".join(diff)), lang="diff")


def _delete_preview(path: str, filename: str) -> str:
    """Size + content of a file about to be permanently deleted."""
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return (
            f"{lines} line(s), {size} byte(s). Content:\n"
            + _fenced(_truncate(content))
        )
    except Exception as e:  # noqa: BLE001 — preview is best-effort
        return f"(could not read '{filename}' for preview: {e})"


@tool_register(namespace="core")
@tool
def write_memory(filename: str, content: str) -> str:
    """Write information to a memory file. Creates or overwrites the file.

    SOUL.md is protected: writing to it requests owner confirmation (showing
    a diff of the change) and only proceeds after Roi approves.
    HEARTBEAT.md cannot be written directly — change tasks via
    manage_heartbeat_task.

    Args:
        filename: File name or relative path (e.g., 'user_prefs.txt', 'daily/daily_2026-05-08.md').
        content: The exact text to save.
    """
    try:
        filename = _canonical_name(filename)
    except ValueError as e:
        return f"Error: {e}"
    if filename == "HEARTBEAT.md":
        return (
            "Error: HEARTBEAT.md is managed through manage_heartbeat_task — "
            "use it to create, update or delete heartbeat tasks (validated "
            "before write). Direct writes are not allowed."
        )
    if filename == "SOUL.md":
        from gateway.factory import get_confirmation

        async def _do_write() -> str:
            return await asyncio.to_thread(_exec_write_memory, filename, content)

        try:
            return get_confirmation().request_confirmation_sync(
                description=(
                    "Overwrite SOUL.md (Jarvis identity file).\n\n"
                    + _diff_preview("SOUL.md", content)
                ),
                action_fn=_do_write,
                result_ok_text="SOUL.md updated. Changes take effect after the next service restart.",
                result_cancel_text="SOUL.md update cancelled — identity file unchanged.",
            )
        except Exception as e:
            return f"Error requesting SOUL.md confirmation: {e}"

    return _exec_write_memory(filename, content)


def _exec_write_memory(filename: str, content: str) -> str:
    try:
        path = _get_safe_path(filename)
        dir_ = os.path.dirname(path)
        os.makedirs(dir_, exist_ok=True)
        # Atomic + serialized: write a temp file in the same dir, then
        # os.replace() (atomic rename on one filesystem) so a reader/crash
        # never sees a truncated file; the lock serializes the user vs
        # heartbeat threads racing the same file (e.g. today's daily log).
        with _WRITE_LOCK:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(
                    "w", dir=dir_, delete=False, encoding="utf-8", suffix=".tmp"
                ) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                os.replace(tmp_path, path)
            except Exception:
                # The agent is told the write failed and will likely retry;
                # remove our temp so failed attempts don't accumulate stray
                # .tmp files in the memory surface.
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                raise
        return f"Successfully saved memory to {filename}"
    except Exception as e:
        return f"Error writing memory: {str(e)}"


@tool_register(namespace="core")
@tool
def read_memory(filename: str) -> str:
    """Read information from a previously saved memory file.

    Args:
        filename: File name or relative path (e.g., 'user_prefs.txt', 'daily/daily_2026-05-08.md').
    """
    try:
        path = _get_safe_path(filename)
        if not os.path.exists(path):
            return f"Memory file '{filename}' does not exist."
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading memory: {str(e)}"


@tool_register(namespace="core")
@tool
def list_memory() -> str:
    """List all memory files (.txt and .md), including files in subdirectories.

    Use read_memory("MEMORY.md") for a quick categorised overview instead of
    reading every file individually.
    """
    try:
        result = []
        for root, dirs, files in os.walk(config.MEMORY_DIR):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for f in sorted(files):
                if f.endswith(('.txt', '.md')):
                    rel_path = os.path.relpath(os.path.join(root, f), config.MEMORY_DIR)
                    result.append(f"- {rel_path}")
        return "Memory files:\n" + "\n".join(result) if result else "No memory files found."
    except Exception as e:
        return f"Error listing memory: {str(e)}"


@tool_register(namespace="core", destructive=True)
@tool
def delete_memory(filename: str) -> str:
    """Permanently delete a memory file. Requests owner confirmation (showing a content preview) before executing.

    Use when a file is stale, empty, or explicitly no longer needed.
    Protected files (cannot be deleted): SOUL.md, HEARTBEAT.md, MEMORY.md, USER.md.

    Args:
        filename: The name of the file to delete (e.g., 'old_notes.txt').
    """
    try:
        filename = _canonical_name(filename)
    except ValueError as e:
        return f"Error: {e}"
    if filename in _PROTECTED_FILES:
        return f"Error: '{filename}' is protected and cannot be deleted."
    path = _get_safe_path(filename)
    if not os.path.exists(path):
        return f"Memory file '{filename}' does not exist."

    from gateway.factory import get_confirmation

    async def _do_delete() -> str:
        return await asyncio.to_thread(_exec_delete_memory, path, filename)

    try:
        return get_confirmation().request_confirmation_sync(
            description=(
                f"Permanently delete memory file '{filename}'.\n\n"
                + _delete_preview(path, filename)
            ),
            action_fn=_do_delete,
            result_ok_text=f"'{filename}' deleted.",
            result_cancel_text=f"Deletion of '{filename}' cancelled.",
        )
    except Exception as e:
        return f"Error requesting delete confirmation: {e}"


def _exec_delete_memory(path: str, filename: str) -> str:
    try:
        os.remove(path)
        return f"'{filename}' removed from disk."
    except OSError as e:
        return f"Delete failed: {e}"
