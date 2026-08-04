"""The apps the agent exposes.

Content only — the mechanism lives in `registry.py`.
"""

from __future__ import annotations

from typing import Any

from gateway.apps.registry import AppEntry, AppSpec, register_app


# Stubs: the entries are declared, routable, and answer promptly, but serve no
# data yet. They return None — a valid success carrying no payload — rather
# than an empty listing, which would be indistinguishable from genuinely empty
# memory and would commit to a payload shape before the real one exists.
async def _stub(params: dict[str, str]) -> Any:
    return None


# Memory is read-only over this surface: both entries are GET, so a channel that
# honours `method` refuses a write outright rather than relying on the agent to
# decline it.
MEMORY_APP = register_app(
    AppSpec(
        ns="memory",
        name="Memory",
        entries=(
            AppEntry(id="list", method="GET", params=("path",), handler=_stub),
            AppEntry(id="read", method="GET", params=("path",), handler=_stub),
        ),
    )
)
