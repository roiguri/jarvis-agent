"""Collections tools, exercised directly — no model.

Mechanism only: each action does what it claims, refuses what it should, and
gates what §3 of the plan gates. One cumulative scenario: the tests share the
collections database and run in file order.
"""

import asyncio
import re

import pytest

from tools.collections import _fields
from tools.collections import (
    add_items, delete_item, list_items, manage_collection, update_item,
)


def call(tool, **kwargs) -> str:
    return tool.invoke(kwargs)


def check(label: str, got: str, *, contains=(), missing=()) -> None:
    problems = [f"expected {c!r}" for c in ((contains,) if isinstance(contains, str) else contains)
                if c.lower() not in got.lower()]
    problems += [f"should NOT contain {m!r}" for m in ((missing,) if isinstance(missing, str) else missing)
                 if m.lower() in got.lower()]
    assert not problems, f"{label}: {'; '.join(problems)}\ngot: {got!r}"


def first_id(listing: str) -> int:
    return int(re.search(r"#(\d+)", listing).group(1))


class FakeConfirmation:
    """Captures each request instead of prompting; tap() runs the action the
    way the store would after the owner confirms."""

    def __init__(self):
        self.pending = []

    def request_confirmation_sync(self, description, action_fn, result_ok_text="",
                                  result_cancel_text=""):
        self.pending.append((description, action_fn))
        return f"Confirmation request sent. Awaiting your approval to: {description}"

    def tap(self) -> str:
        _, action_fn = self.pending.pop()
        return asyncio.run(action_fn())

    def cancel(self) -> None:
        self.pending.pop()


