"""Fakes for the seams the suite drives: the model, the Outbox, a channel."""

from gateway.outbox import SendOutcome


class FakeLLM:
    """Plays a script: each entry is an AIMessage to return or an exception to
    raise. Records every request it was sent."""

    model = "fake-model"

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages, **kwargs):
        self.sent.append(list(messages))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def tool_call(name, args, n):
    """A tool call as the model emits it, with a unique id from ``n``."""
    return {"name": name, "args": args, "id": f"call_{n}", "type": "tool_call"}


class FakeOutbox:
    """Records sends. ``fail_next`` makes that many sends fail first."""

    def __init__(self):
        self.sent: list[tuple[str, str]] = []
        self.meta: list[dict | None] = []
        self.fail_next = 0

    async def notify_owner(self, text, *, event=None, metadata=None):
        self.sent.append((event, text))
        self.meta.append(metadata)
        if self.fail_next:
            self.fail_next -= 1
            return SendOutcome(ok=False, error="offline")
        return SendOutcome(ok=True)

    def clear(self):
        self.sent.clear()
        self.meta.clear()


class FakeChannel:
    """A channel that records what it was asked to send to the owner."""

    def __init__(self):
        self.texts = []

    async def send_to_owner(self, text):
        self.texts.append(text)
