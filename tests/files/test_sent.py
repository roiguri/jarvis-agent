"""Files Jarvis sends: kept in the store once delivered, and their marker reaches
the notification log, the episode store and the pending-mirror drain."""
import asyncio
import json
import os
import shutil

import pytest

import config
import pending_mirrors
from episodes import record as episodes_record
from episodes import store as episodes_store
from files import attachments, index, store
from gateway.outbox import EVENT_MEDIA, Outbox
from tests.conftest import LOG_DIR
from tools.core.history import async_append_notification_log

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"0" * 64
NOTIF_LOG = os.path.join(LOG_DIR, "notifications.jsonl")


@pytest.fixture(autouse=True)
def clean():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    for p in (episodes_store.STORE_PATH, NOTIF_LOG, pending_mirrors.CURSOR_PATH):
        if os.path.exists(p):
            os.unlink(p)
    yield


class MediaChannel:
    name = "test"

    def __init__(self, fail=False, att_id=None):
        self.fail = fail
        self.att_id = att_id
        self.sent = []

    async def send_to_owner_media(self, kind, payload, caption=None, *, event=None,
                                  filename=None, mime_type=None):
        if self.fail:
            raise ConnectionError("offline")
        self.sent.append((kind, caption))
        return self.att_id


async def log_sink(event, text, metadata):
    await async_append_notification_log(event, text, metadata)
    await asyncio.to_thread(episodes_record.notification, event, text, metadata)


async def save_sent(attachment, caption):
    return await asyncio.to_thread(attachments.save_sent, attachment, caption)


async def link_origin(file_id, channel, origin_id):
    await asyncio.to_thread(store.link_origin, file_id, channel, origin_id)


def send(outbox, caption):
    return asyncio.run(outbox.notify_owner_media("image", JPEG, caption, event=EVENT_MEDIA,
                                                 metadata={"has_image": True}))


def logged():
    with open(NOTIF_LOG, encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_a_sent_file_is_kept_and_its_marker_reaches_history():
    assert send(Outbox(MediaChannel(), log_sink, save_sent), "New episode of Silo").ok
    (rec,) = [store.get(r["id"]) for r in index.rows("SELECT id FROM files")]
    assert (rec.source, rec.caption, rec.kind, rec.turn_id) == ("jarvis", "New episode of Silo",
                                                                "image", None)
    (row,) = logged()
    assert row["message"] == f"New episode of Silo\n{rec.marker()}"
    assert row["media"] == [rec.id] and row["has_image"] is True
    (episode,) = episodes_store.rows("SELECT content FROM episodes")
    assert episode["content"] == row["message"]
    block, _ = pending_mirrors.drain_pending()
    assert block.endswith(f"[Notification] New episode of Silo\n{rec.marker()}")


def test_a_failed_send_keeps_nothing():
    assert not send(Outbox(MediaChannel(fail=True), log_sink, save_sent), "poster").ok
    assert index.rows("SELECT id FROM files") == [] and not os.path.exists(NOTIF_LOG)


def test_a_failed_save_still_logs_the_delivered_send():
    async def broken(attachment, caption):
        raise OSError(28, "No space left on device")
    assert send(Outbox(MediaChannel(), log_sink, broken), "poster").ok
    (row,) = logged()
    assert row["message"] == "poster" and "media" not in row


def test_the_drain_keeps_the_marker_of_a_long_caption():
    assert send(Outbox(MediaChannel(), log_sink, save_sent), "x" * 5000).ok
    (rec,) = [store.get(r["id"]) for r in index.rows("SELECT id FROM files")]
    block, _ = pending_mirrors.drain_pending()
    entry = block.split("\n", 1)[1]
    assert entry.endswith(rec.marker())
    assert len(entry) < pending_mirrors.ENTRY_CAP + 100



def test_a_sent_file_keeps_its_name_and_type():
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    outbox = Outbox(MediaChannel(), log_sink, save_sent)
    assert asyncio.run(outbox.notify_owner_media("document", pdf, "lease", event=EVENT_MEDIA,
                                                 filename="lease.pdf",
                                                 mime_type="application/pdf")).ok
    (rec,) = [store.get(r["id"]) for r in index.rows("SELECT id FROM files")]
    assert (rec.filename, rec.kind) == ("lease.pdf", "document")


def test_fresh_send_records_origin_link_for_channel_att_id():
    att_id = "att_01H11111111111111111111111"
    outbox = Outbox(MediaChannel(att_id=att_id), log_sink, save_sent, link_origin)
    assert send(outbox, "Poster").ok
    (rec,) = [store.get(r["id"]) for r in index.rows("SELECT id FROM files")]
    assert rec.origin == {}
    assert store.get_by_origin("test", att_id) == rec
    assert store.get_by_origin("jarvis", att_id) is None
    assert index.rows("SELECT channel, origin_id, file_id FROM origin_links") == [
        {"channel": "test", "origin_id": att_id, "file_id": rec.id}
    ]


def test_kept_send_links_channel_att_id_without_overwriting_origin_url():
    existing = store.add(JPEG, source="web", origin={"url": "https://example.com/poster.jpg"})
    att_id = "att_01H22222222222222222222222"
    outbox = Outbox(MediaChannel(att_id=att_id), log_sink, save_sent, link_origin)
    outcome = asyncio.run(outbox.notify_owner_media(
        "image", JPEG, "Resent poster", event=EVENT_MEDIA,
        kept=(existing.id, existing.marker()),
    ))
    assert outcome.ok
    assert len(index.rows("SELECT id FROM files")) == 1
    assert store.get_by_origin("test", att_id) == existing
    assert store.get(existing.id).origin == {"url": "https://example.com/poster.jpg"}


def test_a_failed_link_never_fails_the_delivered_send():
    async def broken_link(file_id, channel, origin_id):
        raise RuntimeError("sqlite busy")

    att_id = "att_01H33333333333333333333333"
    outbox = Outbox(MediaChannel(att_id=att_id), log_sink, save_sent, broken_link)
    assert send(outbox, "Poster").ok
    (row,) = logged()
    assert "Poster" in row["message"] and row["media"]
