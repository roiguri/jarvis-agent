"""Owner sends carry the Outbox event tag to the hub as `meta.source`, and an
untagged or unmapped send carries no meta (the hub records "agent")."""

import pytest

from gateway.blocks import Form, FormRow, TextField
from gateway.channels.jarvis_app.channel import JarvisAppChannel
from gateway.outbox import (
    EVENT_HEARTBEAT,
    EVENT_LLM_MEDIA,
    EVENT_MEDIA,
    EVENT_REMINDER,
    Outbox,
)


class FakeClient:
    def __init__(self):
        self.sent = []

    async def send_message(self, body):
        self.sent.append(body)
        return {"id": 1}

    async def upload_attachment(self, payload, **kw):
        return "att_1"


def form():
    return Form(summary="Log it", slug="log", title="Log",
                rows=(FormRow("Note", (TextField("note"),)),))


@pytest.mark.asyncio
@pytest.mark.parametrize("event, source", [
    (EVENT_HEARTBEAT, "heartbeat"),
    (EVENT_REMINDER, "reminder"),
    (EVENT_MEDIA, "notifier"),
    (EVENT_LLM_MEDIA, "notifier"),
])
async def test_event_sets_source(event, source):
    fc = FakeClient()
    outbox = Outbox(JarvisAppChannel(fc, owner_id="owner"))
    assert (await outbox.notify_owner("hi", event=event)).ok
    assert (await outbox.notify_owner_media("audio", b"ogg", "cap", event=event)).ok
    assert (await outbox.send_block_to_owner("fill", form(), event=event)).ok
    assert [b["meta"] for b in fc.sent] == [{"source": source}] * 3, \
        "text, media and block sends all carry the source"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [None, "something_new"])
async def test_untagged_or_unknown_sends_no_meta(event):
    fc = FakeClient()
    outbox = Outbox(JarvisAppChannel(fc, owner_id="owner"))
    assert (await outbox.notify_owner("hi", event=event)).ok
    assert (await outbox.notify_owner_media("audio", b"ogg", "cap", event=event)).ok
    assert (await outbox.send_block_to_owner("fill", form(), event=event)).ok
    assert all("meta" not in b for b in fc.sent), "hub default (agent) applies"
