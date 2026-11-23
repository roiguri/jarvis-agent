"""Quote-reply support for the jarvis-app channel: outbound att_id origin
linking and inbound reply_to_message resolution to File Store `[file:…]`
handles without inlining quoted media bytes."""

from __future__ import annotations

import asyncio
import shutil

import pytest

import config
from files import attachments as file_attachments
from files import index, store
from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.channels.jarvis_app.client import PINNED_CONTRACT_VERSION
from gateway.channels.jarvis_app.router import JarvisAppInboundRouter
from gateway.outbox import Outbox

PNG_1 = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05"
    b"\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)
JPEG_1 = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x01" * 64
JPEG_2 = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x02" * 64
JPEG_3 = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x03" * 64
PDF_1 = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF\n"

ATT_1 = "att_01HAAAAAAAAAAAAAAAAAAAAAAA"
ATT_2 = "att_01HBBBBBBBBBBBBBBBBBBBBBBB"
ATT_3 = "att_01HCCCCCCCCCCCCCCCCCCCCCCC"
ATT_NEW = "att_01HDDDDDDDDDDDDDDDDDDDDDDD"
ATT_OUT = "att_01HEEEEEEEEEEEEEEEEEEEEEEE"


@pytest.fixture(autouse=True)
def clean_store():
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)
    yield
    for d in (config.FILES_DIR, index.INDEX_DIR):
        shutil.rmtree(d, ignore_errors=True)


class FakeHubClient:
    def __init__(self, blobs_by_id: dict[str, bytes | Exception] | None = None,
                 upload_ids: list[str] | None = None):
        self.blobs = dict(blobs_by_id or {})
        self.upload_ids = list(upload_ids or [ATT_OUT])
        self.downloads: list[str] = []
        self.sent: list[dict] = []

    async def download_attachment(self, att_id: str) -> bytes:
        self.downloads.append(att_id)
        item = self.blobs.get(att_id)
        if item is None:
            raise RuntimeError(f"404 {att_id}")
        if isinstance(item, BaseException):
            raise item
        return item

    async def upload_attachment(self, payload: bytes, **kw) -> str:
        return self.upload_ids.pop(0)

    async def send_message(self, body: dict) -> dict:
        self.sent.append(body)
        return {"id": len(self.sent)}

    async def post_event(self, *a, **kw) -> None:
        return None


def _make_router(client: FakeHubClient, captured: list):
    async def on_message(inbound):
        captured.append(inbound)
        return None

    channel = JarvisAppChannel(client, owner_id="owner")
    return JarvisAppInboundRouter(
        channel,
        client,
        on_message,
        confirmation_ui=None,
        resolve_attachment=file_attachments.resolve_origin_attachment,
    )


def test_pinned_contract_version():
    assert PINNED_CONTRACT_VERSION == "b4798d761570821e"


@pytest.mark.asyncio
async def test_channel_and_outbox_record_origin_links_for_fresh_and_kept_sends():
    client = FakeHubClient(upload_ids=[ATT_1, ATT_2])
    channel = JarvisAppChannel(client, owner_id="owner")

    async def save_sent(att, caption):
        return await asyncio.to_thread(file_attachments.save_sent, att, caption)

    async def link_origin(fid, ch, oid):
        await asyncio.to_thread(store.link_origin, fid, ch, oid)

    outbox = Outbox(channel, save_file=save_sent, link_origin=link_origin)

    # Fresh send (e.g. notifier poster)
    assert (await outbox.notify_owner_media("image", JPEG_1, "Fresh poster")).ok
    fresh_rec = store.get_by_origin("jarvis-app", ATT_1)
    assert fresh_rec is not None
    assert fresh_rec.source == "jarvis" and fresh_rec.origin == {}
    assert store.get_by_origin("jarvis", ATT_1) is None

    # Kept send (e.g. send_file on an existing web-fetched image)
    fetched = store.add(PNG_1, source="web", filename="poster.png",
                        origin={"url": "https://example.com/poster.png"})
    assert (await outbox.notify_owner_media(
        "image", PNG_1, "From web", filename="poster.png", mime_type="image/png",
        kept=(fetched.id, fetched.marker()),
    )).ok
    assert store.get_by_origin("jarvis-app", ATT_2) == fetched
    assert store.get(fetched.id).origin == {"url": "https://example.com/poster.png"}
    assert len(index.rows("SELECT * FROM origin_links")) == 2

    # Direct channel send_media also returns att_id
    client.upload_ids.append(ATT_3)
    got_id = await channel.send_media("owner", "image", JPEG_2, "Direct")
    assert got_id == ATT_3


