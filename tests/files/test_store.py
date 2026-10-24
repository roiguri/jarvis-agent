"""The file store: ids, adding, states, derived content, collection, references."""
import os
import shutil
import threading
from datetime import timedelta

import pytest
from contextlib import closing

import config
from files import blobs, ids, index, references, store

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
       b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
       b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    shutil.rmtree(config.FILES_DIR, ignore_errors=True)
    shutil.rmtree(index.INDEX_DIR, ignore_errors=True)
    monkeypatch.setattr(references, "_HOLDERS", {})
    yield
    shutil.rmtree(config.FILES_DIR, ignore_errors=True)
    shutil.rmtree(index.INDEX_DIR, ignore_errors=True)


def _age_bytes(sha, seconds=3600):
    t = os.path.getmtime(blobs.path_of(sha)) - seconds
    os.utime(blobs.path_of(sha), (t, t))


# --- ids -------------------------------------------------------------------

def test_new_ids_are_well_formed_and_distinct():
    made = {ids.new_id() for _ in range(2000)}
    assert len(made) == 2000
    assert all(ids.normalize(i) == i for i in made)


@pytest.mark.parametrize("raw,want", [
    ("file:k7m2q9xd", "file:k7m2q9xd"),
    ("K7M2Q9XD", "file:k7m2q9xd"),
    ("file:k7m2q9xI", "file:k7m2q9x1"),   # I reads as 1
    ("file:O7m2q9xd", "file:07m2q9xd"),   # O reads as 0
    ("file:k7m2q9x", None),
    ("file:k7m2q9xdz", None),
    ("file:k7m2q9xu", None),              # u is not Crockford
])
def test_normalize(raw, want):
    assert ids.normalize(raw) == want


def test_find_ids_ignores_ordinary_text():
    text = "see [file:k7m2q9xd · image] and file:///etc/passwd, file:readme.txt, xfile:abcdefgh"
    assert ids.find_ids(text) == {"file:k7m2q9xd"}


# --- adding and reading ----------------------------------------------------

def test_add_and_read_back():
    rec = store.add(PNG, source="test", filename="dot.png", caption="a dot")
    assert rec.kind == "image" and rec.mime_type == "image/png" and rec.state == "available"
    assert store.read_bytes(rec.id) == PNG
    assert store.get(rec.id.upper()) == rec
    assert rec.marker() == f"[{rec.id} · image · dot.png · {len(PNG)} B]"


def test_same_bytes_twice_is_two_records_one_copy():
    a = store.add(PNG, source="test")
    b = store.add(PNG, source="test")
    assert a.id != b.id and a.hash == b.hash
    assert list(blobs.hashes()) == [a.hash]
    assert [r.id for r in store.sharing_bytes(a.id)] == [b.id]


def test_kind_comes_from_content_not_the_sender():
    assert store.add(PNG, source="t", mime_hint="application/pdf").kind == "image"
    assert store.add(b"%PDF-1.4\n...", source="t").kind == "document"
    text = store.add("שלום, a note\n".encode(), source="t", filename="note.md")
    assert (text.kind, text.mime_type) == ("text", "text/markdown")
    assert store.add(b"\x00\x01\x02binary", source="t").kind == "file"


def test_audio_only_mp4_is_audio():
    m4a = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00M4A mp42isom" + b"\x00" * 64
    rec = store.add(m4a, source="t", filename="voice.m4a")
    assert rec.kind == "audio"


def test_filenames_are_cleaned():
    assert store.clean_filename("../../etc/x]\n[y].txt") == "x) (y).txt"
    long = "a" * 300 + ".pdf"
    assert store.clean_filename(long).endswith(".pdf") and len(store.clean_filename(long)) == 120


def test_limits():
    with pytest.raises(ValueError):
        store.add(b"", source="t")
    with pytest.raises(store.FileTooLarge):
        store.add(b"x" * (store.MAX_FILE_BYTES + 1), source="t")


def test_unknown_or_malformed_id():
    assert store.get("file:zzzzzzzz") is None
    assert store.get("not an id") is None
    with pytest.raises(LookupError):
        store.read_bytes("file:zzzzzzzz")


# --- states ----------------------------------------------------------------

def test_trash_and_restore_keep_bytes():
    rec = store.add(PNG, source="t")
    assert store.trash(rec.id).state == "trash"
    assert store.read_bytes(rec.id) == PNG
    assert store.restore(rec.id).state == "available"


def test_expire_keeps_records_releases_bytes():
    rec = store.add(PNG, source="t")
    store.put_derived(rec.id, "description", "a red dot", producer="test", version="1")
    gone = store.expire(rec.id, "retention")
    assert gone.state == "expired" and gone.removed_reason == "retention"
    assert store.get_derived(rec.id, "description")[0]["content"] == "a red dot"
    with pytest.raises(store.FileUnavailable) as e:
        store.read_bytes(rec.id)
    assert e.value.state == "expired"


