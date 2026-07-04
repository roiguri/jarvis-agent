"""Meta-tools that expose Tier-2 skills on demand.

These are the only tools whose return value mutates graph state: they return
a sentinel dict that the agent's tool node interprets to update
``active_skills``. Every other tool returns a plain string.

The activatable skills (and their purpose) are advertised in the system
prompt's skill block, generated from the registry — these tool docstrings
stay generic so skill names live in exactly one place (the registry).
"""

from langchain_core.tools import tool

from tools.registry import tool_register, skill_namespaces


def _split_known(namespaces: list[str]) -> tuple[list[str], list[str]]:
    """Partition requested namespaces into (known, unknown), order-preserving."""
    if isinstance(namespaces, str):
        namespaces = [namespaces]
    known_all = skill_namespaces()
    known, unknown, seen = [], [], set()
    for n in namespaces or []:
        n = str(n).strip()
        if not n or n in seen:
            continue
        seen.add(n)
        (known if n in known_all else unknown).append(n)
    return known, unknown


@tool_register(namespace="core")
@tool
def activate_skill(namespaces: list[str]) -> dict:
    """Load the tools for one or more skills into this conversation.

    Call this the moment you realize you need a capability you don't currently
    have — the skill's tools become available immediately, in this same turn,
    so act on the request without asking the user to repeat themselves. The
    available skills and what each is for are listed under "Available skills"
    in your context. Some skills expose sub-skills (shown indented once the
    parent is active, e.g. ``media/radarr``); activate those the same way."""
    known, unknown = _split_known(namespaces)
    parts: list[str] = []
    if known:
        parts.append(f"Activated: {', '.join(known)}. Tools are now available.")
    if unknown:
        avail = ", ".join(sorted(skill_namespaces())) or "(none)"
        parts.append(
            f"Unknown skill(s) ignored: {', '.join(unknown)}. "
            f"Available skills: {avail}."
        )
    if not known and not unknown:
        parts.append("No skills specified.")
    return {"_activate": known, "content": " ".join(parts)}


@tool_register(namespace="core")
@tool
def deactivate_skill(namespaces: list[str]) -> dict:
    """Drop a skill's tools from this conversation to shrink the tool surface.

    Use when a skill is clearly no longer needed for the rest of the
    conversation."""
    known, unknown = _split_known(namespaces)
    parts: list[str] = []
    if known:
        parts.append(f"Deactivated: {', '.join(known)}.")
    if unknown:
        avail = ", ".join(sorted(skill_namespaces())) or "(none)"
        parts.append(
            f"Unknown skill(s) ignored: {', '.join(unknown)}. "
            f"Available skills: {avail}."
        )
    if not known and not unknown:
        parts.append("No skills specified.")
    return {"_deactivate": known, "content": " ".join(parts)}