@pytest.mark.asyncio
async def test_non_reply_leaves_reply_to_none_and_ignores_meta_reply_to():
    client = FakeHubClient()
    captured = []
    router = _make_router(client, captured)

    # Plain non-reply with reply_to_message: None
    await router._handle({
        "update_id": 1,
        "type": "message",
        "message": {"id": 10, "role": "user", "text": "hello", "attachments": []},
        "reply_to_message": None,
    })
    assert captured[0].reply_to is None and captured[0].user_text == "hello"

    # Empty dict reply_to_message is also treated as a non-reply
    await router._handle({
        "update_id": 2,
        "type": "message",
        "message": {"id": 11, "role": "user", "text": "empty reply dict", "attachments": []},
        "reply_to_message": {},
    })
    assert captured[1].reply_to is None

    # Even if message.meta.reply_to is populated, router reads ONLY reply_to_message.
    await router._handle({
        "update_id": 3,
        "type": "message",
        "message": {
            "id": 12,
            "role": "user",
            "text": "still plain",
            "attachments": [],
            "meta": {"source": "user", "reply_to": {"id": 5, "role": "assistant", "excerpt": "should be ignored"}},
        },
        "reply_to_message": None,
    })
    assert captured[2].reply_to is None


@pytest.mark.asyncio
async def test_reply_to_cached_gallery_resolves_markers_without_downloading():
    r1 = store.add(JPEG_1, source="jarvis-app", filename="1.jpg", origin={"id": ATT_1})
    r2 = store.add(JPEG_2, source="jarvis-app", filename="2.jpg", origin={"id": ATT_2})
    # Simulate a Jarvis-sent photo linked via origin_links
    r3 = store.add(JPEG_3, source="jarvis", filename="3.jpg")
    store.link_origin(r3.id, "jarvis-app", ATT_3)

    client = FakeHubClient()
    captured = []
    router = _make_router(client, captured)

    await router._handle({
        "update_id": 42,
        "type": "message",
        "message": {
            "id": 105,
            "role": "user",
            "text": "What's in the second one?",
            "attachments": [],
            "blocks": None,
            "meta": {
                "source": "user",
                "reply_to": {"id": 98, "role": "assistant", "excerpt": "3 photos · Trip pics"},
            },
        },
        "reply_to_message": {
            "id": 98,
            "role": "assistant",
            "text": "Trip pics",
            "created_at": "2026-10-18T11:02:41+00:00",
            "attachments": [
                {"id": ATT_1, "kind": "image", "mime_type": "image/jpeg", "filename": "1.jpg", "size": len(JPEG_1)},
                {"id": ATT_2, "kind": "image", "mime_type": "image/jpeg", "filename": "2.jpg", "size": len(JPEG_2)},
                {"id": ATT_3, "kind": "image", "mime_type": "image/jpeg", "filename": "3.jpg", "size": len(JPEG_3)},
            ],
            "blocks": None,
            "meta": {"source": "agent"},
        },
    })

    (inbound,) = captured
    assert client.downloads == [], "all 3 attachments hit origin_links — zero downloads"
    assert inbound.attachments == [], "quoted media bytes are never inlined"
    assert inbound.user_text == "What's in the second one?"
    ctx = inbound.reply_to
    assert ctx is not None
    assert ctx.role == "assistant"
    assert ctx.text == "Trip pics"
    assert ctx.created_at == "2026-10-18T11:02:41+00:00"
    assert ctx.source == "agent"
    assert ctx.file_markers == [r1.marker(), r2.marker(), r3.marker()]


@pytest.mark.asyncio
async def test_reply_fallback_downloads_once_and_dedups_by_sha256():
    # Pre-existing record with same bytes, not yet linked to ATT_1 (e.g. pre-migration send)
    existing = store.add(JPEG_1, source="web", filename="trip.jpg",
                         origin={"url": "https://example.com/trip.jpg"})

    client = FakeHubClient(blobs_by_id={ATT_1: JPEG_1, ATT_2: PDF_1})
    captured = []
    router = _make_router(client, captured)

    update = {
        "update_id": 50,
        "type": "message",
        "message": {"id": 200, "role": "user", "text": "check these", "attachments": []},
        "reply_to_message": {
            "id": 150,
            "role": "assistant",
            "text": "Older files",
            "created_at": "2026-10-10T08:00:00+00:00",
            "attachments": [
                {"id": ATT_1, "kind": "image", "mime_type": "image/jpeg", "filename": "trip.jpg"},
                {"id": ATT_2, "kind": "file", "mime_type": "application/pdf", "filename": "doc.pdf"},
            ],
            "blocks": None,
            "meta": {"source": "notifier"},
        },
    }
    await router._handle(update)

    assert client.downloads == [ATT_1, ATT_2]
    ctx = captured[0].reply_to
    assert ctx.source == "notifier"
    # ATT_1 deduplicated onto `existing` without adding a duplicate row or touching its origin url
    assert ctx.file_markers[0] == existing.marker()
    assert store.get_by_origin("jarvis-app", ATT_1) == existing
    assert store.get(existing.id).origin == {"url": "https://example.com/trip.jpg"}
    # ATT_2 saved once as a new document record with caption from reply_to_message
    saved_pdf = store.get_by_origin("jarvis-app", ATT_2)
    assert saved_pdf is not None and saved_pdf.kind == "document"
    assert saved_pdf.filename == "doc.pdf" and saved_pdf.caption == "Older files"
    assert ctx.file_markers[1] == saved_pdf.marker()
    assert len(index.rows("SELECT id FROM files")) == 2

    # Quoting the same message a second time makes zero downloads!
    client.downloads.clear()
    await router._handle(update)
    assert client.downloads == []
    assert captured[1].reply_to.file_markers == [existing.marker(), saved_pdf.marker()]


