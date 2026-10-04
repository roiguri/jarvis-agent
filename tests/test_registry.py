"""The tool registry's rules: which tools bind for a scope and active skills,
how sub-skills are discovered, and SKILL.md parsing. The real tool surface
itself is pinned by the golden files (test_tool_surface.py)."""

import pytest
from langchain_core.tools import tool

import agent  # noqa: F401  (importing the agent registers every tool)
from tools import registry


def make_tool(name):
    @tool(name)
    def t(x: str) -> str:
        """A fake tool."""
        return x
    return t


@pytest.fixture
def fake_registry(monkeypatch):
    """An empty private registry, and SKILL.md metadata from a dict; returns the dict."""
    monkeypatch.setattr(registry, "_REGISTRY", {})
    meta = {}
    monkeypatch.setattr(registry, "_skill_meta", lambda ns: meta.get(ns, ("", "")))
    reg = registry.tool_register
    reg(namespace="core")(make_tool("core_tool"))
    reg(namespace="core", scopes=("heartbeat",))(make_tool("tick_only"))
    reg(namespace="travel")(make_tool("trip_tool"))
    reg(namespace="media/radarr", destructive=True)(make_tool("radarr_tool"))
    reg(namespace="media/sonarr")(make_tool("sonarr_tool"))
    meta.update({
        "travel": ("Trips.", "Travel rules."),
        "media": ("Media index.", ""),
        "media/radarr": ("Movies.", "Radarr rules."),
        "media/sonarr": ("Series.", ""),
    })
    return meta


def names(tools):
    return sorted(t.name for t in tools)


# --- registration ---------------------------------------------------------------

def test_reregistering_with_same_metadata_is_idempotent(fake_registry):
    t = registry._REGISTRY["trip_tool"].tool
    assert registry.tool_register(namespace="travel")(t) is t


def test_reregistering_with_different_metadata_fails(fake_registry):
    t = registry._REGISTRY["trip_tool"].tool
    with pytest.raises(ValueError, match="different metadata"):
        registry.tool_register(namespace="travel", destructive=True)(t)


def test_register_must_wrap_a_tool(fake_registry):
    with pytest.raises(TypeError, match="ABOVE @tool"):
        registry.tool_register(namespace="travel")(lambda x: x)


# --- binding --------------------------------------------------------------------

def test_core_always_bound_skills_only_when_active(fake_registry):
    assert names(registry.get_tools("user", set())) == ["core_tool"]
    assert names(registry.get_tools("user", {"travel"})) == ["core_tool", "trip_tool"]


def test_scoped_tool_binds_only_in_its_scope(fake_registry):
    assert "tick_only" not in names(registry.get_tools("user", set()))
    assert "tick_only" in names(registry.get_tools("heartbeat", set()))


def test_activating_a_parent_binds_none_of_its_children(fake_registry):
    assert names(registry.get_tools("user", {"media"})) == ["core_tool"]
    assert names(registry.get_tools("user", {"media/radarr"})) == ["core_tool", "radarr_tool"]


def test_find_honors_activation_and_scope(fake_registry):
    assert registry.find("trip_tool", "user", set()) is None
    assert registry.find("trip_tool", "user", {"travel"}).name == "trip_tool"
    assert registry.find("tick_only", "user", set()) is None
    assert registry.find("nope", "user", {"travel"}) is None
    assert registry.namespace_of("trip_tool") == "travel", "named even when inactive"


def test_destructive_flag(fake_registry):
    assert registry.is_destructive("radarr_tool")
    assert not registry.is_destructive("trip_tool")
    assert not registry.is_destructive("nope")


def test_skill_namespaces_include_derived_parents(fake_registry):
    assert registry.skill_namespaces() == {"travel", "media", "media/radarr", "media/sonarr"}


# --- the prompt's skill block ---------------------------------------------------

def test_children_hidden_until_the_parent_is_active(fake_registry):
    idle = registry.compact_skill_list("user", set())
    assert "- media: Media index." in idle and "media/radarr" not in idle
    assert "Currently active in this conversation: none" in idle
    opened = registry.compact_skill_list("user", {"media"})
    assert "  - media/radarr: Movies." in opened and "  - media/sonarr: Series." in opened


def test_rules_only_for_active_skills(fake_registry):
    assert "Travel rules." not in registry.compact_skill_list("user", set())
    block = registry.compact_skill_list("user", {"travel"})
    assert "## travel — rules\nTravel rules." in block
    assert "Currently active in this conversation: travel" in block


def test_active_child_keeps_its_rules_without_its_parent(fake_registry):
    block = registry.compact_skill_list("user", {"media/radarr"})
    assert "## media/radarr — rules\nRadarr rules." in block
    assert "  - media/radarr" not in block, "still not listed under an inactive parent"


def test_missing_description_is_marked(fake_registry):
    fake_registry.pop("travel")
    assert "- travel: (no description)" in registry.compact_skill_list("user", set())


# --- SKILL.md frontmatter -------------------------------------------------------

@pytest.mark.parametrize("text, meta, body", [
    ("---\nname: x\ndescription: Does X.\n---\nRule one.\n", {"name": "x", "description": "Does X."}, "Rule one."),
    ("---\ndescription: Only a header.\n---\n", {"description": "Only a header."}, ""),
    ("No frontmatter at all.\n", {}, "No frontmatter at all."),
    ("---\ndescription: never closed\nbody\n", {}, "---\ndescription: never closed\nbody"),
    ("---\n: : bad yaml [\n---\nBody.\n", {}, "Body."),
    ("---\n- a list\n---\nBody.\n", {}, "Body."),
])
def test_parse_frontmatter(text, meta, body):
    assert registry._parse_frontmatter(text) == (meta, body)


# --- the real repo --------------------------------------------------------------

@pytest.mark.parametrize("ns", sorted(registry.skill_namespaces()))
def test_every_skill_has_a_description(ns):
    desc, _ = registry._skill_meta(ns)
    assert desc, f"tools/{ns}/SKILL.md is missing or has no description"


def test_every_tool_has_a_docstring():
    bare = [n for n, e in registry._REGISTRY.items() if not (e.tool.description or "").strip()]
    assert bare == []
