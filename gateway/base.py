"""
Gateway boundary contracts — the neutral types and ABCs that decouple Jarvis's
domain logic from any specific messaging system.

See docs/architecture/GATEWAY.md for the full design. This module is the single
source of truth for the Channel / Confirmation contracts. Channels live under
gateway/<channel>/ and implement these; tools, agent, and heartbeat code import
only from here (or from the channel-agnostic accessors), never from a concrete
channel module.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Awaitable, Callable


@dataclass
class InboundMessage:
    """Channel-agnostic message shape delivered by a channel to the app domain.

    `thread_id` is the only field the agent layer uses to namespace per-conversation
    state (LangGraph checkpointer key + chat_history.jsonl filter). Channels are
    responsible for producing a stable, channel-prefixed thread_id. The format is
    deliberately frozen at `telegram_<user_id>` for Phase 1 — the `:` separator
    change is a Phase 2 concern coupled to the checkpointer-key migration.
    """

    user_id: int
    chat_id: int
    thread_id: str
    user_text: str
    # Each: {kind, path, mime_type, source}. `path` is an ABSOLUTE,
    # channel-produced filesystem path the agent opens as-is — the channel
    # owns media storage; core/agent never resolve or name a channel.
    attachments: list[dict] = field(default_factory=list)


# Inbound handler contract: a channel produces an InboundMessage and awaits this
# handler, which returns the reply text the channel posts back (Plane 1 -> Plane 2).
OnMessage = Callable[[InboundMessage], Awaitable[str | None]]


class Channel(ABC):
    """Boundary between Jarvis's domain logic and one external messaging system.

    A channel is a thin adapter: it translates between an external protocol and
    the neutral contracts here. Anything richer belongs elsewhere (agent.py,
    memory tools, heartbeat).
    """

    name: str  # "telegram", "email", "whatsapp", "webhook", ...

    @abstractmethod
    async def send(self, chat_id: str, text: str, *, reply_to: str | None = None) -> None:
        """Reply to a known chat (Plane 2, reply path)."""

    @abstractmethod
    async def send_media(
        self, chat_id: str, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        """Reply with media to a known chat. Channels that can't represent `kind`
        raise NotImplementedError; the caller downgrades or skips."""

    @abstractmethod
    async def send_to_owner(self, text: str) -> None:
        """Proactive send to the channel's owner (Plane 2, proactive path).

        The decoupling seam for heartbeat / confirmation outcomes: callers have no
        chat_id. The channel reads its own owner-config env at construction and
        addresses internally."""

    @abstractmethod
    async def send_to_owner_media(
        self, kind: str, payload: bytes, caption: str | None = None
    ) -> None:
        """Proactive media send to the channel's owner (e.g. Sonarr/Radarr posters)."""

    @abstractmethod
    def authorize(self, raw_user_id: str) -> bool:
        """Is this user allowed to use Jarvis on this channel?"""

    @property
    @abstractmethod
    def owner_thread_id(self) -> str:
        """Canonical agent thread id for the owner's conversation on this
        channel (same value the channel's router stamps on inbound messages).
        Lets domain code address the owner's thread without knowing the
        channel's thread_id format."""

    async def send_stream(self, chat_id: str, chunks: AsyncIterator[str]) -> None:
        """Default: collect chunks, then send once. Streaming channels override."""
        full = "".join([c async for c in chunks])
        await self.send(chat_id, full)
