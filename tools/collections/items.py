"""The item tools: add, update, list, delete. No confirmation — a single item
is cheap to put back, and the schema they write against is already approved.

Each write is a plain function (add / update / delete) raising CollectionsError,
wrapped by its tool; the collections app's quick actions call the same functions,
so validation lives in one place."""

import json
import sqlite3

from langchain_core.tools import tool

from tools.collections._db import (
    CollectionsError,
    _check_items,
    _check_section,
    _check_state,
    _check_values,
    _get_db,
    _insert_items,
    _item_line,
    _open_states,
    _require_collection,
    _require_item,
    _require_writable,
    _schema,
    _tags_in,
    _touch,
)
from tools.registry import tool_register

_LIST_CAP = 200


@tool_register(namespace="collections")
@tool
def add_items(collection: str, items: list[dict]) -> str:
    """Add one or more items to a collection, in one call.

    Args:
        collection: the collection's exact name (manage_collection action='list'
            if unsure).
        items: each {title, notes?, section?, status?, fields?}. `fields` is
            {field_name: value} for the collection's custom fields only; money
            takes 3400, '3400 USD' or {amount, currency}; tags takes a list.
            status defaults to the first open state. Never invent a value
            that wasn't given.
    """
    conn = _get_db()
    try:
        coll = _require_collection(conn, collection)
        schema = _schema(coll)
        ids, rows, dupes = add(conn, coll, items)
        out = [f"Added to {coll['name']}: " + ", ".join(
            f"#{i} {r['title']}" for i, r in zip(ids, rows))]
        if dupes:
            out.append("Possible duplicates (both kept; delete one if so): " + "; ".join(dupes))
        tags = _tags_in(conn, coll["collection_id"], schema)
        if tags:
            out.append(tags)
        return "\n".join(out)
    except CollectionsError as e:
        return f"Error: {e}"
    finally:
        conn.close()


def add(conn: sqlite3.Connection, coll: sqlite3.Row, items) -> tuple[list[int], list[dict], list[str]]:
    """Validate every item, then insert all of them or none."""
    _require_writable(coll)
    schema = _schema(coll)
    if not items:
        raise CollectionsError("items is empty — pass at least one {title, ...}.")
    rows = _check_items(schema, items)
    dupes = _duplicates(conn, coll["collection_id"], schema, rows)
    ids = _insert_items(conn, coll["collection_id"], rows)
    conn.commit()
    return ids, rows, dupes


def _duplicates(conn: sqlite3.Connection, cid: int, schema: dict, rows: list[dict]) -> list[str]:
    url_fields = [f["name"] for f in schema["fields"] if f["type"] == "url"]
    found = []
    for r in rows:
        hit = conn.execute(
            "SELECT item_id FROM items WHERE collection_id = ? AND lower(title) = lower(?)",
            (cid, r["title"]),
        ).fetchone()
        if hit:
            found.append(f"{r['title']!r} has the same title as #{hit[0]}")
            continue
        for name in url_fields:
            if name not in r["extra"]:
                continue
            hit = conn.execute(
                "SELECT item_id FROM items WHERE collection_id = ? "
                "AND json_extract(extra, ?) = ?",
                (cid, f"$.{name}", r["extra"][name]),
            ).fetchone()
            if hit:
                found.append(f"{r['title']!r} has the same {name} as #{hit[0]}")
                break
    return found


@tool_register(namespace="collections")
@tool
def update_item(
    item_id: int,
    title: str | None = None,
    notes: str | None = None,
    section: str | None = None,
    status: str | None = None,
    fields: dict | None = None,
) -> str:
    """Edit one item. Pass only what changes; an empty string clears notes,
    section or a field value.

    Args:
        item_id: the item's id (list_items shows it as #id).
        title: new title (cannot be cleared).
        notes: free text.
        section: the heading it sits under, if the collection has sections.
        status: one of the collection's states, e.g. marking it done or read.
        fields: {field_name: value} for custom fields; "" clears one.
    """
    conn = _get_db()
    try:
        row, coll, said = update(conn, item_id, title, notes, section, status, fields)
        schema = _schema(coll)
        out = f"Updated in {coll['name']} ({'; '.join(said)}):\n{_item_line(row, schema)}"
        tags = _tags_in(conn, coll["collection_id"], schema) if fields else ""
        return out + (f"\n{tags}" if tags else "")
    except CollectionsError as e:
        return f"Error: {e}"
    finally:
        conn.close()


