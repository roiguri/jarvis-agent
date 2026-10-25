"""Saving a message's files, showing them to the model, and the startup upkeep."""
import os
import shutil

import pytest

import config
from episodes import record as episodes_record
from episodes import store as episodes_store
from files import attachments, index, model_input, startup, store

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64
OGG = b"OggS" + b"\x00" * 64


@pytest.fixture(autouse=True)
def clean():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    if os.path.exists(episodes_store.STORE_PATH):
        os.unlink(episodes_store.STORE_PATH)
    yield


def att(data, kind="image", **kw):
    return {"kind": kind, "mime_type": kw.pop("mime_type", ""), "data": data,
            "filename": kw.pop("filename", None), "source": "test", "origin_id": "o1", **kw}


# --- saving ------------------------------------------------------------------

def test_save_attachments():
    recs, notes = attachments.save_attachments([att(JPEG, filename="a.jpg"), att(OGG, "audio")],
                                           caption="look at these", turn_id="t1")
    assert notes == []
    assert [r.kind for r in recs] == ["image", "audio"]
    assert all(r.turn_id == "t1" and r.caption == "look at these" for r in recs)
    assert recs[0].origin == {"id": "o1"} and recs[0].filename == "a.jpg"


def test_unsaveable_files_become_notes(monkeypatch):
    monkeypatch.setattr(store, "MAX_FILE_BYTES", 10)
    recs, notes = attachments.save_attachments([att(JPEG), att(b"", "video")])
    assert recs == []
    assert notes == ["[A photo was sent but is too large to keep (0 MB).]",
                     "[A video was sent but arrived empty.]"]


def test_disk_failure_becomes_a_note(monkeypatch):
    def full(*a, **kw):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store, "add", full)
    recs, notes = attachments.save_attachments([att(OGG, "audio")])
    assert recs == [] and notes == ["[An audio was sent but couldn't be saved (OSError).]"]


# --- what the model sees -------------------------------------------------------

def test_content_blocks_for_readable_files():
    img = store.add(JPEG, source="t")
    voice = store.add(OGG, source="t", mime_hint="audio/ogg")
    blocks = model_input.content_blocks([img, voice])
    assert (blocks[0]["type"], blocks[0]["mime_type"]) == ("image", "image/jpeg") and blocks[0]["base64"]
    assert blocks[1] == {"type": "text", "text": f"\n{img.marker()}"}
    assert (blocks[2]["type"], blocks[2]["mime_type"]) == ("audio", "audio/ogg")
    assert blocks[3] == {"type": "text", "text": f"\n{voice.marker()}"}


@pytest.mark.parametrize("make, note", [
    (lambda: store.add(b"\x00\x01bin", source="t"), "I can't read this file."),
    (lambda: store.add(b"GIF89a" + b"\x00" * 40, source="t"), "I can't read that format (image/gif)"),
])
def test_content_blocks_note_what_cant_be_shown(make, note):
    rec = make()
    (block,) = model_input.content_blocks([rec])
    assert block["type"] == "text" and rec.marker() in block["text"] and note in block["text"]


def test_content_blocks_for_a_removed_file():
    rec = store.delete(store.add(JPEG, source="t").id, "owner")
    (block,) = model_input.content_blocks([rec])
    assert "no longer stored" in block["text"]


def test_content_blocks_for_a_file_too_large(monkeypatch):
    rec = store.add(JPEG, source="t")
    monkeypatch.setattr(model_input, "INLINE_MEDIA_MAX_BYTES", 10)
    (block,) = model_input.content_blocks([rec])
    assert "too large" in block["text"]


# --- startup ---------------------------------------------------------------------

def test_startup_summary():
    assert startup.prepare().startswith("collected 0 unused,")


def test_startup_survives_a_failing_step(monkeypatch):
    def boom():
        raise RuntimeError("bad")
    monkeypatch.setattr(store, "collect_unused", boom)
    assert "MB stored" in startup.prepare()


def test_turn_input_with_only_files(tmp_path):
    rec = store.add(JPEG, source="t")
    episodes_record.turn_input(turn_id="t", thread_id="owner", scope="user", channel=None,
                               role="owner", source="chat", content="", attachments=[rec.marker()])
    (row,) = episodes_store.rows("SELECT content FROM episodes")
    assert row["content"] == rec.marker()
