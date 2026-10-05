"""The collections app — browse collections and act on items, no model.

Reads go straight to the database through a read-only URI, never through the
tools' English output (same reasoning as `travel.py`). Writes call the very
functions the item tools wrap, so the app can do nothing chat can't and every
value is validated in one place. Creating collections and changing schemas stay
in chat, where the confirmation plane guards them.

Every caller-supplied value reaches SQLite only as a bound parameter.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any

from gateway.apps.registry import (
    AppEntry,
    AppInvalidRequest,
    AppNotFound,
    AppSpec,
    register_app,
)
from tools.collections import items as item_ops
from tools.collections._db import (
    COLLECTIONS_RO_URI,
    CollectionsError,
    CollectionsNotFound,
    _get_db,
    _open_states,
)

# The keys update_item's `changes` may carry — the same set the update_item
# tool takes. Anything else is refused rather than dropped.
_CHANGE_KEYS = {"title", "notes", "section", "status", "fields"}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(COLLECTIONS_RO_URI, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _ts(value: str | None) -> str | None:
    """SQLite's UTC 'YYYY-MM-DD HH:MM:SS' as ISO 8601 with its zone stated."""
    return value.replace(" ", "T") + "Z" if value else None


def _int(params: dict[str, str], key: str) -> int:
    raw = (params.get(key) or "").strip()
    try:
        return int(raw)
    except ValueError:
        raise AppInvalidRequest(f"{key} must be an integer, got {raw!r}")


def _item(row: sqlite3.Row, schema: dict) -> dict[str, Any]:
    st = schema["status"]
    return {
        "item_id": row["item_id"],
        "title": row["title"],
        "notes": row["notes"],
        "section": row["section"],
        "status": row["status"],
        # Derived here so the client never re-implements "which states hide".
        "closed": (row["status"] in st["closed"]) if st else None,
        # Typed values: money is {amount, currency}, tags a list, rating an int.
        "fields": json.loads(row["extra"]),
        "position": row["position"],
        "created_at": _ts(row["created_at"]),
        "updated_at": _ts(row["updated_at"]),
    }


def _summary(conn: sqlite3.Connection, r: sqlite3.Row) -> dict[str, Any]:
    schema = json.loads(r["schema"])
    cid = r["collection_id"]
    total = conn.execute("SELECT COUNT(*) FROM items WHERE collection_id = ?", (cid,)).fetchone()[0]
    open_count = None
    if schema["status"]:
        opens = _open_states(schema)
        open_count = conn.execute(
            f"SELECT COUNT(*) FROM items WHERE collection_id = ? "
            f"AND status IN ({','.join('?' * len(opens))})",
            (cid, *opens),
        ).fetchone()[0]
    return {
        "collection_id": cid,
        "name": r["name"],
        "archived": r["archived_at"] is not None,
        "has_status": schema["status"] is not None,
        "open_count": open_count,
        "item_count": total,
        "updated_at": _ts(r["updated_at"]),
    }


def _home_sync() -> dict[str, Any]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM collections ORDER BY name COLLATE NOCASE").fetchall()
        return {"collections": [_summary(conn, r) for r in rows]}
    finally:
        conn.close()


def _collection_sync(collection_id: int) -> dict[str, Any]:
    conn = _connect()
    try:
        r = conn.execute(
            "SELECT * FROM collections WHERE collection_id = ?", (collection_id,)
        ).fetchone()
        if r is None:
            raise AppNotFound(f"No collection {collection_id}")
        schema = json.loads(r["schema"])
        # Display order: by section (unsectioned last), then manual position —
        # the client groups consecutive rows under one heading.
        rows = conn.execute(
            "SELECT * FROM items WHERE collection_id = ? "
            "ORDER BY section IS NULL, section COLLATE NOCASE, position",
            (collection_id,),
        ).fetchall()
        return {
            "collection": {**_summary(conn, r), "schema": schema},
            "items": [_item(i, schema) for i in rows],
        }
    finally:
        conn.close()


