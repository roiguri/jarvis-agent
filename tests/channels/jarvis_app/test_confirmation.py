"""AppConfirmationUI against a fake hub, using the real confirmation store."""

import pytest

from gateway.channels.jarvis_app.client import MessageAlreadyResolved
from gateway.channels.jarvis_app.confirmation import AppConfirmationUI
from gateway.confirmation.base import PendingAction
from gateway.confirmation.store import InMemoryConfirmationStore


class FakeClient:
    def __init__(self, raise_state=None):
        self.patches = []
        self.raise_state = raise_state
        self._next_id = 100

    async def send_message(self, body):
        self._next_id += 1
        return {"id": self._next_id}

    async def patch_message_state(self, message_id, state):
        self.patches.append((message_id, state))
        if self.raise_state:
            s, self.raise_state = self.raise_state, None
            raise MessageAlreadyResolved(s)


class FakeOutbox:
    async def notify_owner(self, text, **kw):
        return None


def build(raise_state=None):
    client = FakeClient(raise_state)
    ui = AppConfirmationUI(client)
    store = InMemoryConfirmationStore(ui, FakeOutbox(), "jarvis-app_1")
    ui.bind_store(store)
    return client, ui, store


def pend(store, cb):
    async def act():
        return "done"
    store._pending[cb] = PendingAction(act, "delete a thing", "ok", "cancelled")


async def tap(ui, cb, mid, action="confirm"):
    await ui.handle_action(
        action_id=action, message_id=mid, block_kind="confirmation", callback_id=cb
    )


@pytest.mark.asyncio
async def test_normal_confirm():
    c, ui, store = build()
    await ui.send_prompt("cb1", "delete a thing")
    pend(store, "cb1")
    await tap(ui, "cb1", 101)
    assert c.patches == [(101, "confirmed")]


@pytest.mark.asyncio
async def test_orphan_tap_expires():
    # This process never sent the prompt (a restart), and nothing is pending.
    c, ui, store = build()
    await tap(ui, "gone", 777)
    assert c.patches == [(777, "expired")]


@pytest.mark.asyncio
async def test_retap_reaffirms():
    c, ui, store = build()
    await ui.send_prompt("cb3", "delete a thing")
    pend(store, "cb3")
    await tap(ui, "cb3", 101)
    await tap(ui, "cb3", 101)
    assert c.patches == [(101, "confirmed"), (101, "confirmed")]


@pytest.mark.asyncio
async def test_retap_after_cancel():
    c, ui, store = build()
    await ui.send_prompt("cb4", "delete a thing")
    pend(store, "cb4")
    await tap(ui, "cb4", 101, action="cancel")
    await tap(ui, "cb4", 101)
    assert c.patches == [(101, "cancelled"), (101, "cancelled")]


@pytest.mark.asyncio
async def test_learns_settled_state_from_hub():
    c, ui, store = build(raise_state="confirmed")
    await tap(ui, "unknown", 555)  # tries expired; the hub says confirmed stands
    await tap(ui, "unknown", 555)  # now re-affirms confirmed
    assert c.patches == [(555, "expired"), (555, "confirmed")]


@pytest.mark.asyncio
async def test_ttl_expire():
    # No tap, so only the send-time handle exists.
    c, ui, store = build()
    await ui.send_prompt("cb6", "delete a thing")
    pend(store, "cb6")
    await ui.expire("cb6")
    assert c.patches == [(101, "expired")]


@pytest.mark.asyncio
async def test_no_handle_no_patch():
    # The prompt send failed, so there is no handle from either source.
    c, ui, store = build()
    await ui.expire("never-sent")
    assert c.patches == []


def test_resolved_memory_capped():
    c, ui, store = build()
    for i in range(200):
        ui._remember_resolved(f"cb{i}", "confirmed")
    assert len(ui._resolved) == 64
