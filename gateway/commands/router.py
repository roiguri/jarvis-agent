"""Slash-command router — channel-agnostic dispatch for `/cmd` messages.

Sits between the channel layer (which produces `InboundMessage`) and the
agent layer (`ask_jarvis`). `try_handle_command` is the single entry point:
it inspects an inbound's `user_text`, and if the text is a registered
slash command, runs the handler and returns the reply string — bypassing
the LLM. Anything else (no leading slash) returns None and the caller
proceeds with the normal agent path.

The router never imports a channel. New channels inherit slash commands
for free by reusing this module.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from gateway.base import InboundMessage

logger = logging.getLogger(__name__)

Handler = Callable[[InboundMessage, list[str]], Awaitable[str]]


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    handler: Handler


_COMMANDS: dict[str, Command] = {}


def command(name: str, description: str) -> Callable[[Handler], Handler]:
    """Register a slash-command handler. Apply to an `async def` taking
    (inbound, args) and returning the reply text."""

    def _wrap(fn: Handler) -> Handler:
        key = name.lower().lstrip("/")
        if key in _COMMANDS:
            raise ValueError(f"Slash command /{key} already registered")
        _COMMANDS[key] = Command(name=key, description=description, handler=fn)
        return fn

    return _wrap


def list_commands() -> list[Command]:
    """All registered commands, sorted by name. Used by /help and by channels
    to populate their command menu."""
    return [_COMMANDS[k] for k in sorted(_COMMANDS)]


async def try_handle_command(inbound: InboundMessage) -> str | None:
    """If `inbound.user_text` is a slash command, dispatch and return the
    reply text. Otherwise return None — caller proceeds with the agent."""
    text = (inbound.user_text or "").strip()
    if not text.startswith("/"):
        return None

    parts = text.split()
    name = parts[0][1:].lower()
    args = parts[1:]

    cmd = _COMMANDS.get(name)
    if cmd is None:
        return f"Unknown command /{name} — try /help."

    try:
        return await cmd.handler(inbound, args)
    except Exception:
        logger.exception("Slash command /%s failed", name)
        return f"Command /{name} failed — check logs."
