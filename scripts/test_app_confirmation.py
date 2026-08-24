#!/usr/bin/env python3
"""Exercise AppConfirmationUI against a fake hub, using the real store."""
import asyncio, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))

from gateway.channels.jarvis_app.confirmation import AppConfirmationUI
from gateway.channels.jarvis_app.client import MessageAlreadyResolved
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


async def main():
    fails = []

    def check(name, got, want):
        ok = got == want
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {got!r}")
        if not ok:
            fails.append(f"{name}: got {got!r}, want {want!r}")

    # 1 — normal confirm, prompt sent by this process
    c, ui, store = build()
    await ui.send_prompt("cb1", "delete a thing")
    pend(store, "cb1")
    await tap(ui, "cb1", 101)
    check("normal confirm", c.patches, [(101, "confirmed")])

    # 2 — orphan: this process never sent the prompt (restart), nothing pending
    c, ui, store = build()
    await tap(ui, "gone", 777)
    check("orphan expires (was: no PATCH at all)", c.patches, [(777, "expired")])

    # 3 — re-tap after we already settled it
    c, ui, store = build()
    await ui.send_prompt("cb3", "delete a thing")
    pend(store, "cb3")
    await tap(ui, "cb3", 101)
    await tap(ui, "cb3", 101)
    check("re-tap re-affirms (was: silent no-op)", c.patches,
          [(101, "confirmed"), (101, "confirmed")])

    # 4 — cancel, then re-tap
    c, ui, store = build()
    await ui.send_prompt("cb4", "delete a thing")
    pend(store, "cb4")
    await tap(ui, "cb4", 101, action="cancel")
    await tap(ui, "cb4", 101)
    check("re-tap after cancel", c.patches, [(101, "cancelled"), (101, "cancelled")])

    # 5 — hub refuses our guess; we learn the truth and re-affirm it next time
    c, ui, store = build(raise_state="confirmed")
    await tap(ui, "unknown", 555)            # tries expired, hub says confirmed stands
    await tap(ui, "unknown", 555)            # now re-affirms confirmed
    check("learns settled state from hub", c.patches,
          [(555, "expired"), (555, "confirmed")])

    # 6 — TTL eviction path (no tap, so only the send-time handle exists)
    c, ui, store = build()
    await ui.send_prompt("cb6", "delete a thing")
    pend(store, "cb6")
    await ui.expire("cb6")
    check("TTL expire still works", c.patches, [(101, "expired")])

    # 7 — prompt send failed, so no handle from either source
    c, ui, store = build()
    await ui.expire("never-sent")
    check("no handle -> no PATCH", c.patches, [])

    # 8 — memory stays bounded
    c, ui, store = build()
    for i in range(200):
        ui._remember_resolved(f"cb{i}", "confirmed")
    check("resolved memory capped", len(ui._resolved), 64)

    print("\n" + ("ALL PASS" if not fails else "FAILURES:\n  " + "\n  ".join(fails)))
    return 1 if fails else 0


sys.exit(asyncio.run(main()))
