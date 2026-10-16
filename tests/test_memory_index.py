"""MEMORY.md in the prompt (both scopes, after USER.md), and the prompt-side
ceiling on the injected core memory files."""

import logging
import os

import pytest

import agent
import config


@pytest.fixture
def core_files():
    written = []

    def write(name, body):
        path = os.path.join(config.MEMORY_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        written.append(path)

    yield write
    for path in written:
        os.remove(path)


@pytest.mark.parametrize("scope", ["user", "heartbeat"])
def test_index_follows_user_md_in_both_scopes(core_files, scope):
    core_files("USER.md", "Prefers metric units.")
    core_files("MEMORY.md", "- reading_list.md — books to read")
    prompt = agent.build_system_prompt(scope, set(), [] if scope == "heartbeat" else None)
    assert "--- MEMORY.md (index of memory files) ---\n- reading_list.md — books to read" in prompt
    assert prompt.index("Prefers metric units.") < prompt.index("--- MEMORY.md")


def test_no_index_section_without_the_file():
    assert "--- MEMORY.md" not in agent.build_system_prompt("user", set(), None)


def test_a_file_far_past_its_target_is_cut_in_the_prompt(core_files, caplog):
    limit = agent.CORE_FILE_TARGETS["MEMORY.md"] * agent.CORE_FILE_CEILING
    core_files("MEMORY.md", "x" * (limit + 50))
    with caplog.at_level(logging.WARNING):
        prompt = agent.build_system_prompt("user", set(), None)
    assert "x" * limit + "\n[truncated — consolidation pending]" in prompt
    assert "x" * (limit + 1) not in prompt
    assert "over its prompt ceiling" in caplog.text


def test_a_file_over_target_but_under_the_ceiling_is_whole(core_files):
    body = "y" * (agent.CORE_FILE_TARGETS["USER.md"] + 500)
    core_files("USER.md", body)
    prompt = agent.build_system_prompt("user", set(), None)
    assert body in prompt and "truncated" not in prompt