def update(conn: sqlite3.Connection, item_id, title=None, notes=None, section=None,
           status=None, fields=None) -> tuple[sqlite3.Row, sqlite3.Row, list[str]]:
    """Apply the given changes to one item; None leaves a value alone."""
    item, coll = _require_item(conn, item_id)
    _require_writable(coll)
    schema = _schema(coll)
    sets, args, said = [], [], []
    if title is not None:
        t = title.strip()
        if not t:
            raise CollectionsError("title can't be cleared; give the new title.")
        sets.append("title = ?"), args.append(t), said.append(f"title → {t}")
    if notes is not None:
        n = notes.strip() or None
        sets.append("notes = ?"), args.append(n), said.append("notes updated" if n else "notes cleared")
    if section is not None:
        s = _check_section(schema, section)
        sets.append("section = ?"), args.append(s), said.append(f"section → {s}" if s else "section cleared")
    if status is not None:
        st = _check_state(schema, status)
        if st is None:
            raise CollectionsError("status can't be cleared while the collection has status.")
        sets.append("status = ?"), args.append(st), said.append(f"status → {st}")
    if fields:
        extra = _check_values(schema, fields, json.loads(item["extra"]))
        sets.append("extra = ?"), args.append(json.dumps(extra, ensure_ascii=False))
        said.append("fields: " + ", ".join(fields))
    if not sets:
        raise CollectionsError(f"Nothing to update on #{item['item_id']} — pass a value to change.")
    conn.execute(
        f"UPDATE items SET {', '.join(sets)}, updated_at = datetime('now') WHERE item_id = ?",
        (*args, item["item_id"]),
    )
    _touch(conn, coll["collection_id"])
    conn.commit()
    row = conn.execute("SELECT * FROM items WHERE item_id = ?", (item["item_id"],)).fetchone()
    return row, coll, said


@tool_register(namespace="collections")
@tool
def list_items(
    collection: str = "",
    status: str = "",
    section: str = "",
    tag: str = "",
    search: str = "",
    item_id: int = 0,
) -> str:
    """List a collection's items with their ids. Closed items (e.g. done, read,
    dropped) are hidden unless asked for. Long notes are cut; pass item_id to
    see one item in full.

    Args:
        collection: the collection's exact name. Not needed with item_id.
        status: "" = open items only; "all" = everything; or one state.
        section: only items under this section.
        tag: only items carrying this tag.
        search: text to find in titles, notes and field values (e.g. a URL).
        item_id: show just this item, in full.
    """
    conn = _get_db()
    try:
        if item_id:
            item, coll = _require_item(conn, item_id)
            schema = _schema(coll)
            section_note = f" (section: {item['section']})" if item["section"] else ""
            return f"In {coll['name']}{section_note}:\n{_item_line(item, schema, full=True)}"

        coll = _require_collection(conn, collection)
        schema = _schema(coll)
        where, args = ["collection_id = ?"], [coll["collection_id"]]
        st = (status or "").strip().lower()
        hidden = ""
        if st == "all":
            pass
        elif st:
            where.append("status = ?"), args.append(_check_state(schema, st))
        elif schema["status"]:
            opens = _open_states(schema)
            where.append(f"status IN ({','.join('?' * len(opens))})"), args.extend(opens)
            n_closed = conn.execute(
                f"SELECT COUNT(*) FROM items WHERE collection_id = ? "
                f"AND status NOT IN ({','.join('?' * len(opens))})",
                (coll["collection_id"], *opens),
            ).fetchone()[0]
            if n_closed:
                hidden = f" ({n_closed} closed hidden; status='all' shows them)"
        if (section or "").strip():
            where.append("section = ? COLLATE NOCASE"), args.append(section.strip())
        if (tag or "").strip():
            tag_fields = [f["name"] for f in schema["fields"] if f["type"] == "tags"]
            if not tag_fields:
                raise CollectionsError(f"{coll['name']} has no tags field.")
            where.append("(" + " OR ".join(
                "EXISTS (SELECT 1 FROM json_each(items.extra, ?) WHERE value = ?)"
                for _ in tag_fields) + ")")
            for name in tag_fields:
                args.extend([f"$.{name}", tag.strip().lower()])
        if (search or "").strip():
            where.append("(title LIKE ? OR notes LIKE ? OR extra LIKE ?)")
            args.extend([f"%{search.strip()}%"] * 3)

        rows = conn.execute(
            f"SELECT * FROM items WHERE {' AND '.join(where)} "
            "ORDER BY section IS NULL, section COLLATE NOCASE, position",
            args,
        ).fetchall()
        archived = " [archived, read-only]" if coll["archived_at"] else ""
        head = f"{coll['name']}{archived} — {len(rows)} item(s){hidden}"
        if not rows:
            return head
        out, current = [head], object()
        for r in rows[:_LIST_CAP]:
            if schema["sections"] and r["section"] != current:
                current = r["section"]
                out.append(f"\n## {current or '(no section)'}")
            out.append(_item_line(r, schema))
        if len(rows) > _LIST_CAP:
            out.append(f"…{len(rows) - _LIST_CAP} more; narrow with section, tag or search.")
        return "\n".join(out)
    except CollectionsError as e:
        return f"Error: {e}"
    finally:
        conn.close()


@tool_register(namespace="collections")
@tool
def delete_item(item_id: int) -> str:
    """Delete one item, e.g. one added by mistake. To mark something finished,
    set its status instead — closed items stay answerable later.

    Args:
        item_id: the item's id.
    """
    conn = _get_db()
    try:
        item, coll = delete(conn, item_id)
        return f"Deleted #{item['item_id']} {item['title']!r} from {coll['name']}."
    except CollectionsError as e:
        return f"Error: {e}"
    finally:
        conn.close()


def delete(conn: sqlite3.Connection, item_id) -> tuple[sqlite3.Row, sqlite3.Row]:
    item, coll = _require_item(conn, item_id)
    _require_writable(coll)
    conn.execute("DELETE FROM items WHERE item_id = ?", (item["item_id"],))
    _touch(conn, coll["collection_id"])
    conn.commit()
    return item, coll
