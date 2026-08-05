"""The apps the agent exposes — the one place that says which exist.

Each app lives in its own module (its spec beside the handlers that implement
it) and registers itself on import. This module imports them, so adding an app
is a new module plus one line here, and there is a single file to read to learn
what the agent serves.

Mechanism lives in `registry.py`; nothing here knows about a transport.
"""

from gateway.apps import memory as _memory  # noqa: F401 — registers "memory"
