"""Tool registry — the per-turn tool surface for the runtime layer.

Tools decorate themselves with ``@tool_register(namespace=..., destructive=...)``
so the catalog is populated at import time. ``get_tools`` / ``find`` resolve the
tool set for a turn — core tools are always bound; a skill's tools are bound
only when its namespace is in ``active_skills``. ``compact_skill_list`` renders
the available/active skill block for the system prompt. See
docs/architecture/RUNTIME.md.
"""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from dataclasses import dataclass

import yaml

from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

CORE_NAMESPACE = "core"


@dataclass(frozen=True)
class RegisteredTool:
    tool: BaseTool
    namespace: str
    destructive: bool
    scopes: tuple[str, ...] | None = None  # None = any scope


# name -> RegisteredTool. Populated by @tool_register side-effects at import.
_REGISTRY: dict[str, RegisteredTool] = {}


def tool_register(
    namespace: str,
    destructive: bool = False,
    scopes: tuple[str, ...] | None = None,
):
    """Record a ``@tool``-decorated BaseTool in the registry.

    Apply *above* ``@tool`` so this receives the constructed BaseTool and
    returns it unchanged — the tool is usable normally; registering only
    records its namespace/destructive metadata.

    ``destructive`` is metadata only: confirmation is still the inline
    ``get_confirmation()`` call inside each tool body. Registering does not wrap
    or alter the tool's behavior.

    ``scopes`` restricts which turn scopes may bind the tool: ``None`` (the
    default) means any scope; ``("heartbeat",)`` binds the tool only on
    heartbeat turns. Namespace activation still applies on top.
    """

    def _wrap(t: BaseTool) -> BaseTool:
        if not isinstance(t, BaseTool):
            raise TypeError(
                f"@tool_register must wrap a @tool (BaseTool); got {type(t)!r}. "
                "Place @tool_register ABOVE @tool."
            )
        existing = _REGISTRY.get(t.name)
        if existing is not None:
            # Module re-import (idempotent). Guard against conflicting metadata.
            if (existing.namespace, existing.destructive, existing.scopes) != (
                namespace,
                destructive,
                scopes,
            ):
                raise ValueError(
                    f"Tool {t.name!r} re-registered with different metadata: "
                    f"{(existing.namespace, existing.destructive, existing.scopes)} != "
                    f"{(namespace, destructive, scopes)}"
                )
            return t
        _REGISTRY[t.name] = RegisteredTool(t, namespace, destructive, scopes)
        return t

    return _wrap


def _parent_of(ns: str) -> str | None:
    """The parent namespace of a sub-skill (``media/radarr`` → ``media``), or
    None for a top-level namespace."""
    return ns.split("/", 1)[0] if "/" in ns else None


def _visible(
    entry: RegisteredTool, scope: str | None, active_skills: set[str] | None
) -> bool:
    """Core tools are always bound; a skill tool is bound only when its
    namespace is in active_skills. A tool registered with ``scopes`` is
    additionally bound only when the turn's scope is in that tuple."""
    if entry.scopes is not None and scope not in entry.scopes:
        return False
    if entry.namespace == CORE_NAMESPACE:
        return True
    return entry.namespace in (active_skills or set())


def get_tools(scope: str | None = None, active_skills: set[str] | None = None) -> list[BaseTool]:
    """Tools to bind for this turn: all core tools plus the tools of every
    currently-active skill, minus tools whose ``scopes`` excludes this turn's
    scope (tools registered without ``scopes`` — all of them today — bind in
    any scope)."""
    return [e.tool for e in _REGISTRY.values() if _visible(e, scope, active_skills)]


def find(
    name: str, scope: str | None = None, active_skills: set[str] | None = None
) -> BaseTool | None:
    """Resolve a tool call by name, honoring activation and scope. Returns
    None if the tool's skill is not active — the caller turns that into a
    ToolMessage telling the model to activate the skill first."""
    e = _REGISTRY.get(name)
    if e is None or not _visible(e, scope, active_skills):
        return None
    return e.tool


def namespace_of(name: str) -> str | None:
    """The namespace a tool is registered under, regardless of activation
    (so an 'activate the skill first' message can name the right skill)."""
    e = _REGISTRY.get(name)
    return e.namespace if e else None