def test_owner_delete_keeps_a_minimal_record():
    rec = store.add(PNG, source="t", filename="secret.png", caption="private", origin={"url": "u"})
    store.put_derived(rec.id, "description", "private things", producer="test", version="1")
    gone = store.delete(rec.id, "owner")
    assert gone.state == "deleted" and gone.filename is None and gone.caption is None
    assert gone.origin == {} and gone.kind == "image" and gone.source == "t"
    assert store.get_derived(rec.id) == []
    assert index.rows("SELECT * FROM chunks WHERE file_id = ?", (rec.id,)) == []
    assert "deleted" in gone.marker()


def test_illegal_transitions_are_refused():
    rec = store.add(PNG, source="t")
    store.delete(rec.id, "owner")
    for change in (store.restore, store.trash):
        with pytest.raises(ValueError):
            change(rec.id)
    with pytest.raises(ValueError):
        store.expire(rec.id, "r")


def test_missing_bytes_read_as_missing():
    rec = store.add(PNG, source="t")
    os.unlink(blobs.path_of(rec.hash))  # e.g. an older index restored after a collection
    assert store.get(rec.id).state == "missing"
    with pytest.raises(store.FileUnavailable) as e:
        store.read_bytes(rec.id)
    assert e.value.state == "missing"
    assert store.verify()["missing"] == [rec.id]


# --- derived content and chunks ----------------------------------------------

def test_put_derived_replaces_and_reindexes():
    rec = store.add(PNG, source="t")
    store.put_derived(rec.id, "text", "alpha " * 600, producer="p", version="1")
    first = index.rows("SELECT position FROM chunks WHERE file_id = ? AND origin = 'text'", (rec.id,))
    assert len(first) >= 2 and first[0]["position"] == 0
    store.put_derived(rec.id, "text", "beta", producer="p", version="2")
    assert store.get_derived(rec.id, "text")[0]["version"] == "2"
    hits = index.rows("SELECT c.file_id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
                      " WHERE chunks_fts MATCH 'alpha'")
    assert hits == []


def test_meta_is_searchable_from_the_start():
    rec = store.add(PNG, source="t", filename="receipt.png", caption="dentist bill")
    hits = index.rows("SELECT c.file_id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid"
                      " WHERE chunks_fts MATCH 'dentist'")
    assert [h["file_id"] for h in hits] == [rec.id]


def test_chunk_positions_point_into_the_text():
    text = ("word " * 1000).strip()
    for position, piece in store.chunk(text, size=300):
        assert text[position:].lstrip().startswith(piece[:20])


def test_derived_refused_for_deleted_file():
    rec = store.add(PNG, source="t")
    store.delete(rec.id, "owner")
    with pytest.raises(store.FileUnavailable):
        store.put_derived(rec.id, "description", "x", producer="p", version="1")


# --- collecting unused bytes -------------------------------------------------

def test_collect_keeps_needed_and_young_bytes():
    kept = store.add(PNG, source="t")
    other = store.add(b"%PDF-1.4 other", source="t")
    store.delete(other.id, "owner")
    assert store.collect_unused() == []          # unused, but within the grace period
    _age_bytes(other.hash)
    _age_bytes(kept.hash)
    assert store.collect_unused() == [other.hash]
    assert blobs.exists(kept.hash) and not blobs.exists(other.hash)


def test_shared_bytes_survive_one_arrivals_delete():
    a = store.add(PNG, source="t")
    b = store.add(PNG, source="t")
    store.delete(a.id, "owner")
    _age_bytes(a.hash)
    assert store.collect_unused() == []
    assert store.read_bytes(b.id) == PNG


def test_readding_unused_bytes_saves_them_from_collection():
    a = store.add(PNG, source="t")
    store.delete(a.id, "owner")
    _age_bytes(a.hash)
    b = store.add(PNG, source="t")              # refreshes the bytes' age
    assert store.collect_unused() == []
    assert store.read_bytes(b.id) == PNG


def test_collection_waits_for_an_add_in_progress(monkeypatch):
    """A collection that starts between an add writing the bytes and inserting its
    record must not delete them: the bytes are unused at that instant."""
    seed = store.add(PNG, source="t")
    store.delete(seed.id, "owner")
    real_put = blobs.put
    collector = {}

    def put_then_collect(data):
        sha = real_put(data)
        t = threading.Thread(target=lambda: collector.setdefault("deleted", store.collect_unused(0)))
        t.start()
        t.join(timeout=0.5)        # blocked on the store lock, so this times out
        collector["thread"] = t
        return sha

    monkeypatch.setattr(blobs, "put", put_then_collect)
    rec = store.add(PNG, source="t")
    collector["thread"].join()
    assert collector["deleted"] == []
    assert store.read_bytes(rec.id) == PNG


