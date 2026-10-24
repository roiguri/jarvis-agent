"""The files skill and what it rests on: search (with Hebrew inside longer words),
sending a stored file back, deleting with its copies, the collections file field,
and the places that count as using a file."""
import asyncio
import concurrent.futures
import os
import shutil
import sqlite3

import pytest

import config
from files import index, references, search, store
from gateway import outbox as outbox_mod
from gateway.outbox import Outbox
from tools.files import delete_file, find_files, send_file

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


@pytest.fixture(autouse=True)
def clean():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    index._initialized.clear()
    yield


def described(rec, kind, text):
    store.put_derived(rec.id, kind, text, producer="m", version="1")
    return rec


# --- search ------------------------------------------------------------------------

def test_a_hebrew_word_is_found_inside_a_longer_one():
    lease = described(store.add(PDF, source="telegram", filename="lease.pdf"), "text",
                      "Summary: a rental contract.\nText: והחוזה נחתם ביום ראשון")
    described(store.add(JPEG, source="t"), "description", "A monstera in a pot.")
    found = find_files.invoke({"query": "חוזה"})
    assert found.startswith(lease.marker()) and "monstera" not in found
    assert "Match in its text: …" in found or "Match in its text:" in found


def test_all_words_first_then_any():
    both = described(store.add(JPEG + b"1", source="t"), "description", "red bicycle by the sea")
    one = described(store.add(JPEG + b"2", source="t"), "description", "red car")
    assert search.find("red bicycle").count("[file:") == 1 and both.id in search.find("red bicycle")
    assert one.id in search.find("car bicycle") and both.id in search.find("car bicycle")


def test_short_words_fall_back_to_a_substring_match():
    rec = described(store.add(JPEG, source="t"), "description", "Text: דב בגן")
    assert rec.id in search.find("דב")


def test_filters_and_the_listing_without_words():
    old = described(store.add(JPEG + b"1", source="telegram", created="2026-01-05T10:00:00+00:00"),
                    "description", "an old photo")
    new = store.add(b"OggS" + b"\x00" * 60, source="jarvis-app", filename="voice.ogg",
                    mime_hint="audio/ogg")
    described(new, "transcript", "Summary: a greeting.\nTranscript: שלום")
    assert search.find("", kind="audio").startswith(new.marker())
    assert "Its transcript: Summary: a greeting." in search.find("", kind="audio")
    assert old.id not in search.find("", source="jarvis-app")
    assert search.find("", until="2026-01-31").startswith(old.marker())
    assert "Invalid date" in search.find("", since="last week")


def test_removed_files():
    gone = described(store.add(JPEG + b"1", source="t"), "description", "a passport photo")
    expired = described(store.add(JPEG + b"2", source="t"), "description", "a passport scan")
    store.delete(gone.id, "owner")
    store.expire(expired.id, "retention")
    found = search.find("passport")
    assert gone.id not in found and "expired]" in found


def test_a_text_file_match_says_where_to_read():
    body = ("filler line\n" * 400) + "the secret ingredient is cardamom\n"
    rec = store.add(body.encode(), source="t", filename="recipe.txt")
    store.put_derived(rec.id, "text", body, producer="file", version="text-1")
    found = search.find("cardamom")
    start = int(found.split("start=")[1].rstrip(")"))
    assert 0 < start <= body.index("cardamom") < start + 8000


def test_an_index_built_before_trigram_is_rebuilt(tmp_path, monkeypatch):
    path = str(tmp_path / "files.sqlite")
    con = sqlite3.connect(path)
    con.executescript(index._SCHEMA.replace(", tokenize='trigram'", ""))
    con.execute("INSERT INTO files (id, hash, kind, mime_type, size, source, created)"
                " VALUES ('file:aaaaaaaa', 'h', 'image', 'image/jpeg', 1, 't', '2026-01-01')")
    con.execute("INSERT INTO chunks (file_id, origin, position, text)"
                " VALUES ('file:aaaaaaaa', 'description', 0, 'והחוזה')")
    con.commit()
    con.close()
    monkeypatch.setattr(index, "INDEX_PATH", path)
    rows = index.rows("SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH 'חוזה'")
    assert len(rows) == 1
    assert "trigram" in index.rows("SELECT sql FROM sqlite_master WHERE name = 'chunks_fts'")[0]["sql"]


# --- send_file ---------------------------------------------------------------------

class MediaChannel:
    name = "test"

    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    async def send_to_owner_media(self, kind, payload, caption=None, *, event=None,
                                  filename=None, mime_type=None):
        if self.fail:
            raise self.fail
        self.sent.append((kind, caption, filename, mime_type, event))


@pytest.fixture
def channel(monkeypatch):
    from gateway import factory

    chan = MediaChannel()

    async def never(attachment, caption):
        raise AssertionError("a stored file must not be saved again")
    monkeypatch.setattr(factory, "origin_outbox", lambda: Outbox(chan, None, never))

    def run_now(coro):
        future = concurrent.futures.Future()
        future.set_result(asyncio.run(coro))
        return future
    monkeypatch.setattr(outbox_mod, "submit", run_now)
    return chan


def test_send_file_sends_the_stored_file_under_its_own_id(channel):
    rec = store.add(PDF, source="telegram", filename="lease.pdf")
    assert send_file.invoke({"file_id": rec.id, "caption": "here"}) == f"Sent {rec.marker()} to the owner."
    assert channel.sent == [("document", "here", "lease.pdf", "application/pdf", None)]
    assert index.rows("SELECT COUNT(*) AS n FROM files")[0]["n"] == 1