def is_destructive(name: str) -> bool:
    """Whether a tool was registered with destructive=True.

    Read-only accessor for telemetry (tools/core/telemetry.py) so that layer
    does not reach into the private `_REGISTRY` to inspect the flag. Returns
    False for unknown tools — a record for a non-existent tool should not
    falsely report destructiveness."""
    e = _REGISTRY.get(name)
    return bool(e.destructive) if e else False


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a `---`-delimited YAML frontmatter header from the body.

    Returns (metadata_dict, body). Any malformed header degrades to
    ({}, text) — a bad SKILL.md must never crash a turn.
    """
    if not text.startswith("---"):
        return {}, text.strip()
    parts = text.split("\n", 1)
    if len(parts) < 2:
        return {}, ""
    rest = parts[1]
    end = rest.find("\n---")
    if end == -1:
        return {}, text.strip()
    fm_block = rest[:end]
    body = rest[end + 4:].lstrip("\n").strip()
    try:
        meta = yaml.safe_load(fm_block) or {}
        if not isinstance(meta, dict):
            meta = {}
    except yaml.YAMLError:
        meta = {}
    return meta, body


def _skill_meta(namespace: str) -> tuple[str, str]:
    """(description, instructions) for a skill, read fresh from
    tools/<namespace>/SKILL.md each call (per-turn, hot-reloadable).
    Missing/unreadable file → ("", "")."""
    path = os.path.join(os.path.dirname(__file__), namespace, "SKILL.md")
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError:
        return "", ""
    meta, body = _parse_frontmatter(raw)
    return str(meta.get("description", "")).strip(), body


def compact_skill_list(
    scope: str | None = None, active_skills: set[str] | None = None
) -> str:
    """The prompt skill block, sourced from each skill's SKILL.md:

    - one ``- <ns>: <description>`` line per top-level skill (always);
    - sub-skills are listed indented under their parent **only when that
      parent is active** (two-step discovery — children are hidden until
      the parent is activated);
    - the currently-active line;
    - for each *active* skill (top-level or sub-skill) that ships an
      instructions body, that body appended under a ``## <ns> — rules``
      heading (inactive skills cost only their one-liner).
    """
    active = sorted(active_skills) if active_skills else []
    all_ns = sorted(skill_namespaces())
    top = [n for n in all_ns if "/" not in n]
    children: dict[str, list[str]] = {}
    for n in all_ns:
        p = _parent_of(n)
        if p:
            children.setdefault(p, []).append(n)

    lines = []
    rule_blocks = []
    for ns in top:
        desc, instructions = _skill_meta(ns)
        lines.append(f"- {ns}: {desc or '(no description)'}")
        if instructions and ns in active:
            rule_blocks.append(f"## {ns} — rules\n{instructions}")
        if ns in children and ns in active:
            for child in children[ns]:
                cdesc, _ = _skill_meta(child)
                lines.append(f"  - {child}: {cdesc or '(no description)'}")
    # Rule bodies for active sub-skills render whenever the sub-skill itself
    # is active, even if its parent is not — guardrails stay attached to the
    # bound tools.
    for ns in all_ns:
        if "/" not in ns:
            continue
        if ns in active:
            _, cinstr = _skill_meta(ns)
            if cinstr:
                rule_blocks.append(f"## {ns} — rules\n{cinstr}")

    block = (
        "## Available skills (call activate_skill to load tools for this conversation):\n"
        + "\n".join(lines)
        + f"\n\n## Currently active in this conversation: {', '.join(active) if active else 'none'}"
    )
    if rule_blocks:
        block += "\n\n" + "\n\n".join(rule_blocks)
    return block


def import_all() -> None:
    """Import every ``tools.*`` module so ``@tool_register`` side-effects run.

    Idempotent: Python caches modules, so re-import does not re-run decorators.
    """
    import tools as _tools_pkg

    for mod in pkgutil.iter_modules(_tools_pkg.__path__, "tools."):
        if mod.name == "tools.registry":
            continue
        importlib.import_module(mod.name)


def registered_counts() -> tuple[int, int, int]:
    """(core tool count, skill tool count, distinct skill-namespace count)."""
    core = sum(1 for e in _REGISTRY.values() if e.namespace == CORE_NAMESPACE)
    skill = sum(1 for e in _REGISTRY.values() if e.namespace != CORE_NAMESPACE)
    namespaces = {
        e.namespace for e in _REGISTRY.values() if e.namespace != CORE_NAMESPACE
    }
    return core, skill, len(namespaces)


def skill_namespaces() -> set[str]:
    """All activatable skill namespaces: every registered non-core namespace
    plus every derived parent. A parent (e.g. ``media``) owns no tools of its
    own but is a valid activate_skill target — it expands its sub-skills in
    the prompt as a discovery aid."""
    leaves = {
        e.namespace for e in _REGISTRY.values() if e.namespace != CORE_NAMESPACE
    }
    parents = {p for ns in leaves if (p := _parent_of(ns))}
    return leaves | parents
