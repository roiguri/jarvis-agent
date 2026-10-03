"""The trigger record and its on-disk shape.

``when`` and ``action`` are stored as single-key tagged objects
(``{"at": ...}``, ``{"send": {...}}``, ``{"turn": {...}}``) so a new kind is a
new tag, never a migration of the existing rows.
"""

from dataclasses import dataclass
from datetime import datetime

# Who created a trigger. "owner": a chat turn, the owner present. "jarvis": a
# background turn (a heartbeat tick or a wake) — the only origin whose
# self-scheduling is limited (tools/core/scheduling.py). "code": a gate's
# handler, deterministic and keyed (triggers/gates.py).
ORIGIN_OWNER = "owner"
ORIGIN_JARVIS = "jarvis"
ORIGIN_CODE = "code"
# ``parent`` of a trigger created by an hourly tick rather than by a wake.
PARENT_TICK = "heartbeat"


@dataclass(frozen=True)
class At:
    """A one-shot instant. Always timezone-aware."""
    instant: datetime


@dataclass(frozen=True)
class Send:
    """Deliver fixed text to the owner. No model involved."""
    text: str


@dataclass(frozen=True)
class Turn:
    """Wake Jarvis: run a background turn with this instruction. ``task``
    names a HEARTBEAT.md task whose block the turn is shown."""
    instruction: str
    task: str | None = None


@dataclass(frozen=True)
class Trigger:
    id: str
    when: At
    action: Send | Turn
    origin: str = ORIGIN_OWNER
    # The trigger whose run created this one (or PARENT_TICK, or the gated
    # task's name for ORIGIN_CODE); None from chat.
    parent: str | None = None
    # Set by code that creates triggers it may later replace or cancel
    # (e.g. "arbox:<class>:brief"): at most one stored trigger per key.
    key: str | None = None

    def to_dict(self) -> dict:
        if isinstance(self.action, Turn):
            action = {"turn": {"instruction": self.action.instruction}}
            if self.action.task is not None:
                action["turn"]["task"] = self.action.task
        else:
            action = {"send": {"text": self.action.text}}
        d = {"id": self.id, "when": {"at": self.when.instant.isoformat()}, "action": action,
             "origin": self.origin}
        if self.parent is not None:
            d["parent"] = self.parent
        if self.key is not None:
            d["key"] = self.key
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Trigger":
        """Raises KeyError/ValueError on a row it can't read. Rows written
        before ``origin`` existed read as the owner's."""
        instant = datetime.fromisoformat(d["when"]["at"])
        if instant.tzinfo is None:
            raise ValueError(f"trigger {d.get('id')!r}: 'at' has no timezone offset")
        raw = d["action"]
        if "turn" in raw:
            action: Send | Turn = Turn(raw["turn"]["instruction"], raw["turn"].get("task"))
        else:
            action = Send(raw["send"]["text"])
        return cls(
            id=d["id"],
            when=At(instant),
            action=action,
            origin=d.get("origin", ORIGIN_OWNER),
            parent=d.get("parent"),
            key=d.get("key"),
        )
