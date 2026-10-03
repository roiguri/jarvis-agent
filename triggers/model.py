"""The trigger record and its on-disk shape.

``when`` and ``action`` are stored as single-key tagged objects
(``{"at": ...}``, ``{"send": {...}}``) so a new kind is a new tag, never a
migration of the existing rows.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class At:
    """A one-shot instant. Always timezone-aware."""
    instant: datetime


@dataclass(frozen=True)
class Send:
    """Deliver fixed text to the owner. No model involved."""
    text: str


@dataclass(frozen=True)
class Trigger:
    id: str
    when: At
    action: Send

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "when": {"at": self.when.instant.isoformat()},
            "action": {"send": {"text": self.action.text}},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Trigger":
        """Raises KeyError/ValueError on a row it can't read."""
        instant = datetime.fromisoformat(d["when"]["at"])
        if instant.tzinfo is None:
            raise ValueError(f"trigger {d.get('id')!r}: 'at' has no timezone offset")
        return cls(
            id=d["id"],
            when=At(instant),
            action=Send(d["action"]["send"]["text"]),
        )
