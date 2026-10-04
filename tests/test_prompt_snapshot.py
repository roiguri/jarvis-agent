"""Snapshots of the system prompt the model sees, per scope.

The memory files Jarvis writes (SOUL.md, USER.md, HEARTBEAT.md, daily logs, the
chat log) are fixed fixtures from tests/fixtures/, and the clock is pinned, so
these files change only when code or the committed prompts change: AGENTS.md,
heartbeat.md, SKILL.md bodies, the framing, or the assembly logic.
"""

import datetime as real_dt
import os
import shutil

import pytest

import agent
import config
from tests.conftest import LOG_DIR
from tests.fakes import frozen_datetime_module
from tools import registry

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
NOW = real_dt.datetime(2026, 3, 10, 9, 30, tzinfo=real_dt.timezone.utc)  # 11:30 Israel time


@pytest.fixture
def fixture_memory(monkeypatch):
    """The fixture memory files and chat log in the scratch root, and agent.py's
    clock pinned to NOW; all removed afterwards."""
    monkeypatch.setattr(agent, "_dt", frozen_datetime_module(NOW))
    src = os.path.join(FIXTURES, "memory")
    copied = []
    for root, _, files in os.walk(src):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), src)
            dest = os.path.join(config.MEMORY_DIR, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy(os.path.join(root, name), dest)
            copied.append(dest)
    shutil.copy(os.path.join(FIXTURES, "logs", "chat_history.jsonl"), LOG_DIR)
    yield
    for path in copied:
        os.remove(path)


def test_user_prompt(fixture_memory, golden):
    golden("prompt_user.md", agent.build_system_prompt("user", set()))


def test_heartbeat_prompt(fixture_memory, golden):
    golden("prompt_heartbeat.md", agent.build_system_prompt("heartbeat", set(), due_tasks=["due-task"]))


def test_skill_block_all_active(golden):
    """Every skill's description and rules body, as the prompt shows them when active."""
    golden("skills_all_active.md", registry.compact_skill_list("user", registry.skill_namespaces()))