def _write(fn):
    """Run a shared item operation on a read-write connection, mapping its
    refusals onto the app's closed error vocabulary."""
    conn = _get_db()
    try:
        return fn(conn)
    except CollectionsNotFound as e:
        raise AppNotFound(str(e))
    except CollectionsError as e:
        raise AppInvalidRequest(str(e))
    finally:
        conn.close()


def _parse_changes(raw: str) -> dict[str, Any]:
    """`changes` is a JSON object in a string — the only way a typed value
    (money, tags) crosses a string-only param. Same keys as update_item."""
    try:
        changes = json.loads(raw or "")
    except ValueError:
        raise AppInvalidRequest("changes must be a JSON object")
    if not isinstance(changes, dict) or not changes:
        raise AppInvalidRequest("changes must be a non-empty JSON object")
    unknown = sorted(set(changes) - _CHANGE_KEYS)
    if unknown:
        raise AppInvalidRequest(
            f"changes has unknown key(s) {unknown}; allowed: {sorted(_CHANGE_KEYS)}"
        )
    for key in ("title", "notes", "section", "status"):
        if key in changes and not isinstance(changes[key], str):
            raise AppInvalidRequest(f"changes.{key} must be a string")
    if "fields" in changes and not isinstance(changes["fields"], dict):
        raise AppInvalidRequest("changes.fields must be an object of {field: value}")
    return changes


def _update_sync(item_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    def run(conn):
        row, coll, _ = item_ops.update(conn, item_id, **changes)
        return {"item": _item(row, json.loads(coll["schema"]))}
    return _write(run)


def _quick_add_sync(collection_id: int, title: str, section: str) -> dict[str, Any]:
    def run(conn):
        coll = conn.execute(
            "SELECT * FROM collections WHERE collection_id = ?", (collection_id,)
        ).fetchone()
        if coll is None:
            raise CollectionsNotFound(f"No collection {collection_id}")
        ids, _, _ = item_ops.add(conn, coll, [{"title": title, "section": section}])
        row = conn.execute("SELECT * FROM items WHERE item_id = ?", (ids[0],)).fetchone()
        return {"item": _item(row, json.loads(coll["schema"]))}
    return _write(run)


def _delete_sync(item_id: int) -> dict[str, Any]:
    def run(conn):
        item, _ = item_ops.delete(conn, item_id)
        return {"deleted": item["item_id"]}
    return _write(run)


# Every handler blocks on SQLite; one event loop serves the poll, any in-flight
# turn and this drain, so each runs off-loop.
async def _home(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_home_sync)


async def _collection(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_collection_sync, _int(params, "collection_id"))


async def _update_item(params: dict[str, str]) -> Any:
    item_id = _int(params, "item_id")
    changes = _parse_changes(params.get("changes", ""))
    return await asyncio.to_thread(_update_sync, item_id, changes)


async def _quick_add(params: dict[str, str]) -> Any:
    collection_id = _int(params, "collection_id")
    title = (params.get("title") or "").strip()
    if not title:
        raise AppInvalidRequest("title is required")
    section = (params.get("section") or "").strip()
    return await asyncio.to_thread(_quick_add_sync, collection_id, title, section)


async def _delete_item(params: dict[str, str]) -> Any:
    return await asyncio.to_thread(_delete_sync, _int(params, "item_id"))


COLLECTIONS_APP = register_app(
    AppSpec(
        ns="collections",
        name="Collections",
        entries=(
            AppEntry(id="home", method="GET", handler=_home),
            AppEntry(id="collection", method="GET", params=("collection_id",),
                     handler=_collection),
            AppEntry(id="update_item", method="POST", params=("item_id", "changes"),
                     handler=_update_item),
            AppEntry(id="quick_add", method="POST",
                     params=("collection_id", "title", "section"), handler=_quick_add),
            AppEntry(id="delete_item", method="POST", params=("item_id",),
                     handler=_delete_item),
        ),
    )
)
