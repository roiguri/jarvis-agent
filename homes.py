"""Other homes for owner data outside memory — discovered from SKILL.md
frontmatter and optional live providers.

Mirrors ``files/references.py``: each subsystem owns its own store and may
register a zero-arg read-only summary hook via ``@homes.provider(...)``.
Skills that persist data without dynamic entity lists declare a one-line
``stores:`` key in their ``SKILL.md`` YAML frontmatter (ignored by
``tools/registry.py``, so normal chat and heartbeat prompts pay zero cost).

``summarize()`` merges both sources, calls each provider inside ``try/except``,
fails open to the static description on missing/failing stores, and caps each
entry so the review prompt stays bounded.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

MAX_LABELS = 20
MAX_LINE_CHARS = 300

_TOOLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools")
_SKIP_DIRS = {"core", "__pycache__"}


@dataclass(frozen=True)
class HomeProvider:
    name: str
    fn: Callable[[], str | None]
    fallback: str = ""


_PROVIDERS: dict[str, HomeProvider] = {}


def provider(name: str, *, fallback: str = ""):
    """Register ``fn() -> str | None`` as the live summary provider for ``name``."""
    def register(fn: Callable[[], str | None]):
        existing = _PROVIDERS.get(name)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f"home provider {name!r} is already registered")
        _PROVIDERS[name] = HomeProvider(name=name, fn=fn, fallback=fallback)
        return fn
    return register


def providers() -> list[HomeProvider]:
    return list(_PROVIDERS.values())


def cap_labels(labels: list[str], *, limit: int = MAX_LABELS) -> str:
    """Format up to ``limit`` labels, summarizing any overflow."""
    cleaned = [" ".join(str(x).split()) for x in labels if str(x).strip()]
    if len(cleaned) <= limit:
        return ", ".join(cleaned)
    return f"{', '.join(cleaned[:limit])}, +{len(cleaned) - limit} more"


def _truncate(text: str, limit: int = MAX_LINE_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1].rstrip() + "…"


def _skill_stores(tools_dir: str = _TOOLS_DIR) -> dict[str, str]:
    """Read optional ``stores:`` frontmatter from top-level ``tools/*/SKILL.md``."""
    from tools.registry import _parse_frontmatter

    found: dict[str, str] = {}
    try:
        entries = sorted(os.listdir(tools_dir))
    except OSError:
        return found
    for entry in entries:
        if entry in _SKIP_DIRS or entry.startswith("."):
            continue
        skill_md = os.path.join(tools_dir, entry, "SKILL.md")
        if not os.path.isfile(skill_md):
            continue
        try:
            with open(skill_md, encoding="utf-8") as f:
                meta, _ = _parse_frontmatter(f.read())
        except OSError:
            continue
        stores = meta.get("stores")
        if isinstance(stores, str) and stores.strip():
            found[entry] = stores.strip()
    return found


def _ensure_loaded() -> None:
    """Make sure built-in providers have run their registration decorators."""
    import heartbeat_state  # noqa: F401
    from tools import registry
    from triggers import store as _trigger_store  # noqa: F401

    registry.import_all()


def summarize(*, tools_dir: str = _TOOLS_DIR, load_defaults: bool = True) -> str:
    """Render a concise bulleted list of non-memory homes for owner data."""
    if load_defaults:
        _ensure_loaded()
    static_map = _skill_stores(tools_dir)
    names = sorted(set(static_map) | set(_PROVIDERS))
    lines: list[str] = []
    for name in names:
        prov = _PROVIDERS.get(name)
        static = static_map.get(name) or (prov.fallback if prov else "")
        live: str | None = None
        if prov is not None:
            try:
                raw = prov.fn()
                if isinstance(raw, str) and raw.strip():
                    live = raw.strip()
            except Exception:
                logger.warning("homes: provider %r failed; falling back to static description", name, exc_info=True)
        if static and live:
            body = f"{static} ({live})"
        elif live:
            body = live
        elif static:
            body = static
        else:
            continue
        lines.append(f"- {name}: {_truncate(body)}")
    return "\n".join(lines) if lines else "(none)"
