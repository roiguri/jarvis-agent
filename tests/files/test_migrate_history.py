"""scripts/files_migrate_history.py: old cache paths in owner messages become file
markers, with search following, and a second run changes nothing."""
import importlib.util
import os
import shutil

import pytest

import config
from episodes import store as episodes
from files import index, store

SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      "scripts", "files_migrate_history.py")
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64
OLD = "/app/jarvis_code/gateway/channels/jarvis_app/media_cache/image_att_01.jpg"


@pytest.fixture
def migrate():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    if os.path.exists(episodes.STORE_PATH):
        os.unlink(episodes.STORE_PATH)
    spec = importlib.util.spec_from_file_location("files_migrate_history", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _owner(content, turn_id):
    with episodes.write() as con:
        episodes.insert(con, turn_id=turn_id, thread_id="owner", scope="user", role="owner",
                        source="chat", meta=None, content=content)


def _arrival(turn_id):
    rec = store.add(JPEG + turn_id.encode(), source="jarvis-app", turn_id=turn_id)
    with index.write() as con:
        con.execute("INSERT INTO legacy_paths (path, file_id) VALUES (?, ?)", (OLD, rec.id))
    return rec


def _search(word):
    return episodes.rows("SELECT e.id FROM episodes_fts f JOIN episodes e ON e.id = f.rowid"
                         " WHERE episodes_fts MATCH ?", (word,))


def test_each_message_gets_the_file_from_its_own_turn(migrate):
    first, second = _arrival("turn-a"), _arrival("turn-b")
    _owner(f"look at this\n[attached: {OLD}]", "turn-a")
    _owner(f"again\n[attached: {OLD}]", "turn-b")
    _owner("[attached: /old/media_cache/gone.jpg]", "turn-c")
    changes, unmatched = migrate.plan()
    assert unmatched == ["/old/media_cache/gone.jpg"] and len(changes) == 2
    migrate.apply(changes)
    contents = [r["content"] for r in episodes.rows("SELECT content FROM episodes ORDER BY id")]
    assert contents == [f"look at this\n{first.marker()}", f"again\n{second.marker()}",
                        "[attached: /old/media_cache/gone.jpg]"]
    assert _search("look") and not _search("image_att_01"), "search follows the new text"
    assert migrate.plan()[0] == [], "a second run changes nothing"


def test_messages_from_others_stay(migrate):
    _arrival("turn-a")
    with episodes.write() as con:
        episodes.insert(con, turn_id="turn-a", role="tool_result", source="chat",
                        content=f"You: [attached: {OLD}]")
    assert migrate.plan() == ([], [])


def test_it_checks_this_instance_s_service(migrate, monkeypatch):
    asked = []
    monkeypatch.setattr(migrate.subprocess, "run",
                        lambda cmd: asked.append(cmd[-1]) or type("R", (), {"returncode": 3})())
    assert migrate.service_running() is False
    monkeypatch.setattr(migrate.config, "ROOT", "/app")
    migrate.service_running()
    assert asked == ["jarvis-staging", "jarvis"]
