"""
Neutral interactive-block contracts — what a channel may carry beyond text.

A Block describes *intent* (what is being asked), never wire shape: rendering
one into a concrete payload is a channel adapter's job, so nothing in this
package imports a channel or names one. `Interactive` marks the kinds that can
be resolved after a tap — they carry a `callback_id`; a kind without one (a
card) is display-only. `BlockAction` is the inbound mirror: one neutral tap,
built by a channel's router from its own update shape.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Block:
    """Base of every outbound block. `kind` is the wire discriminator each
    subclass fixes; `summary` is the block's one-line stand-in where the full
    render doesn't fit (notification preview, collapsed list)."""

    summary: str

    kind = ""  # overridden per subclass


@dataclass(frozen=True)
class Interactive(Block):
    """A block that resolves: it carries the callback_id a later tap names.
    Which terminal states it may resolve to is per-kind, declared beside the
    kind's own dataclass."""

    callback_id: str = ""


@dataclass(frozen=True)
class BlockAction:
    """One tap on a live block, translated out of a channel's update shape.

    `values` is form-submission data (field_id -> str | int | float | None)
    and None for every other kind — mirroring the wire, where only a block
    that declared fields may carry it.
    """

    kind: str
    action_id: str
    message_id: int
    callback_id: str | None
    values: dict | None = None
