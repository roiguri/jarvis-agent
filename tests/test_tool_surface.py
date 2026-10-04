"""Snapshots of the tool schemas the model is bound with.

One golden file per namespace (name, destructive flag, scopes, and the schema
built from the docstring and arguments), so a docstring edit shows up in exactly
one file. surface.md sums it up: which tools each scope binds with nothing
active and with each skill active, and an estimate of what their schemas cost
per model call.
"""

import json

import pytest

import agent  # noqa: F401  (importing the agent registers every tool)
from langchain_core.utils.function_calling import convert_to_openai_tool
from tools import registry


def schema(tool) -> dict:
    return convert_to_openai_tool(tool)["function"]


def tokens(tools) -> int:
    """Characters ÷ 4 over the compact JSON schemas: an estimate, not the
    model's own count (that needs a network call), but deterministic."""
    return sum(len(json.dumps(schema(t), sort_keys=True, separators=(",", ":"))) for t in tools) // 4


def by_namespace() -> dict[str, list]:
    groups: dict[str, list] = {}
    for entry in registry._REGISTRY.values():
        groups.setdefault(entry.namespace, []).append(entry)
    return groups


@pytest.mark.parametrize("ns", sorted(by_namespace()))
def test_tool_schemas(ns, golden):
    doc = [{
        "name": e.tool.name,
        "destructive": e.destructive,
        "scopes": list(e.scopes) if e.scopes is not None else None,
        "schema": schema(e.tool),
    } for e in sorted(by_namespace()[ns], key=lambda e: e.tool.name)]
    golden(f"tools/{ns}.json", json.dumps(doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n")


def test_surface_summary(golden):
    lines = ["# Tool surface", "",
             "Tools bound per model call, by scope and active skill. Tokens ≈ characters ÷ 4",
             "of the compact JSON schemas.", ""]
    for scope in ("user", "heartbeat"):
        base = registry.get_tools(scope, set())
        lines += [f"## {scope}", "",
                  f"Always on: {len(base)} tools, ≈{tokens(base)} tokens — "
                  + ", ".join(sorted(t.name for t in base)), "",
                  "| Active skill | Tools | Tokens | Added |", "|---|---|---|---|"]
        for ns in sorted(registry.skill_namespaces()):
            bound = registry.get_tools(scope, {ns})
            added = len(bound) - len(base)
            lines.append(f"| {ns} | {len(bound)} | ≈{tokens(bound)} | +{added} tools, ≈+{tokens(bound) - tokens(base)} |")
        lines.append("")
    golden("surface.md", "\n".join(lines))
