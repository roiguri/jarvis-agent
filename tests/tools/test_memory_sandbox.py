"""The memory tools' sandbox: every path stays inside MEMORY_DIR (traversal and
symlinks included), the checkpointer DB is off limits, and the protected files
are guarded under any spelling of their name."""

import asyncio
import os

import pytest

import config
from tools.core import memory


class FakeConfirmation:
    """Records confirmation requests; the action runs only if the test confirms."""

    def __init__(self):
        self.requests = []

    def request_confirmation_sync(self, description, action_fn, result_ok_text, result_cancel_text):
        self.requests.append({"description": description, "action": action_fn})
        return "Confirmation requested."

    def confirm_last(self):
        return asyncio.run(self.requests[-1]["action"]())


@pytest.fixture
def mem(tmp_path, monkeypatch):
    """A fresh memory dir with the protected files, plus an outside dir for escapes."""
    root = tmp_path / "memory"
    (root / "daily").mkdir(parents=True)
    for name in ("SOUL.md", "USER.md", "MEMORY.md", "HEARTBEAT.md"):
        (root / name).write_text(f"original {name}", encoding="utf-8")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("outside the sandbox", encoding="utf-8")
    monkeypatch.setattr(config, "MEMORY_DIR", str(root))
    return root


@pytest.fixture
def confirmation(monkeypatch):
    from gateway import factory

    fake = FakeConfirmation()
    monkeypatch.setattr(factory, "get_confirmation", lambda: fake)
    return fake


# --- _get_safe_path -------------------------------------------------------------

@pytest.mark.parametrize("name", ["notes.md", "daily/daily_2026-03-10.md", "./notes.md", "daily/../notes.md"])
def test_paths_inside_resolve(mem, name):
    assert memory._get_safe_path(name).startswith(str(mem) + os.sep)


@pytest.mark.parametrize("name", ["../outside/secret.txt", "daily/../../outside/secret.txt",
                                  "/etc/passwd", "..", "../memory2/x.md"])
def test_paths_outside_refused(mem, name):
    with pytest.raises(ValueError, match="outside of sandboxed"):
        memory._get_safe_path(name)


def test_symlink_out_of_the_sandbox_refused(mem, tmp_path):
    (mem / "link.txt").symlink_to(tmp_path / "outside" / "secret.txt")
    (mem / "linkdir").symlink_to(tmp_path / "outside", target_is_directory=True)
    for name in ("link.txt", "linkdir/secret.txt"):
        with pytest.raises(ValueError, match="outside of sandboxed"):
            memory._get_safe_path(name)
    assert memory.read_memory.invoke({"filename": "link.txt"}).startswith("Error reading memory")


def test_symlink_inside_the_sandbox_allowed(mem):
    (mem / "alias.md").symlink_to(mem / "USER.md")
    assert memory.read_memory.invoke({"filename": "alias.md"}) == "original USER.md"


@pytest.mark.parametrize("name", ["threads.sqlite", "threads.sqlite-wal", "threads.sqlite-shm",
                                  "./threads.sqlite", "daily/threads.sqlite"])
def test_checkpointer_db_denied(mem, name):
    with pytest.raises(ValueError, match="conversation-state database"):
        memory._get_safe_path(name)


@pytest.mark.parametrize("alias", ["SOUL.md", "./SOUL.md", "daily/../SOUL.md", ".//SOUL.md"])
def test_aliases_canonicalize(mem, alias):
    assert memory._canonical_name(alias) == "SOUL.md"


# --- write_memory ---------------------------------------------------------------

def write(name, content):
    return memory.write_memory.invoke({"filename": name, "content": content})


def test_write_creates_subdirs_and_leaves_no_temp_files(mem):
    assert write("projects/new.md", "hello").startswith("Successfully saved")
    assert (mem / "projects" / "new.md").read_text(encoding="utf-8") == "hello"
    assert not [p for p in mem.rglob("*.tmp")]


def test_write_outside_refused(mem, tmp_path):
    assert write("../outside/secret.txt", "pwned").startswith("Error:")
    assert (tmp_path / "outside" / "secret.txt").read_text(encoding="utf-8") == "outside the sandbox"


@pytest.mark.parametrize("alias", ["HEARTBEAT.md", "./HEARTBEAT.md", "daily/../HEARTBEAT.md"])
def test_heartbeat_md_never_written_directly(mem, alias):
    out = write(alias, "- **evil** | every 1h")
    assert "manage_heartbeat_task" in out
    assert (mem / "HEARTBEAT.md").read_text(encoding="utf-8") == "original HEARTBEAT.md"


@pytest.mark.parametrize("alias", ["SOUL.md", "./SOUL.md", "daily/../SOUL.md"])
def test_soul_md_waits_for_confirmation(mem, confirmation, alias):
    assert write(alias, "new identity") == "Confirmation requested."
    assert (mem / "SOUL.md").read_text(encoding="utf-8") == "original SOUL.md", "unchanged until confirmed"
    assert "new identity" in confirmation.requests[0]["description"], "the request shows the diff"
    confirmation.confirm_last()
    assert (mem / "SOUL.md").read_text(encoding="utf-8") == "new identity"


def test_user_md_writes_without_confirmation(mem, confirmation):
    assert write("USER.md", "updated").startswith("Successfully saved")
    assert confirmation.requests == []


# --- delete_memory --------------------------------------------------------------

def delete(name):
    return memory.delete_memory.invoke({"filename": name})


@pytest.mark.parametrize("alias", ["SOUL.md", "HEARTBEAT.md", "MEMORY.md", "USER.md",
                                   "./USER.md", "daily/../MEMORY.md"])
def test_protected_files_never_deleted(mem, confirmation, alias):
    assert "is protected" in delete(alias)
    assert confirmation.requests == []
    assert (mem / memory._canonical_name(alias)).exists()


def test_delete_waits_for_confirmation(mem, confirmation):
    (mem / "old.md").write_text("stale", encoding="utf-8")
    assert delete("old.md") == "Confirmation requested."
    assert (mem / "old.md").exists(), "still there until confirmed"
    confirmation.confirm_last()
    assert not (mem / "old.md").exists()


def test_delete_missing_and_outside(mem, confirmation, tmp_path):
    assert "does not exist" in delete("ghost.md")
    assert delete("../outside/secret.txt").startswith("Error:")
    assert (tmp_path / "outside" / "secret.txt").exists()
    assert confirmation.requests == []


# --- read_memory / list_memory --------------------------------------------------

def test_read(mem):
    assert memory.read_memory.invoke({"filename": "USER.md"}) == "original USER.md"
    assert "does not exist" in memory.read_memory.invoke({"filename": "ghost.md"})
    assert memory.read_memory.invoke({"filename": "../outside/secret.txt"}).startswith("Error reading memory")
    assert "conversation-state database" in memory.read_memory.invoke({"filename": "threads.sqlite"})


def test_list_shows_only_memory_files(mem):
    (mem / ".git").mkdir()
    (mem / ".git" / "HEAD.md").write_text("x", encoding="utf-8")
    (mem / "threads.sqlite").write_text("db", encoding="utf-8")
    (mem / "daily" / "daily_2026-03-10.md").write_text("x", encoding="utf-8")
    (mem / "image.png").write_bytes(b"x")
    listed = memory.list_memory.invoke({})
    assert "- daily/daily_2026-03-10.md" in listed and "- SOUL.md" in listed
    assert ".git" not in listed and "threads.sqlite" not in listed and "image.png" not in listed