@pytest.mark.asyncio
async def test_reply_skips_malformed_ids_and_notes_failed_downloads_and_blocks():
    client = FakeHubClient(blobs_by_id={ATT_1: RuntimeError("hub 503")})
    captured = []
    router = _make_router(client, captured)

    await router._handle({
        "update_id": 60,
        "type": "message",
        "message": {"id": 210, "role": "user", "text": "about this", "attachments": []},
        "reply_to_message": {
            "id": 205,
            "role": "assistant",
            "text": "Log workout",
            "created_at": "2026-10-18T12:00:00+00:00",
            "attachments": [
                {"id": "../../etc/passwd", "kind": "file", "mime_type": "text/plain"},
                {"id": ATT_1, "kind": "image", "mime_type": "image/jpeg"},
            ],
            "blocks": [
                {"kind": "form", "summary": "Push day: 2 to log", "state": "logged"},
            ],
            "meta": {"source": "reminder"},
        },
    })

    (inbound,) = captured
    assert client.downloads == [ATT_1], "malformed id was skipped before hitting client"
    assert inbound.reply_to.file_markers == ["[quoted attachment could not be retrieved]"]
    assert inbound.reply_to.block_summaries == ["[form (logged): Push day: 2 to log]"]
    assert inbound.reply_to.source == "reminder"


@pytest.mark.asyncio
async def test_reply_with_new_attachment_keeps_new_bytes_separate_from_quoted_marker():
    quoted_rec = store.add(JPEG_1, source="jarvis-app", filename="old.jpg", origin={"id": ATT_1})
    client = FakeHubClient(blobs_by_id={ATT_NEW: JPEG_2})
    captured = []
    router = _make_router(client, captured)

    await router._handle({
        "update_id": 70,
        "type": "message",
        "message": {
            "id": 300,
            "role": "user",
            "text": "Compare with this new one",
            "attachments": [
                {"id": ATT_NEW, "kind": "image", "mime_type": "image/jpeg", "filename": "new.jpg"},
            ],
        },
        "reply_to_message": {
            "id": 100,
            "role": "user",
            "text": "Old photo",
            "created_at": "2026-10-01T09:00:00+00:00",
            "attachments": [
                {"id": ATT_1, "kind": "image", "mime_type": "image/jpeg", "filename": "old.jpg"},
            ],
            "blocks": None,
            "meta": {"source": "user"},
        },
    })

    (inbound,) = captured
    assert client.downloads == [ATT_NEW], "only the new attachment is downloaded"
    assert len(inbound.attachments) == 1
    assert inbound.attachments[0]["origin_id"] == ATT_NEW
    assert inbound.attachments[0]["data"] == JPEG_2
    assert inbound.reply_to.file_markers == [quoted_rec.marker()]


@pytest.mark.asyncio
async def test_quoting_deleted_file_keeps_tombstone_without_redownloading():
    rec = store.add(JPEG_1, source="jarvis-app", filename="secret.jpg", origin={"id": ATT_1})
    deleted = store.delete(rec.id, "owner")
    client = FakeHubClient(blobs_by_id={ATT_1: JPEG_1, ATT_2: b""})
    captured = []
    router = _make_router(client, captured)

    await router._handle({
        "update_id": 80,
        "type": "message",
        "message": {"id": 310, "role": "user", "text": "what was that?", "attachments": []},
        "reply_to_message": {
            "id": 110,
            "role": "user",
            "text": "old",
            "attachments": [
                {"id": ATT_1, "kind": "image", "mime_type": "image/jpeg"},
                {"id": ATT_2, "kind": "image", "mime_type": "image/jpeg"},
            ],
        },
    })

    (inbound,) = captured
    assert client.downloads == [ATT_2], "deleted file is never re-downloaded from hub"
    assert inbound.reply_to.file_markers == [
        deleted.marker(),
        "[quoted attachment could not be retrieved]",
    ]
