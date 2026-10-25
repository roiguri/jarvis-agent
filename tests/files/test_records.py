"""Records: one function per file kind behind records.make(), and the worker that
queues, makes, copies, retries and drops them."""
import asyncio
import shutil
from datetime import datetime, timedelta, timezone

import pytest

import config
from files import index, records, store, worker
from observability import telemetry

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64
PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
ZIP = b"PK\x03\x04" + b"\x00" * 60


@pytest.fixture(autouse=True)
def clean():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    yield


@pytest.fixture
def model(monkeypatch):
    """Stands in for the side call; `calls` holds (block type, prompt, kwargs)."""
    class Fake:
        calls = []
        reply = ("Shown: a monstera.\nText: none", "STOP")

        def __call__(self, parts, prompt, **kw):
            self.calls.append((parts[0]["type"], prompt, kw))
            text, finish = self.reply
            return text, finish, (object() if text else None)
    fake = Fake()
    monkeypatch.setattr(records, "side_call", fake)
    return fake


def jobs():
    return index.rows("SELECT * FROM jobs")


# --- one function per kind --------------------------------------------------------

def test_every_recorded_kind_has_one_recorder_and_other_files_have_none():
    assert set(records.RECORDERS) == {"image", "video", "audio", "document", "text"}
    rec = store.add(ZIP, source="t", filename="a.zip")
    with pytest.raises(records.NoRecord):
        records.make(rec)


def test_each_kind_gets_its_own_prompt_and_record_kind(model):
    image = records.make(store.add(JPEG, source="t"))
    document = records.make(store.add(PDF, source="t", filename="lease.pdf"))
    assert (image.kind, image.version, image.producer) == ("description", "image-1", config.MODEL)
    assert (document.kind, document.version) == ("text", "document-1")
    (_, image_prompt, _), (block, doc_prompt, kw) = model.calls
    assert image_prompt == records.IMAGE_PROMPT and block == "file"
    assert doc_prompt == records.DOCUMENT_PROMPT and kw["job"] == "file_record"


def test_a_text_file_is_its_own_record_without_a_model(model):
    record = records.make(store.add(b"print('hi')\n", source="t", filename="hi.py"))
    assert (record.kind, record.content, record.producer) == ("text", "print('hi')\n", "file")
    assert model.calls == []


def test_a_cut_off_record_says_so(model):
    model.reply = ("Shown: half a monstera", "MAX_TOKENS")
    record = records.make(store.add(JPEG, source="t"))
    assert record.content.endswith("[The record stops early (MAX_TOKENS); the rest of the file "
                                   "is missing.]")


# --- the worker -------------------------------------------------------------------

def test_a_stored_file_is_queued_and_its_record_made_and_searchable(model):
    rec = store.add(JPEG, source="t")
    assert [j["file_id"] for j in jobs()] == [rec.id]
    assert worker.run_pending() == ["made"]
    (derived,) = store.get_derived(rec.id)
    assert (derived["kind"], derived["version"]) == ("description", "image-1")
    assert jobs() == []
    hits = index.rows("SELECT c.file_id FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid"
                      " WHERE chunks_fts MATCH 'monstera'")
    assert [h["file_id"] for h in hits] == [rec.id]


def test_the_newest_file_goes_first(model):
    old = store.add(JPEG + b"1", source="t", created="2020-01-01T00:00:00+00:00")
    new = store.add(JPEG + b"2", source="t")
    assert worker.next_job()["file_id"] == new.id
    worker.run_pending()
    assert {old.id, new.id} == {d["file_id"] for d in index.rows("SELECT file_id FROM derived")}


def test_the_same_bytes_are_recorded_once(model):
    first, second = store.add(JPEG, source="t"), store.add(JPEG, source="t")
    assert sorted(worker.run_pending()) == ["copied", "made"]
    assert len(model.calls) == 1
    assert store.get_derived(first.id)[0]["content"] == store.get_derived(second.id)[0]["content"]


def test_a_failure_backs_off_and_stops_after_the_last_attempt(model):
    model.reply = (None, "TimeoutError: slow")
    rec = store.add(JPEG, source="t")
    assert worker.run_pending() == ["failed"]
    (job,) = jobs()
    assert job["attempts"] == 1 and job["last_error"] == "TimeoutError: slow"
    retry_at = datetime.fromisoformat(job["next_at"])
    assert timedelta(seconds=50) < retry_at - datetime.now(timezone.utc) < timedelta(seconds=70)
    assert worker.run_pending() == [], "not due yet"
    for _ in range(worker.MAX_ATTEMPTS - 1):
        with index.write() as con:
            con.execute("UPDATE jobs SET next_at = ?", ("2000-01-01T00:00:00+00:00",))
        assert worker.run_pending() == ["failed"]
    with index.write() as con:
        con.execute("UPDATE jobs SET next_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert worker.run_pending() == [], "given up; the job stays with its error"
    assert jobs()[0]["attempts"] == worker.MAX_ATTEMPTS and store.get_derived(rec.id) == []
    status = worker.queue_status()
    assert (status["queued"], status["given_up"]) == (0, 1)
    assert status["given_up_jobs"] == [{"file_id": rec.id, "last_error": "TimeoutError: slow"}]


def test_files_that_cannot_have_a_record_are_dropped(model):
    store.add(ZIP, source="t", filename="a.zip")
    gone = store.add(JPEG, source="t")
    store.expire(gone.id, "retention")
    assert worker.run_pending() == ["dropped", "dropped"]
    assert jobs() == [] and model.calls == []


def test_a_deleted_file_takes_its_job_with_it(model):
    rec = store.add(JPEG, source="t")
    store.delete(rec.id, "owner")
    assert jobs() == [] and worker.run_pending() == []


def test_requeue_picks_up_records_older_than_the_current_version(model):
    current, outdated = store.add(JPEG + b"1", source="t"), store.add(JPEG + b"2", source="t")
    worker.run_pending()
    store.put_derived(outdated.id, "description", "old", producer="m", version="image-0")
    assert worker.requeue_outdated("image") == 1
    assert [j["file_id"] for j in jobs()] == [outdated.id]
    with pytest.raises(ValueError):
        worker.requeue_outdated("file")


def test_a_job_runs_outside_the_turn_that_started_it():
    seen = []

    async def go():
        token = telemetry.TURN_ID.set("a-turn")
        try:
            await worker._in_own_thread(lambda: seen.append(telemetry.TURN_ID.get()))
        finally:
            telemetry.TURN_ID.reset(token)
    asyncio.run(go())
    assert seen == [None]
