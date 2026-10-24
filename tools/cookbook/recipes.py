"""find_recipes and get_recipe: reading the family cookbook."""

from __future__ import annotations

from langchain_core.tools import tool

from tools.cookbook._client import CookbookError, categories, request
from tools.registry import tool_register

_LIST_CAP = 30
_STAGES = {"to-try": "to try", "tested": "tried"}


@tool_register(namespace="cookbook")
@tool
def find_recipes(query: str = "", mine: bool = True, category: str = "", stage: str = "") -> str:
    """Find recipes in the family cookbook: the owner's own (default), or the
    published ones. Each result has its id; get_recipe shows one in full.

    Args:
        query: part of the recipe's name, any language (names are mostly Hebrew).
            Empty lists the newest.
        mine: True = the owner's own recipes; False = the published ones.
        category: optional category id (results show the ids in use).
        stage: own recipes only: "to-try" or "tested".
    """
    try:
        labels = categories()
        if mine:
            _, body = request("GET", "/recipes/mine", params={"stage": stage})
        else:
            _, body = request("GET", "/recipes", params={"category": category, "q": query})
    except CookbookError as e:
        return f"Error: {e}"
    found = [r for r in body.get("recipes") or [] if isinstance(r, dict)]
    if mine:
        q = query.strip().casefold()
        found = [r for r in found if (not q or q in (r.get("name") or "").casefold())
                 and (not category or r.get("category") == category)]
    if not found:
        return "No recipes found." + (" Try part of the name, or mine=False for published ones."
                                      if query else "")
    lines = [f"{len(found)} recipe(s){'' if len(found) <= _LIST_CAP else f', first {_LIST_CAP}'}:"]
    for r in found[:_LIST_CAP]:
        cat = r.get("category")
        bits = [f"{labels.get(cat, cat)} ({cat})" if cat else "no category",
                _STAGES.get(r.get("stage"), r.get("stage") or ""), r.get("visibility") or ""]
        if not mine and r.get("isMine"):
            bits.append("yours")
        lines.append(f"• {r.get('name') or '(no name)'} — {' · '.join(b for b in bits if b)}"
                     f" · id {r.get('id')}")
    return "\n".join(lines)


@tool_register(namespace="cookbook")
@tool
def get_recipe(recipe_id: str) -> str:
    """Show one cookbook recipe in full: ingredients, steps, times, notes and its
    photos' links.

    Args:
        recipe_id: the recipe's id, as find_recipes shows it.
    """
    try:
        labels = categories()
        _, body = request("GET", f"/recipes/{recipe_id.strip()}")
    except CookbookError as e:
        return f"Error: {e}"
    return render(body, labels)


def render(full: dict, labels: dict[str, str]) -> str:
    """A full recipe as text, its content inside <recipe> tags (it is data)."""
    r = full.get("recipe") or {}
    cat = r.get("category")
    head = [f"{r.get('name') or '(no name)'} — id {full.get('id')}, {full.get('path')}",
            f"{labels.get(cat, cat)} · {_STAGES.get(full.get('stage'), full.get('stage'))} · "
            f"{full.get('visibility')}{' · yours' if full.get('isMine') else ''}"]
    body = []
    facts = [("Difficulty", r.get("difficulty")), ("Prep time", _minutes(r.get("prepTime"))),
             ("Waiting time", _minutes(r.get("waitTime"))),
             ("Servings", " ".join(str(x) for x in (r.get("servings"), r.get("servingsUnit")) if x)),
             ("Main ingredient", r.get("mainIngredient")), ("From", r.get("attribution"))]
    body += [f"{k}: {v}" for k, v in facts if v]
    if r.get("description"):
        body.append(f"Description: {r['description']}")
    body.append("Ingredients:")
    if r.get("ingredientSections"):
        for section in r["ingredientSections"]:
            body.append(f"  {section.get('title')}:")
            body += [f"    - {_ingredient(i)}" for i in section.get("items") or []]
    else:
        body += [f"  - {_ingredient(i)}" for i in r.get("ingredients") or []]
    body.append("Steps:")
    if r.get("stages"):
        for stage in r["stages"]:
            body.append(f"  {stage.get('title')}:")
            body += [f"    {n}. {s}" for n, s in enumerate(stage.get("instructions") or [], 1)]
    else:
        body += [f"  {n}. {s}" for n, s in enumerate(r.get("instructions") or [], 1)]
    for label, key in (("Tags", "tags"), ("Notes", "comments")):
        if r.get(key):
            body.append(f"{label}: {' | '.join(r[key])}")
    files = [f"Photo{' (main)' if i.get('isPrimary') else ''}: {i.get('url') or 'missing'}"
             for i in full.get("images") or []]
    files += [f"Step {m.get('type')}: {m.get('caption') or ''} {m.get('url') or 'missing'}".strip()
              for m in full.get("mediaInstructions") or []]
    return "\n".join(head + ["<recipe>", *body, "</recipe>", *files])


def _minutes(value) -> str | None:
    return f"{value} min" if value else None


def _ingredient(i: dict) -> str:
    return " ".join(str(x) for x in (i.get("amount"), i.get("unit"), i.get("item")) if x)