def test_a_text_file_goes_out_as_a_document(channel):
    rec = store.add(b"print('hi')\n", source="t", filename="hi.py")
    send_file.invoke({"file_id": rec.id})
    assert channel.sent[0][0] == "document"


def test_send_file_refuses_what_it_cannot_send(channel):
    rec = store.add(JPEG, source="t")
    store.expire(rec.id, "retention")
    assert "no longer stored" in send_file.invoke({"file_id": rec.id})
    assert send_file.invoke({"file_id": "file:zzzzzzzz"}) == "No file with id 'file:zzzzzzzz'."
    channel.fail = NotImplementedError("cannot send media kind='video'")
    live = store.add(JPEG + b"1", source="t")
    assert "could not be sent — cannot send media kind='video'" in send_file.invoke({"file_id": live.id})
    assert channel.sent == []


# --- delete_file -------------------------------------------------------------------

@pytest.fixture
def confirm(monkeypatch):
    from gateway import factory
    from tests.tools.test_collections import FakeConfirmation

    fake = FakeConfirmation()
    monkeypatch.setattr(factory, "get_confirmation", lambda: fake)
    return fake


@pytest.fixture
def no_holders(monkeypatch):
    monkeypatch.setattr(references, "_HOLDERS", {})


def test_delete_asks_first_and_takes_every_copy(confirm, no_holders):
    first, copy = store.add(JPEG, source="t"), store.add(JPEG, source="t")
    other = store.add(JPEG + b"x", source="t")
    asked = delete_file.invoke({"file_ids": [first.id]})
    assert asked.startswith("Confirmation request sent.")
    description, _ = confirm.pending[0]
    assert first.id in description and f"{copy.id}" in description and "(a copy of the same file)" in description
    assert other.id not in description
    assert store.get(first.id).state == "available", "nothing happens before the owner confirms"
    assert confirm.tap() == "Deleted 2 file(s)."
    assert (store.get(first.id).state, store.get(copy.id).state, store.get(other.id).state) == \
        ("deleted", "deleted", "available")


def test_delete_shows_where_a_file_is_used(confirm, no_holders):
    rec = store.add(JPEG, source="t")
    references.holder("collections", strength="strong")(lambda: [rec.id])
    delete_file.invoke({"file_ids": [rec.id]})
    assert "used in: collections" in confirm.pending[0][0]


def test_delete_refuses_when_where_used_cannot_be_checked(confirm, no_holders):
    rec = store.add(JPEG, source="t")

    def broken():
        raise OSError("disk")
    references.holder("collections", strength="strong")(broken)
    assert delete_file.invoke({"file_ids": [rec.id]}).startswith("Nothing deleted: couldn't check")
    assert confirm.pending == [] and store.get(rec.id).state == "available"


def test_delete_with_an_unknown_id_changes_nothing(confirm, no_holders):
    rec = store.add(JPEG, source="t")
    assert "no file with id" in delete_file.invoke({"file_ids": [rec.id, "file:zzzzzzzz"]})
    assert confirm.pending == []


# --- collections and memory as holders ---------------------------------------------

def test_a_collection_file_field(tmp_path, monkeypatch):
    from tools.collections import _db, _fields
    from tools.collections import references as coll_refs

    monkeypatch.setattr(_db, "DB_PATH", str(tmp_path / "collections.sqlite"))
    field = {"name": "photo", "type": "file"}
    rec = store.add(JPEG, source="t")
    assert _fields.normalize(field, rec.id.upper()) == rec.id
    assert _fields.render(field, rec.id) == rec.marker()
    with pytest.raises(_fields.FieldError, match="no stored file"):
        _fields.normalize(field, "file:zzzzzzzz")
    gone = store.add(JPEG + b"1", source="t")
    store.delete(gone.id, "owner")
    with pytest.raises(_fields.FieldError, match="was deleted"):
        _fields.normalize(field, gone.id)
    store.delete(rec.id, "owner")
    assert _fields.render(field, rec.id).endswith("deleted]"), "a removed file reads as removed"

    assert coll_refs.collection_files() == []
    _db._init_db()
    conn = _db._get_db()
    conn.execute("INSERT INTO collections (name, schema, created_at) VALUES (?, ?, ?)",
                 ("Plants", '{"fields": [{"name": "photo", "type": "file"}], "status": null,'
                  ' "sections": false}', "2026-01-01"))
    cid = conn.execute("SELECT collection_id FROM collections").fetchone()[0]
    conn.execute("INSERT INTO items (collection_id, title, extra, position, created_at, updated_at)"
                 " VALUES (?, 'monstera', ?, 0, '2026-01-01', '2026-01-01')",
                 (cid, f'{{"photo": "{rec.id}"}}'))
    conn.commit()
    conn.close()
    assert coll_refs.collection_files() == [rec.id]


def test_memory_files_hold_the_ids_written_into_them():
    from tools.core.memory import memory_files

    rec = store.add(JPEG, source="t")
    path = os.path.join(config.MEMORY_DIR, "documents.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"Passport scan: {rec.id}\n")
    try:
        assert rec.id in memory_files()
    finally:
        os.unlink(path)
