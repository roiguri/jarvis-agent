"""App registry — what the agent exposes as structured, queryable surfaces.

Channel-agnostic, and deliberately parallel to `gateway/commands/`: the agent
declares what it can answer, and a channel that has somewhere to show it (the
jarvis-app hub's Apps screen) publishes the list in its own wire shape. Nothing
here names a channel or a URL.

An entry's `id` is an identifier the agent routes on, never a URL path — the
transport is the channel's business. `params` is the closed set of parameter
names the entry accepts; a channel may use it, with `method`, to reject a
malformed call before it reaches the agent.

`method` uses HTTP verbs deliberately, despite this module naming no transport.
They are the precise vocabulary for the distinction meant here — GET asserts safe
and idempotent, which a looser "read" would not — and this surface is genuinely
request/response shaped, unlike the messaging contracts in `base.py`, where
transports really do disagree. A channel that speaks something else maps out of
these verbs; that is no harder than mapping out of any other spelling.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Namespaces, entry ids and param names are identifiers, not free text. Checked
# at registration — i.e. at import — because publishing a manifest is
# best-effort by design: a channel logs a rejected declare and carries on, so an
# unchecked typo would surface only as an Apps screen that is silently empty.
# Failing loudly at startup is the whole point of validating a second time.
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# A human label is shown as-is, so it is bounded but otherwise free text.
_NAME_MAX = 64

# Methods are HTTP verbs: the precise vocabulary for "safe and idempotent"
# versus "mutating", and what lets a channel refuse a write to a read entry
# without a round-trip.
_METHODS = ("GET", "POST")


@dataclass(frozen=True)
class AppEntry:
    """One callable entry point within an app."""

    id: str
    method: str  # "GET" for a read, "POST" for a write
    params: tuple[str, ...] = ()


@dataclass(frozen=True)
class AppSpec:
    """One app: a namespace, a human label, and its entry points."""

    ns: str
    name: str
    entries: tuple[AppEntry, ...] = field(default_factory=tuple)


_APPS: dict[str, AppSpec] = {}


def _check_identifier(kind: str, value: str) -> None:
    if not _IDENTIFIER_RE.match(value):
        raise ValueError(
            f"Invalid app {kind} {value!r} — must match {_IDENTIFIER_RE.pattern}"
        )


def register_app(spec: AppSpec) -> AppSpec:
    """Register an app under its namespace. Duplicate namespaces are a wiring
    bug, not a merge — the second registration raises."""
    if spec.ns in _APPS:
        raise ValueError(f"App namespace {spec.ns!r} already registered")
    _check_identifier("namespace", spec.ns)
    if not 1 <= len(spec.name) <= _NAME_MAX:
        raise ValueError(
            f"App {spec.ns!r} name must be 1..{_NAME_MAX} characters, got {len(spec.name)}"
        )
    # An app with no entries draws an icon that leads nowhere — a dead
    # affordance, refused here rather than rendered.
    if not spec.entries:
        raise ValueError(f"App {spec.ns!r} declares no entries — nothing could be called")
    for entry in spec.entries:
        _check_identifier("entry id", entry.id)
        if entry.method not in _METHODS:
            raise ValueError(
                f"App {spec.ns!r} entry {entry.id!r} method must be one of "
                f"{', '.join(_METHODS)}, got {entry.method!r}"
            )
        for param in entry.params:
            _check_identifier("param name", param)
    _APPS[spec.ns] = spec
    return spec


def list_apps() -> list[AppSpec]:
    """Every registered app, sorted by namespace. Used by channels to publish
    the manifest."""
    return [_APPS[ns] for ns in sorted(_APPS)]
