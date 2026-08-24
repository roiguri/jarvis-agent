"""
Form — the first interactive block kind: labelled, prefilled boxes the owner
corrects and submits in one tap.

A form is content, not a protocol: nothing is pending while it sits untapped,
and the submit comes back as ordinary inbound conversation. Everything here is
validated at construction so a bad form fails at its caller with a correctable
message, not as a hub 422 inside an async send. Two rules matter beyond what
the hub's schema already enforces:

- every field on a multi-field row needs a `unit` (a screen reader has only
  the row label to name bare boxes) — the hub checks this only at runtime;
- the ~row cap is purely local policy: a form is for correcting guesses, and
  anything longer is a survey that should have been a conversation.

`default` is a prepopulation, not a hint — but never a *mandatory* one:
an invented prefill is worse than an empty box, because submit-unchanged is
the designed happy path and would turn the guess into recorded fact.
"""

import re
import secrets
from dataclasses import dataclass

from gateway.blocks.base import Interactive

MAX_ROWS = 6

# What a form may resolve to. No "cancelled": a form has nothing to decline —
# expiry (the absence of a decision) is the only non-submit ending.
FORM_STATES = ("logged", "expired")

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_FIELD_ID_RE = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class TextField:
    """A free-text box. `unit` is the only text beside a box (there is no
    per-field label); `default` must be a string when present."""

    field_id: str
    default: str | None = None
    unit: str | None = None

    type = "text"

    def __post_init__(self):
        _check_field_id(self.field_id)
        if self.default is not None and not isinstance(self.default, str):
            raise ValueError(
                f"field {self.field_id!r}: a text field's default must be a string, "
                f"got {type(self.default).__name__}"
            )


@dataclass(frozen=True)
class NumberField:
    """A numeric box. `default` must be an int or float when present — bools
    are refused here even though the wire would coerce True to 1, because
    nothing should be relying on that."""

    field_id: str
    default: int | float | None = None
    unit: str | None = None

    type = "number"

    def __post_init__(self):
        _check_field_id(self.field_id)
        if self.default is not None and (
            isinstance(self.default, bool) or not isinstance(self.default, (int, float))
        ):
            raise ValueError(
                f"field {self.field_id!r}: a number field's default must be a number, "
                f"got {type(self.default).__name__}"
            )


@dataclass(frozen=True)
class FormRow:
    """One labelled thing being filled in, and the box(es) it takes."""

    label: str
    fields: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "fields", tuple(self.fields))
        if not self.label or not self.label.strip():
            raise ValueError("every form row needs a label")
        if not self.fields:
            raise ValueError(f"row {self.label!r} has no fields")
        if len(self.fields) > 1:
            missing = [f.field_id for f in self.fields if not f.unit]
            if missing:
                raise ValueError(
                    f"row {self.label!r} has multiple fields, so every one needs a "
                    f"unit (missing on: {', '.join(missing)})"
                )


@dataclass(frozen=True)
class Form(Interactive):
    """The outbound form. Note what it deliberately has no field for: `values`
    — that records what the *owner* submitted, stamped by the hub on the tap,
    and a pre-stamped form must be unconstructible, not merely forbidden.

    `slug` is the caller's semantic name; construction appends entropy to make
    the callback_id (`push-day-a3f1`) so uniqueness is never the caller's job.
    """

    slug: str = ""
    title: str = ""
    rows: tuple = ()
    subtitle: str | None = None
    submit_label: str | None = None

    kind = "form"

    def __post_init__(self):
        object.__setattr__(self, "rows", tuple(self.rows))
        if not _SLUG_RE.match(self.slug or ""):
            raise ValueError(
                f"slug {self.slug!r} must be lowercase kebab-case (a-z, 0-9, '-')"
            )
        if not self.title or not self.title.strip():
            raise ValueError("a form needs a title")
        if not self.summary or not self.summary.strip():
            raise ValueError("a form needs a summary — it is the notification preview")
        if not self.rows:
            raise ValueError("a form needs at least one row")
        if len(self.rows) > MAX_ROWS:
            raise ValueError(
                f"{len(self.rows)} rows — a form is for correcting a handful of "
                f"guesses, {MAX_ROWS} at most; anything longer belongs in conversation"
            )
        seen: set[str] = set()
        for row in self.rows:
            for f in row.fields:
                if f.field_id in seen:
                    raise ValueError(f"duplicate field_id {f.field_id!r}")
                seen.add(f.field_id)
        object.__setattr__(self, "callback_id", f"{self.slug}-{secrets.token_hex(2)}")

    def describe(self) -> str:
        """The form restated as text — what a tool returns so the thread itself
        records what was asked (field ids and prefills), and what a decline
        path echoes back for the model to say conversationally."""
        parts = []
        for row in self.rows:
            fields = ", ".join(
                f"{f.field_id}={f.default if f.default is not None else '(empty)'}"
                + (f" {f.unit}" if f.unit else "")
                for f in row.fields
            )
            parts.append(f"{row.label}: {fields}")
        return " · ".join(parts)


def _check_field_id(field_id: str) -> None:
    if not _FIELD_ID_RE.match(field_id or ""):
        raise ValueError(
            f"field_id {field_id!r} must be lowercase snake_case (a-z, 0-9, '_')"
        )


def render_submission(callback_id: str, values: dict) -> str:
    """A submitted form's values as turn text — the neutral rendering every
    channel's router hands the agent. `None` stays visibly distinct from zero
    or an empty string: "seen and left empty" is the one distinction the hub
    goes out of its way to preserve, so the rendering must not collapse it.
    The callback_id ties the submission back to the send recorded in the
    thread (the sending tool's return string)."""
    rendered = " · ".join(
        f"{k}: {'(left empty)' if v is None else v}" for k, v in values.items()
    )
    return f"[Submitted form {callback_id}] {rendered}"