def test_crash_leftover_temp_files_are_cleaned():
    rec = store.add(PNG, source="t")
    leftover = os.path.join(os.path.dirname(blobs.path_of(rec.hash)), ".tmp-crash")
    with open(leftover, "wb") as f:
        f.write(b"half")
    old = os.path.getmtime(leftover) - 7200
    os.utime(leftover, (old, old))
    assert list(blobs.hashes()) == [rec.hash]
    store.collect_unused()
    assert not os.path.exists(leftover)


# --- references --------------------------------------------------------------

def test_scan_and_where_used():
    rec = store.add(PNG, source="t")
    references.holder("collections", strength="strong")(lambda: [rec.id.upper()])
    references.holder("history", strength="weak")(lambda: [rec.id, "file:zzzzzzzz"])
    used = references.scan()
    assert {h.name for h in used[rec.id]} == {"collections", "history"}
    assert [h.name for h in references.where_used(rec.id)] == ["collections", "history"]


def test_scan_fails_closed():
    def broken():
        raise OSError("disk")
    references.holder("memory", strength="strong")(broken)
    with pytest.raises(references.ReferenceScanFailed):
        references.scan()
    with pytest.raises(references.ReferenceScanFailed):
        references.sweep_candidates(lambda r: True, grace=timedelta(0))


def test_sweep_report_spares_strong_references_and_young_files():
    old = store.add(PNG, source="t")
    held = store.add(b"%PDF-1.4 held", source="t")
    mentioned = store.add(b"%PDF-1.4 mentioned", source="t")
    references.holder("collections", strength="strong")(lambda: [held.id])
    references.holder("history", strength="weak")(lambda: [mentioned.id])
    report = references.sweep_candidates(lambda r: True, grace=timedelta(0))
    assert {r.id for r in report} == {old.id, mentioned.id}
    assert references.sweep_candidates(lambda r: True) == []   # all younger than the grace
    assert store.get(old.id).state == "available"              # a report changes nothing


def test_holder_names_are_unique():
    references.holder("x", strength="weak")(lambda: [])
    with pytest.raises(ValueError):
        references.holder("x", strength="weak")(lambda: [])
    with pytest.raises(ValueError):
        references.holder("y", strength="medium")


# --- legacy paths and health -------------------------------------------------

def test_legacy_paths_map_many_to_one():
    a = store.add(PNG, source="t")
    b = store.add(PNG, source="t")
    store.map_legacy_path("/old/cache/x.jpg", a.id)
    store.map_legacy_path("/old/cache/x.jpg", b.id)
    assert store.by_legacy_path("/old/cache/x.jpg").id in {a.id, b.id}
    assert store.by_legacy_path("/old/cache/none.jpg") is None


def test_disk_status_and_verify():
    rec = store.add(PNG, source="t")
    status = store.disk_status()
    assert status["stored_bytes"] == len(PNG) and status["free_bytes"] > 0
    report = store.verify(check_hashes=True)
    assert report["records"] == 1 and report["missing"] == [] and report["corrupt"] == []
    with open(blobs.path_of(rec.hash), "wb") as f:
        f.write(b"tampered")
    assert store.verify(check_hashes=True)["corrupt"] == [rec.hash]



def test_a_restored_older_index_never_costs_the_newer_bytes(tmp_path, monkeypatch):
    """A restore puts back an older index; bytes it doesn't know stay, and verify says so."""
    kept = store.add(b"first file", source="t")
    snapshot = tmp_path / "files.sqlite"
    with closing(index.connect()) as con:
        con.execute("VACUUM INTO ?", (str(snapshot),))
    newer = store.add(b"arrived after the backup", source="t")
    gone = store.add(b"deleted before the backup", source="t")
    store.delete(gone.id, "owner")
    shutil.copy(snapshot, index.INDEX_PATH)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(index.INDEX_PATH + suffix):
            os.unlink(index.INDEX_PATH + suffix)
    assert store.collect_unused(grace_s=0) == []
    assert blobs.exists(newer.hash) and blobs.exists(kept.hash)
    report = store.verify()
    assert newer.hash in report["unknown"] and gone.hash in report["unknown"]


def test_released_bytes_are_still_collected():
    rec = store.add(b"to be deleted", source="t")
    store.delete(rec.id, "owner")
    assert store.collect_unused(grace_s=0) == [rec.hash]


def test_a_record_finished_after_a_delete_is_not_written_back():
    rec = store.add(b"\xff\xd8\xff\xe0 photo", source="t")
    store.delete(rec.id, "owner")
    with pytest.raises(store.FileUnavailable):
        with index.write() as con:
            store._put_derived(con, rec.id, "description", "a photo", "m", "1")
    assert store.get_derived(rec.id) == []
