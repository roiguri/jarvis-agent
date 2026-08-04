"""The apps the agent exposes.

Content only — the mechanism lives in `registry.py`. Declaring an entry here is
what makes a channel offer it; answering a call to one is separate, so an entry
can be declared before it can be served.
"""

from __future__ import annotations

from gateway.apps.registry import AppEntry, AppSpec, register_app

# Memory is read-only over this surface: both entries are GET, so a channel that
# honours `method` refuses a write outright rather than relying on the agent to
# decline it.
MEMORY_APP = register_app(
    AppSpec(
        ns="memory",
        name="Memory",
        entries=(
            AppEntry(id="list", method="GET", params=("path",)),
            AppEntry(id="read", method="GET", params=("path",)),
        ),
    )
)
