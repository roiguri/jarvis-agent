"""Collections storage: the connection, the schema, and the helpers every tool shares.

A collection *is* its schema: the JSON in `collections.schema` names its custom
fields, its status states (or null), and whether items carry a section. Item
values for custom fields live in `items.extra`, validated against that schema
on every write.
"""

import json
import os
import sqlite3

import config
from tools.collections import _fields
from tools.collections._fields import FieldError

DB_PATH = os.path.join(config.DATA_DIR, "collections", "collections.sqlite")
COLLECTIONS_RO_URI = f"file:{DB_PATH}?mode=ro"

DEFAULT_STATUS = {"states": ["open", "done"], "closed": ["done"]}
NOTES_PREVIEW = 150


class CollectionsError(Exception):
    """A refusal phrased for the model: what was wrong and, where the fix is a
    different argument, what the valid ones are."""


class CollectionsNotFound(CollectionsError):
    """The addressed collection or item does not exist."""


def _get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Per connection, not per database: without it ON DELETE CASCADE is inert.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_db():
    conn = _get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS collections (
            collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL COLLATE NOCASE UNIQUE,
            schema        TEXT NOT NULL,
            archived_at   DATETIME,
            created_at    DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at    DATETIME NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS items (
            item_id       INTEGER PRIMARY KEY AUTOINCREMENT,
            collection_id INTEGER NOT NULL
                              REFERENCES collections(collection_id) ON DELETE CASCADE,
            title         TEXT NOT NULL,
            notes         TEXT,
            section       TEXT,
            status        TEXT,
            extra         TEXT NOT NULL DEFAULT '{}',
            position      INTEGER NOT NULL,
            created_at    DATETIME NOT NULL DEFAULT (datetime('now')),
            updated_at    DATETIME NOT NULL DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS items_by_collection
            ON items(collection_id, position);
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def _schema(row: sqlite3.Row) -> dict:
    return json.loads(row["schema"])


def _field_map(schema: dict) -> dict[str, dict]:
    return {f["name"]: f for f in schema["fields"]}


def _check_status(states, closed) -> dict:
    s = [str(x).strip().lower() for x in (states or []) if str(x).strip()]
    c = [str(x).strip().lower() for x in (closed or []) if str(x).strip()]
    if len(s) < 2:
        raise CollectionsError("A status needs at least two states.")
    if len(set(s)) != len(s):
        raise CollectionsError(f"Status states repeat: {s}.")
    stray = [x for x in c if x not in s]
    if stray:
        raise CollectionsError(f"closed_statuses {stray} are not among the states {s}.")
    if all(x in c for x in s):
        raise CollectionsError("At least one status state must be open (not closed).")
    return {"states": s, "closed": [x for x in s if x in c]}


def _default_state(schema: dict) -> str | None:
    st = schema["status"]
    if not st:
        return None
    return next(x for x in st["states"] if x not in st["closed"])


def _open_states(schema: dict) -> list[str]:
    st = schema["status"]
    return [x for x in st["states"] if x not in st["closed"]] if st else []


def _schema_lines(schema: dict) -> str:
    st = schema["status"]
    if st:
        states = ", ".join(f"{x} (closed)" if x in st["closed"] else x for x in st["states"])
        status = f"status: {states}"
    else:
        status = "no status"
    fields = ", ".join(_fields.describe(f) for f in schema["fields"]) or "none"
    sections = "sections on" if schema["sections"] else "no sections"
    return f"{status}; {sections}; fields: {fields}"


# ---------------------------------------------------------------------------
# Collections
# ---------------------------------------------------------------------------


def _collection_lines(conn: sqlite3.Connection, include_archived: bool = True) -> str:
    rows = conn.execute("SELECT * FROM collections ORDER BY archived_at IS NOT NULL, name").fetchall()
    out = []
    for r in rows:
        if r["archived_at"] and not include_archived:
            continue
        schema = _schema(r)
        total = conn.execute(
            "SELECT COUNT(*) FROM items WHERE collection_id = ?", (r["collection_id"],)
        ).fetchone()[0]
        if schema["status"]:
            opens = _open_states(schema)
            n_open = conn.execute(
                f"SELECT COUNT(*) FROM items WHERE collection_id = ? "
                f"AND status IN ({','.join('?' * len(opens))})",
                (r["collection_id"], *opens),
            ).fetchone()[0]
            count = f"{n_open} open / {total} items"
        else:
            count = f"{total} items"
        mark = "  [archived]" if r["archived_at"] else ""
        out.append(f"- {r['name']}{mark} — {count}\n    {_schema_lines(schema)}")
    return "\n".join(out) if out else "(no collections yet)"


def _names(conn: sqlite3.Connection) -> str:
    rows = conn.execute("SELECT name, archived_at FROM collections ORDER BY name").fetchall()
    if not rows:
        return "(no collections yet)"
    return ", ".join(r["name"] + (" [archived]" if r["archived_at"] else "") for r in rows)


def _require_collection(conn: sqlite3.Connection, name: str) -> sqlite3.Row:
    name = (name or "").strip()
    if not name:
        raise CollectionsError(f"A collection name is required. Collections: {_names(conn)}")
    row = conn.execute("SELECT * FROM collections WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise CollectionsNotFound(
            f"No collection {name!r}. Collections: {_names(conn)}. "
            "Use one of these exact names, or create the collection first."
        )
    return row


def _require_writable(row: sqlite3.Row) -> None:
    if row["archived_at"]:
        raise CollectionsError(
            f"{row['name']!r} is archived and read-only. Unarchive it first "
            "(manage_collection action='unarchive')."
        )


def _touch(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute(
        "UPDATE collections SET updated_at = datetime('now') WHERE collection_id = ?",
        (collection_id,),
    )


def _fields_in_use(conn: sqlite3.Connection, exclude_name: str = "") -> str:
    """Field names and types other collections already use — shown on create and
    edit so a new schema reuses `rating` instead of inventing `score`."""
    seen: dict[str, list[str]] = {}
    for r in conn.execute("SELECT name, schema FROM collections ORDER BY name"):
        if r["name"].lower() == exclude_name.lower():
            continue
        for f in json.loads(r["schema"])["fields"]:
            seen.setdefault(_fields.describe(f), []).append(r["name"])
    if not seen:
        return ""
    return "Fields in use elsewhere: " + "; ".join(
        f"{d} in {', '.join(names)}" for d, names in sorted(seen.items())
    )


def _tags_in(conn: sqlite3.Connection, collection_id: int, schema: dict) -> str:
    tag_fields = [f["name"] for f in schema["fields"] if f["type"] == "tags"]
    parts = []
    for name in tag_fields:
        tags = [r[0] for r in conn.execute(
            "SELECT DISTINCT j.value FROM items, json_each(items.extra, ?) AS j "
            "WHERE items.collection_id = ? ORDER BY j.value",
            (f"$.{name}", collection_id),
        )]
        if tags:
            parts.append(f"{name} in use: {', '.join(tags)}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------

_ITEM_KEYS = {"title", "notes", "section", "status", "fields"}


def _check_item(schema: dict, item) -> dict:
    """One model-supplied item -> the row values to store. Refuses rather than
    drops anything it can't place, and the refusal carries the schema."""
    if not isinstance(item, dict):
        raise CollectionsError(f"An item is {{title, notes?, section?, status?, fields?}}, got {item!r}.")
    fmap = _field_map(schema)
    unknown = set(item) - _ITEM_KEYS
    if unknown:
        stray = sorted(unknown & set(fmap))
        hint = f" Custom fields go inside 'fields': {stray}." if stray else ""
        raise CollectionsError(
            f"Unknown item key(s) {sorted(unknown)}; an item has "
            f"{sorted(_ITEM_KEYS)}.{hint}"
        )
    title = str(item.get("title") or "").strip()
    if not title:
        raise CollectionsError("Every item needs a title.")
    out = {
        "title": title,
        "notes": str(item.get("notes") or "").strip() or None,
        "section": _check_section(schema, item.get("section")),
        "status": _check_state(schema, item.get("status")) or _default_state(schema),
        "extra": _check_values(schema, item.get("fields") or {}, {}),
    }
    return out


def _check_section(schema: dict, section) -> str | None:
    s = str(section or "").strip()
    if s and not schema["sections"]:
        raise CollectionsError("This collection has no sections; leave section empty.")
    return s or None


def _check_state(schema: dict, status) -> str | None:
    s = str(status or "").strip().lower()
    if not s:
        return None
    st = schema["status"]
    if not st:
        raise CollectionsError("This collection has no status; leave status empty.")
    if s not in st["states"]:
        raise CollectionsError(f"Unknown status {status!r}. States: {', '.join(st['states'])}.")
    return s


def _check_values(schema: dict, values, extra: dict) -> dict:
    """Merge `values` into a copy of `extra`; a None/empty value clears."""
    if not isinstance(values, dict):
        raise CollectionsError(f"'fields' is an object of {{field_name: value}}, got {values!r}.")
    fmap = _field_map(schema)
    out = dict(extra)
    for raw_name, value in values.items():
        name = str(raw_name).strip().lower()
        if name not in fmap:
            have = ", ".join(_fields.describe(f) for f in schema["fields"]) or "none"
            raise CollectionsError(
                f"No field {raw_name!r} in this collection. Its fields: {have}. "
                "Put anything else in notes, or ask the owner about adding a field."
            )
        try:
            v = _fields.normalize(fmap[name], value)
        except FieldError as e:
            raise CollectionsError(str(e))
        if v is None:
            out.pop(name, None)
        else:
            out[name] = v
    return out


def _item_line(row: sqlite3.Row, schema: dict, full: bool = False) -> str:
    status = f" [{row['status']}]" if row["status"] else ""
    extra = json.loads(row["extra"])
    vals = [
        f"{f['name']}: {_fields.render(f, extra[f['name']])}"
        for f in schema["fields"] if f["name"] in extra
    ]
    line = f"#{row['item_id']}{status} {row['title']}"
    if vals:
        line += " — " + "; ".join(vals)
    notes = row["notes"]
    if notes:
        if not full and len(notes) > NOTES_PREVIEW:
            notes = notes[:NOTES_PREVIEW].rstrip() + f"… (cut; list_items item_id={row['item_id']} for all)"
        line += "\n    notes: " + notes.replace("\n", "\n    ")
    return line


def _insert_items(conn: sqlite3.Connection, collection_id: int, rows: list[dict]) -> list[int]:
    """Append checked rows at the end of the collection; returns their ids."""
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), 0) FROM items WHERE collection_id = ?", (collection_id,)
    ).fetchone()[0]
    ids = []
    for r in rows:
        pos += 1
        cur = conn.execute(
            "INSERT INTO items (collection_id, title, notes, section, status, extra, position) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (collection_id, r["title"], r["notes"], r["section"], r["status"],
             json.dumps(r["extra"], ensure_ascii=False), pos),
        )
        ids.append(cur.lastrowid)
    _touch(conn, collection_id)
    return ids


def _check_items(schema: dict, items) -> list[dict]:
    if items is not None and not isinstance(items, list):
        raise CollectionsError("items is a list of {title, notes?, section?, status?, fields?}.")
    rows = []
    for i, it in enumerate(items or [], 1):
        try:
            rows.append(_check_item(schema, it))
        except CollectionsError as e:
            raise CollectionsError(f"Item {i}: {e} Nothing was written.")
    return rows


def _require_item(conn: sqlite3.Connection, item_id) -> tuple[sqlite3.Row, sqlite3.Row]:
    try:
        iid = int(item_id)
    except (TypeError, ValueError):
        raise CollectionsError(f"item_id must be a number, got {item_id!r}.")
    item = conn.execute("SELECT * FROM items WHERE item_id = ?", (iid,)).fetchone()
    if item is None:
        raise CollectionsNotFound(f"No item #{iid}. List the collection to find the right id.")
    coll = conn.execute(
        "SELECT * FROM collections WHERE collection_id = ?", (item["collection_id"],)
    ).fetchone()
    return item, coll


# At import, so the read-only URI the app opens never meets a missing file.
_init_db()
