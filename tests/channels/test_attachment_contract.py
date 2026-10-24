"""Every channel hands core the same attachment shape: the file's bytes and hints,
never a path. A new channel adds its case here."""
import asyncio
import types

from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.channels.jarvis_app.router import JarvisAppInboundRouter
from gateway.channels.telegram.channel import TelegramChannel
from gateway.channels.telegram.router import TelegramInboundRouter

KEYS = {"kind", "mime_type", "data", "filename", "source", "origin_id"}


def check(att: dict, *, source: str) -> None:
    assert set(att) == KEYS, f"unexpected attachment keys: {sorted(att)}"
    assert isinstance(att["data"], bytes) and att["data"]
    assert att["source"] == source
    assert att["kind"] in {"image", "audio", "video", "document", "file"}
    assert isinstance(att["origin_id"], str) and att["origin_id"]


# --- Telegram ----------------------------------------------------------------

def _telegram(captured):
    channel = TelegramChannel(owner_id=7)

    async def download_media(file_id):
        return b"%PDF-1.4 bytes of " + file_id.encode()
    channel.download_media = download_media
    router = TelegramInboundRouter(channel, on_message=None)

    async def dispatch(inbound):
        captured.append(inbound)
    router._dispatch = dispatch
    return router


def _update(**message):
    message.setdefault("caption", None)
    msg = types.SimpleNamespace(from_user=types.SimpleNamespace(id=7), media_group_id=None,
                                **message)
    return types.SimpleNamespace(message=msg, effective_chat=types.SimpleNamespace(id=7))


def test_telegram_document_keeps_its_filename_and_has_no_placeholder():
    captured = []
    router = _telegram(captured)
    doc = types.SimpleNamespace(file_id="BQACAgQAAxkBAAI", mime_type="application/pdf",
                                file_name="lease.pdf")
    asyncio.run(router.handle_document(_update(document=doc), None))
    (inbound,) = captured
    (att,) = inbound.attachments
    check(att, source="telegram")
    assert att["filename"] == "lease.pdf" and att["kind"] == "document"
    assert inbound.user_text == "", "the file's marker says what arrived"


def test_telegram_voice():
    captured = []
    router = _telegram(captured)
    voice = types.SimpleNamespace(file_id="AwACAgQAAxkBAAI", mime_type="audio/ogg")
    asyncio.run(router.handle_voice(_update(voice=voice, caption="listen"), None))
    (att,) = captured[0].attachments
    check(att, source="telegram")
    assert att["kind"] == "audio" and captured[0].user_text == "listen"


# --- jarvis-app ----------------------------------------------------------------

class _HubClient:
    async def download_attachment(self, att_id):
        return b"\x89PNG\r\n\x1a\n" + att_id.encode()

    async def post_event(self, *a, **kw):
        return None

    async def send_message(self, body):
        return {"id": 1}


def test_jarvis_app_attachment():
    captured = []

    async def on_message(inbound):
        captured.append(inbound)
        return None

    client = _HubClient()
    router = JarvisAppInboundRouter(JarvisAppChannel(client, owner_id="owner"), client,
                                    on_message, confirmation_ui=None)
    att_id = "att_" + "0" * 26
    update = {"type": "message", "message": {"role": "user", "text": "", "attachments": [
        {"id": att_id, "kind": "image", "mime_type": "image/png", "filename": "plant.png"}]}}
    asyncio.run(router._handle(update))
    (inbound,) = captured
    (att,) = inbound.attachments
    check(att, source="jarvis-app")
    assert att["filename"] == "plant.png" and att["origin_id"] == att_id
    assert inbound.user_text == ""