@pytest.fixture
def confirm(monkeypatch):
    from gateway import factory

    fake = FakeConfirmation()
    monkeypatch.setattr(factory, "get_confirmation", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Field vocabulary
# ---------------------------------------------------------------------------


def test_field_normalisation():
    money = {"name": "price", "type": "money"}
    assert _fields.normalize(money, 3400) == {"amount": 3400, "currency": "ILS"}
    assert _fields.normalize(money, "3,400") == {"amount": 3400, "currency": "ILS"}
    assert _fields.normalize(money, "12.5 usd") == {"amount": 12.5, "currency": "USD"}
    assert _fields.normalize(money, "€30") == {"amount": 30, "currency": "EUR"}
    assert _fields.normalize(money, {"amount": 5, "currency": "gbp"}) == {"amount": 5, "currency": "GBP"}
    tags = {"name": "topics", "type": "tags"}
    assert _fields.normalize(tags, [" AI ", "agents", "ai", ""]) == ["ai", "agents"]
    assert _fields.normalize(tags, "ai, Agents") == ["ai", "agents"]
    choice = {"name": "size", "type": "choice", "options": ["S", "M", "L"]}
    assert _fields.normalize(choice, "m") == "M"
    assert _fields.normalize({"name": "r", "type": "rating"}, "4") == 4
    assert _fields.normalize({"name": "d", "type": "date"}, "2026-10-04") == "2026-10-04"
    for empty in (None, "", "  ", []):
        assert _fields.normalize(money, empty) is None


@pytest.mark.parametrize("ftype,value", [
    ("url", "example.com"), ("url", "ftp://x.org"), ("number", "lots"), ("number", True),
    ("money", "12 USD EUR"), ("money", -3), ("rating", 6), ("rating", 3.5),
    ("date", "12 Oct"), ("text", {"a": 1}),
])
def test_field_refusals(ftype, value):
    with pytest.raises(_fields.FieldError):
        _fields.normalize({"name": "f", "type": ftype}, value)


def test_field_definitions():
    assert _fields.check_defs([{"name": "Price", "type": "MONEY"}]) == [{"name": "price", "type": "money"}]
    for bad in ([{"name": "title", "type": "text"}], [{"name": "my price", "type": "money"}],
                [{"name": "x", "type": "bool"}], [{"name": "x", "type": "choice", "options": ["a"]}],
                [{"name": "x", "type": "text", "options": ["a", "b"]}],
                [{"name": "x", "type": "text"}, {"name": "X", "type": "url"}]):
        with pytest.raises(_fields.FieldError):
            _fields.check_defs(bad)


# ---------------------------------------------------------------------------
# Ungated creates and item basics
# ---------------------------------------------------------------------------


def test_plain_collection_and_items(confirm):
    check("an empty store says so", call(manage_collection, action="list"),
          contains="no collections")
    check("a plain list needs no confirmation",
          call(manage_collection, action="create", name="packing",
               items=[{"title": "Passport"}, {"title": "Charger", "notes": "USB-C"}]),
          contains=["Created collection packing", "2 item(s)", "no status"])
    assert not confirm.pending
    check("the same name differently cased is refused",
          call(manage_collection, action="create", name="Packing"), contains="already exists")
    check("status on a status-less list is refused",
          call(add_items, collection="packing", items=[{"title": "Hat", "status": "done"}]),
          contains="has no status")
    check("a custom field on a field-less list is refused, naming the schema",
          call(add_items, collection="packing", items=[{"title": "Hat", "fields": {"price": 3}}]),
          contains=["No field 'price'", "Its fields: none"])
    check("a field at top level points at 'fields'",
          call(add_items, collection="packing", items=[{"title": "Hat", "colour": "red"}]),
          contains="Unknown item key")
    out = call(list_items, collection="packing")
    check("both items listed in order", out, contains=["2 item(s)", "Passport", "Charger", "USB-C"])
    assert out.index("Passport") < out.index("Charger")


def test_default_status_and_sections(confirm):
    check("default status + sections need no confirmation",
          call(manage_collection, action="create", name="chores", status=True, sections=True,
               items=[{"title": "Fix tap", "section": "home"},
                      {"title": "Renew licence", "section": "admin"}]),
          contains=["Created collection chores", "open, done (closed)", "sections on"])
    assert not confirm.pending
    check("items default to the first open state",
          call(list_items, collection="chores"), contains=["[open] Fix tap", "## admin", "## home"])
    listed = call(list_items, collection="chores", section="home")
    item_id = first_id(listed)
    check("closing an item", call(update_item, item_id=item_id, status="DONE"),
          contains="status → done")
    check("closed items hide by default, and say so",
          call(list_items, collection="chores"),
          contains=["1 item(s)", "1 closed hidden"], missing="Fix tap")
    check("status='all' shows them", call(list_items, collection="chores", status="all"),
          contains=["[done] Fix tap"])
    check("an unknown state lists the real ones",
          call(update_item, item_id=item_id, status="finished"), contains="States: open, done")


# ---------------------------------------------------------------------------
# Gated create: custom fields and statuses
# ---------------------------------------------------------------------------

SHOPPING = dict(
    action="create", name="shopping", sections=True,
    statuses=["considering", "bought", "dropped"], closed_statuses=["bought", "dropped"],
    fields=[{"name": "url", "type": "url"}, {"name": "price", "type": "money"},
            {"name": "specs", "type": "text"}],
)


def test_custom_create_waits_for_confirm(confirm):
    out = call(manage_collection, **SHOPPING, items=[{"title": "Desk", "fields": {"price": "x"}}])
    check("bad first items are refused before anything is asked", out,
          contains=["Item 1", "price (money)"])
    assert not confirm.pending

    out = call(manage_collection, **SHOPPING,
               items=[{"title": "Standing desk", "section": "office",
                       "fields": {"url": "https://shop.example/desk", "price": 3400,
                                  "specs": "160x80"}}])
    check("custom schema asks first", out, contains=["Awaiting your approval", "price (money)",
                                                     "Standing desk"])
    check("nothing exists before the tap", call(manage_collection, action="list"),
          missing="shopping")
    confirm.cancel()
    check("cancel writes nothing", call(manage_collection, action="list"), missing="shopping")

    call(manage_collection, **SHOPPING,
         items=[{"title": "Standing desk", "section": "office",
                 "fields": {"url": "https://shop.example/desk", "price": 3400, "specs": "160x80"}}])
    check("the tap creates collection and items together", confirm.tap(),
          contains=["Created collection shopping", "1 item(s)"])
    check("the item carries its fields", call(list_items, collection="shopping"),
          contains=["[considering] Standing desk", "price: 3,400 ILS", "url: https://shop.example/desk"])


def test_drift_nudge(confirm):
    check("a new schema is shown the fields other collections use",
          call(manage_collection, action="create", name="gifts", status=True),
          contains=["Created collection gifts", "Fields in use elsewhere", "price (money) in shopping"])


def test_add_items_duplicates_and_tags(confirm):
    call(manage_collection, action="create", name="reading", sections=True,
         statuses=["unread", "read", "dropped"], closed_statuses=["read", "dropped"],
         fields=[{"name": "url", "type": "url"}, {"name": "topics", "type": "tags"}])
    confirm.tap()
    out = call(add_items, collection="reading", items=[
        {"title": "Knowledge graphs", "section": "AI", "notes": "x" * 400,
         "fields": {"url": "https://x.com/i/status/1", "topics": ["AI", "graphs"]}},
        {"title": "Skills v1.2", "section": "AI", "fields": {"url": "https://x.com/i/status/2"}},
    ])
    check("bulk add reports ids and existing tags", out,
          contains=["Added to reading", "Knowledge graphs", "topics in use: ai, graphs"])
    check("a repeat URL is kept but flagged",
          call(add_items, collection="reading",
               items=[{"title": "Same link", "fields": {"url": "https://x.com/i/status/2"}}]),
          contains="same url as #")
    check("long notes are cut in the list", call(list_items, collection="reading"),
          contains="(cut; list_items item_id=")
    check("tag filter", call(list_items, collection="reading", tag="Graphs"),
          contains="Knowledge graphs", missing="Skills")
    check("search covers field values", call(list_items, collection="reading", search="status/2"),
          contains=["Skills v1.2", "Same link"], missing="Knowledge")
    item_id = first_id(call(list_items, collection="reading", tag="graphs"))
    check("item_id shows the full notes", call(list_items, item_id=item_id),
          contains=["x" * 400], missing="cut;")
    check("an empty string clears a field",
          call(update_item, item_id=item_id, fields={"topics": ""}), missing="topics:")
    check("title cannot be cleared", call(update_item, item_id=item_id, title=" "),
          contains="can't be cleared")


# ---------------------------------------------------------------------------
# Schema edits
# ---------------------------------------------------------------------------


def test_schema_edit_add_rename_retype_remove(confirm):
    out = call(manage_collection, action="edit_schema", name="shopping",
               fields=[{"name": "rating", "type": "rating"}],
               rename_fields={"specs": "details"}, retype_fields={"price": "number"})
    check("one prompt lists every change and its effect", out,
          contains=["add field rating (rating)", "rename field specs → details",
                    "price (money) → price (number)", "1 value(s) converted"])
    check("nothing changes before the tap", call(list_items, collection="shopping"),
          contains="specs: 160x80")
    check("the tap applies it", confirm.tap(), contains="Updated shopping")
    check("values follow the rename and retype", call(list_items, collection="shopping"),
          contains=["details: 160x80", "price: 3400"])

    check("removing a field counts the losses",
          call(manage_collection, action="edit_schema", name="shopping", remove_fields=["details"]),
          contains="1 item(s) lose their details value")
    confirm.tap()
    check("value gone", call(list_items, collection="shopping"), missing="160x80")

    check("an empty edit is refused",
          call(manage_collection, action="edit_schema", name="shopping"),
          contains="Nothing to change")
    check("renaming onto an existing field is refused",
          call(manage_collection, action="edit_schema", name="shopping",
               rename_fields={"url": "price"}), contains="already exists")


def test_schema_edit_statuses(confirm):
    sid = first_id(call(list_items, collection="shopping"))
    call(update_item, item_id=sid, status="dropped")
    check("dropping a used state without a map is refused",
          call(manage_collection, action="edit_schema", name="shopping",
               statuses=["considering", "bought"], closed_statuses=["bought"]),
          contains=["1 in 'dropped'", "status_map"])
    check("with a map, the move is stated",
          call(manage_collection, action="edit_schema", name="shopping",
               statuses=["considering", "bought"], closed_statuses=["bought"],
               status_map={"dropped": "bought"}),
          contains="1 item(s) move from dropped to bought")
    confirm.tap()
    check("item moved", call(list_items, item_id=sid), contains="[bought]")

    check("turning status on sets items to open",
          call(manage_collection, action="edit_schema", name="packing", status=True),
          contains="2 item(s) set to open")
    confirm.tap()
    check("packing items now open", call(list_items, collection="packing"),
          contains="[open] Passport")
    check("turning sections off counts what's cleared",
          call(manage_collection, action="edit_schema", name="chores", sections=False),
          contains="2 item(s) lose their section")
    confirm.tap()
    check("no headings left", call(list_items, collection="chores", status="all"),
          missing="## ")


# ---------------------------------------------------------------------------
# Archive and delete
# ---------------------------------------------------------------------------


def test_archive_and_delete(confirm):
    check("archive", call(manage_collection, action="archive", name="gifts"), contains="Archived")
    check("archived refuses writes",
          call(add_items, collection="gifts", items=[{"title": "Book"}]), contains="Unarchive it first")
    check("archived still lists", call(manage_collection, action="list"), contains="gifts  [archived]")
    check("unarchive", call(manage_collection, action="unarchive", name="gifts"), contains="Unarchived")

    pid = first_id(call(list_items, collection="packing"))
    check("single delete needs no confirmation", call(delete_item, item_id=pid), contains="Deleted #")
    check("delete asks, with the count",
          call(manage_collection, action="delete", name="packing"),
          contains=["Awaiting your approval", "all 1 of its items"])
    check("the tap deletes collection and items", confirm.tap(), contains="Deleted packing")
    check("gone", call(manage_collection, action="list"), missing="packing")
    check("an unknown name lists the real ones",
          call(list_items, collection="packing"), contains=["No collection 'packing'", "reading"])

