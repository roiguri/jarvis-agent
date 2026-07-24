#!/usr/bin/env python3
"""CI guard: the app stays channel-agnostic.

Multi-channel support (Steps 1-2) moved all channel-specific code and config
behind gateway.factory. This asserts that boundary holds — a regression (a tool
docstring naming Telegram, a domain module importing a concrete channel, the
gateway importing back up into the agent) fails here instead of silently
coupling the whole app to one channel again.

Four static checks (pure source scan — no app import, no deps):
  1. No domain module (nor a slash-command handler) imports gateway.channels.* —
     they reach the gateway only through gateway.factory.
  2. No channel adapter or core-contract module in gateway/ imports the
     agent/tools/main/heartbeat layers — a channel is a thin adapter; the host
     injects the coupling. gateway/commands/ is exempt: slash-command handlers
     are the documented app bridge and legitimately call into agent/tools (they
     still may not import a concrete channel — check #1 covers that).
  3. No channel name appears in tools/ — tool docstrings are prompt content, so
     the model must not be told a capability is "a Telegram thing".
  4. No channel name appears in agent.py — domain logic must not special-case a
     channel. The thread-prefix filter that once named 'telegram_' now excludes
     the heartbeat thread by identity instead, naming no channel.

main.py stays out of scope: it is the composition root and legitimately names the
channel it builds (build_stack("telegram", ...)).

Run:  python3 scripts/ci/check_channel_agnostic.py   (exit 0 = clean, 1 = leak)
"""
import ast
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Locations that must never import a concrete channel — they reach the gateway
# only through gateway.factory. Includes gateway/commands/: handlers may import
# agent/tools, but not a concrete channel. The factory (composition root) and
# the channels themselves are deliberately absent.
_NO_CONCRETE_CHANNEL = ("agent.py", "heartbeat.py", "heartbeat_state.py", "main.py", "tools", "gateway/commands")
_FORBIDDEN_IMPORT = "gateway.channels"

# gateway/ is thin adapters + core contracts; it must not import back up into the
# app — EXCEPT gateway/commands/ (the documented slash-command bridge).
# turn_context is a stdlib-only leaf and is deliberately allowed.
_REVERSE_ROOTS = {"agent", "tools", "main", "heartbeat", "heartbeat_state"}
_REVERSE_EXEMPT = os.path.join(REPO_ROOT, "gateway", "commands") + os.sep

# tools/ carries prompt content; no channel may be named there. agent.py is
# domain logic and must not special-case a channel by name either.
_FORBIDDEN_TOOL_TOKENS = ("telegram", "inlinekeyboard")
_FORBIDDEN_AGENT_TOKENS = ("telegram", "jarvis-app")

_SKIP_DIRS = {"__pycache__", ".git", "venv", ".venv", "node_modules"}


def _py_files(*rels):
    for rel in rels:
        p = os.path.join(REPO_ROOT, rel)
        if os.path.isfile(p):
            yield p
        elif os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
                for f in files:
                    if f.endswith(".py"):
                        yield os.path.join(root, f)


def _imports(path):
    """(module, lineno) for every absolute import — via AST, so strings and
    comments are ignored. Relative imports (within a package) can't reach across
    the boundaries we guard, so they are skipped."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module, node.lineno


def _rel(path):
    return os.path.relpath(path, REPO_ROOT)


def check_domain_imports():
    """#1 — no domain module (nor a slash-command handler) imports a channel."""
    leaks = []
    for path in _py_files(*_NO_CONCRETE_CHANNEL):
        for mod, line in _imports(path):
            if mod == _FORBIDDEN_IMPORT or mod.startswith(_FORBIDDEN_IMPORT + "."):
                leaks.append(f"{_rel(path)}:{line} imports {mod} — reach the gateway via gateway.factory")
    return leaks


def check_reverse_imports():
    """#2 — no channel adapter / core contract in gateway/ imports the app layers
    (gateway/commands/ exempt — the documented slash-command bridge)."""
    leaks = []
    for path in _py_files("gateway"):
        if path.startswith(_REVERSE_EXEMPT):
            continue
        for mod, line in _imports(path):
            if mod.split(".")[0] in _REVERSE_ROOTS:
                leaks.append(f"{_rel(path)}:{line} imports {mod} — gateway must stay a thin adapter")
    return leaks


def check_tool_channel_names():
    """#3 — no channel name in tools/ (tool docstrings are prompt content)."""
    leaks = []
    for path in _py_files("tools"):
        with open(path, encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                low = raw.lower()
                for tok in _FORBIDDEN_TOOL_TOKENS:
                    if tok in low:
                        leaks.append(f"{_rel(path)}:{i} names a channel ({tok!r}): {raw.strip()[:80]}")
    return leaks


def check_agent_channel_names():
    """#4 — no channel name in agent.py (domain logic must not special-case a
    channel; the thread-prefix filter excludes the heartbeat thread by identity)."""
    leaks = []
    for path in _py_files("agent.py"):
        with open(path, encoding="utf-8") as fh:
            for i, raw in enumerate(fh, 1):
                low = raw.lower()
                for tok in _FORBIDDEN_AGENT_TOKENS:
                    if tok in low:
                        leaks.append(f"{_rel(path)}:{i} names a channel ({tok!r}): {raw.strip()[:80]}")
    return leaks


def main():
    checks = (
        ("domain code imports a concrete channel (gateway.channels.*)", check_domain_imports),
        ("gateway/ reverse-imports the app", check_reverse_imports),
        ("tools/ names a channel", check_tool_channel_names),
        ("agent.py names a channel", check_agent_channel_names),
    )
    failed = False
    for label, fn in checks:
        leaks = fn()
        if leaks:
            failed = True
            print(f"FAIL: {label}:")
            for leak in leaks:
                print("   ", leak)
    if failed:
        print("\nChannel-specific code and config live behind gateway.factory — keep the app channel-agnostic.")
        return 1
    print("OK: channel-agnostic — import boundaries clean, no channel names in tools/ or agent.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
