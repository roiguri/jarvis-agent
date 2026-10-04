"""manage_collection: creating collections, editing their schemas, archiving, deleting.

Shapes the agent invents are gated (§ confirmation rules): custom fields, custom
status states, any schema edit, and delete wait for the owner's tap. A gated
create carries its first items, so the list exists complete on Confirm.
"""

import asyncio
import json
import sqlite3

from langchain_core.tools import tool

from tools.collections import _fields
from tools.collections._db import (
    DEFAULT_STATUS,
    CollectionsError,
    _check_items,
    _check_status,
    _collection_lines,
    _default_state,
    _fields_in_use,
    _get_db,
    _insert_items,
    _require_collection,
    _require_writable,
    _schema,
    _schema_lines,
)
from tools.collections._fields import FieldError
from tools.registry import tool_register

_ACTIONS = ("list", "create", "edit_schema", "archive", "unarchive", "delete")
_PROMPT_ITEMS = 10


@tool_register(namespace="collections", destructive=True)
@tool
def manage_collection(
    action: str,
    name: str = "",
    fields: list[dict] | None = None,
    status: bool | None = None,
    statuses: list[str] | None = None,
    closed_statuses: list[str] | None = None,
    sections: bool | None = None,
    items: list[dict] | None = None,
    rename_fields: dict | None = None,
    retype_fields: dict | None = None,
    remove_fields: list[str] | None = None,
    status_map: dict | None = None,
) -> str:
    """Create and manage collections — structured lists such as reading, shopping
    or books. A collection's schema is its optional status, its optional sections,
    and its custom fields.

    Actions:
    - list: every collection with its schema and item counts. Call this first
      whenever you don't know the exact name.
    - create: needs name. With no schema arguments it is a plain list of titles
      and notes. status=true gives the default open/done lifecycle; sections=true
      lets items carry a heading. These need no confirmation. Custom `fields` or
      custom `statuses` ask the owner to confirm, and the collection (with any
      `items` given here) is created only when they tap Confirm.
    - edit_schema: change an existing collection's schema. Always asks the owner
      to confirm and states which items lose or change values. `fields` adds
      fields; rename_fields / retype_fields / remove_fields change existing ones;
      statuses / closed_statuses replace the states (status_map moves items out of
      removed states); status=false or sections=false removes them and clears
      those values; status=true or sections=true turns them on.
    - archive / unarchive: an archived collection is kept but read-only.
    - delete: permanently remove the collection and every item. Asks to confirm.
      Prefer archive.

    Args:
        action: list | create | edit_schema | archive | unarchive | delete
        name: the collection's exact name (required except for list).
        fields: custom fields, each {name, type, options?}. type is one of text,
            url, number, money, date, rating (1-5), choice (needs options), tags.
            Names are snake_case. A number field carries its unit in the name
            (weight_kg); money stores a currency per value, so 'price', never
            'price_ils'. On create: the fields; on edit_schema: fields to add.
        status: true = the item has a lifecycle (default states open / done);
            false = none (a reference list).
        statuses: custom states in order, e.g. ["unread", "read", "dropped"].
        closed_statuses: which states are finished and hidden by default,
            e.g. ["read", "dropped"].
        sections: true = items carry a section heading (topic, city, ...).
        items: create only — first items, each {title, notes?, section?,
            status?, fields?: {field_name: value}}.
        rename_fields: edit_schema — {old_name: new_name}.
        retype_fields: edit_schema — {name: type}, or {name: {type, options}}
            for a choice. Values that don't convert are cleared.
        remove_fields: edit_schema — field names to remove with their values.
        status_map: edit_schema with statuses — {removed_state: new_state} for
            items in a state the new list drops.
    """
    action = (action or "").strip().lower()
    if action not in _ACTIONS:
        return f"Error: Unknown action {action!r}. Use one of: {', '.join(_ACTIONS)}."
    conn = _get_db()
    try:
        try:
            if action == "list":
                return _collection_lines(conn)
            if action == "create":
                return _create(conn, name, fields, status, statuses, closed_statuses,
                               sections, items)
            coll = _require_collection(conn, name)
            if action == "archive":
                if coll["archived_at"]:
                    return f"{coll['name']} is already archived."
                conn.execute("UPDATE collections SET archived_at = datetime('now') "
                             "WHERE collection_id = ?", (coll["collection_id"],))
                conn.commit()
                return f"Archived {coll['name']} — kept, read-only, hidden from the main list."
            if action == "unarchive":
                if not coll["archived_at"]:
                    return f"{coll['name']} is not archived."
                conn.execute("UPDATE collections SET archived_at = NULL "
                             "WHERE collection_id = ?", (coll["collection_id"],))
                conn.commit()
                return f"Unarchived {coll['name']}."
            if action == "delete":
                return _delete(conn, coll)
            if action == "edit_schema":
                edit = dict(fields=fields, status=status, statuses=statuses,
                            closed=closed_statuses, sections=sections, rename=rename_fields,
                            retype=retype_fields, remove=remove_fields, status_map=status_map)
                return _edit(conn, coll, edit)
        except CollectionsError as e:
            return f"Error: {e}"
    finally:
        conn.close()


def _request(description: str, action, ok: str, cancel: str) -> str:
    from gateway.factory import get_confirmation

    async def _run() -> str:
        return await asyncio.to_thread(action)

    try:
        return get_confirmation().request_confirmation_sync(
            description=description, action_fn=_run,
            result_ok_text=ok, result_cancel_text=cancel,
        )
    except Exception as e:
        return f"Error requesting confirmation: {e}"


def _with_conn(fn):
    """Run fn on a fresh connection: confirmed actions run later, on another
    thread, after the requesting connection is closed."""
    conn = _get_db()
    try:
        return fn(conn)
    except CollectionsError as e:
        return f"Nothing changed: {e}"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


def _create(conn, name, fields, status, statuses, closed, sections, items) -> str:
    name = (name or "").strip()
    if not name:
        raise CollectionsError("create needs a name.")
    if len(name) > 60:
        raise CollectionsError("Keep the collection name under 60 characters.")
    _require_free(conn, name)

    try:
        defs = _fields.check_defs(fields)
    except FieldError as e:
        raise CollectionsError(str(e))
    if statuses or closed:
        if status is False:
            raise CollectionsError("status=false contradicts statuses.")
        st = _check_status(statuses or DEFAULT_STATUS["states"], closed)
    elif status:
        st = DEFAULT_STATUS
    else:
        st = None
    schema = {"fields": defs, "status": st, "sections": bool(sections)}
    gated = bool(defs) or (st is not None and st != DEFAULT_STATUS)

    rows = _check_items(schema, items)
    nudge = _fields_in_use(conn, name)

    if not gated:
        return _exec_create(conn, name, schema, rows) + (f"\n{nudge}" if nudge else "")

    desc = [f"Create collection '{name}'", _schema_lines(schema)]
    if rows:
        titles = [f"• {r['title']}" for r in rows[:_PROMPT_ITEMS]]
        if len(rows) > _PROMPT_ITEMS:
            titles.append(f"…and {len(rows) - _PROMPT_ITEMS} more")
        desc.append(f"With {len(rows)} item(s):\n" + "\n".join(titles))
    result = _request(
        "\n\n".join(desc),
        lambda: _with_conn(lambda c: _exec_create(c, name, schema, rows)),
        ok=f"Collection '{name}' created.",
        cancel=f"Creation of '{name}' cancelled — nothing was written.",
    )
    return result + (f"\n{nudge}" if nudge else "")


def _require_free(conn: sqlite3.Connection, name: str) -> None:
    row = conn.execute("SELECT name FROM collections WHERE name = ?", (name,)).fetchone()
    if row:
        raise CollectionsError(f"A collection named {row['name']!r} already exists.")


def _exec_create(conn, name: str, schema: dict, rows: list[dict]) -> str:
    _require_free(conn, name)
    cur = conn.execute("INSERT INTO collections (name, schema) VALUES (?, ?)",
                       (name, json.dumps(schema, ensure_ascii=False)))
    ids = _insert_items(conn, cur.lastrowid, rows)
    conn.commit()
    added = f" with {len(ids)} item(s) (#{ids[0]}–#{ids[-1]})" if ids else ""
    return f"Created collection {name}{added}. {_schema_lines(schema)}"


# ---------------------------------------------------------------------------
# edit_schema
# ---------------------------------------------------------------------------


def _edit(conn, coll, edit: dict) -> str:
    _require_writable(coll)
    _, _, changes, effects = _plan_edit(conn, coll, edit)
    name = coll["name"]
    desc = f"Change the schema of '{name}':\n" + "\n".join(f"• {c}" for c in changes)
    if effects:
        desc += "\n\nEffect on items:\n" + "\n".join(f"• {e}" for e in effects)
    result = _request(
        desc,
        lambda: _with_conn(lambda c: _exec_edit(c, name, edit)),
        ok=f"Schema of '{name}' updated.",
        cancel=f"Schema change to '{name}' cancelled — nothing changed.",
    )
    nudge = _fields_in_use(conn, name)
    return result + (f"\n{nudge}" if nudge else "")


def _exec_edit(conn, name: str, edit: dict) -> str:
    """Re-plans against the rows as they are at tap time, so items added while
    the prompt was open are migrated too."""
    coll = _require_collection(conn, name)
    _require_writable(coll)
    schema, updates, changes, effects = _plan_edit(conn, coll, edit)
    conn.execute("UPDATE collections SET schema = ?, updated_at = datetime('now') "
                 "WHERE collection_id = ?",
                 (json.dumps(schema, ensure_ascii=False), coll["collection_id"]))
    for item_id, st, section, extra in updates:
        conn.execute("UPDATE items SET status = ?, section = ?, extra = ?, "
                     "updated_at = datetime('now') WHERE item_id = ?",
                     (st, section, json.dumps(extra, ensure_ascii=False), item_id))
    conn.commit()
    tail = f" Items: {'; '.join(effects)}." if effects else ""
    return f"Updated {name}: {'; '.join(changes)}.{tail} Now: {_schema_lines(schema)}"


def _plan_edit(conn, coll, edit: dict):
    """(new schema, item updates, change lines, effect lines). Pure: writes nothing."""
    old = _schema(coll)
    new_fields = [dict(f) for f in old["fields"]]
    rows = conn.execute("SELECT item_id, status, section, extra FROM items "
                        "WHERE collection_id = ?", (coll["collection_id"],)).fetchall()
    state = {r["item_id"]: [r["status"], r["section"], json.loads(r["extra"])] for r in rows}
    before = {k: [v[0], v[1], dict(v[2])] for k, v in state.items()}
    changes: list[str] = []
    effects: list[str] = []

    def names() -> list[str]:
        return [f["name"] for f in new_fields]

    def existing(raw) -> str:
        n = str(raw or "").strip().lower()
        if n not in names():
            raise CollectionsError(f"No field {raw!r}. Fields: {', '.join(names()) or 'none'}.")
        return n

    def holding(n) -> int:
        return sum(1 for s in state.values() if n in s[2])

    try:
        for raw in edit["remove"] or []:
            n = existing(raw)
            k = holding(n)
            new_fields = [f for f in new_fields if f["name"] != n]
            for s in state.values():
                s[2].pop(n, None)
            changes.append(f"remove field {n}")
            if k:
                effects.append(f"{k} item(s) lose their {n} value")

        for raw_old, raw_new in (edit["rename"] or {}).items():
            o = existing(raw_old)
            nn = _fields.check_name(raw_new)
            if nn in names():
                raise CollectionsError(f"Can't rename {o} to {nn}: {nn} already exists.")
            for f in new_fields:
                if f["name"] == o:
                    f["name"] = nn
            for s in state.values():
                if o in s[2]:
                    s[2][nn] = s[2].pop(o)
            changes.append(f"rename field {o} → {nn}")

        for raw, spec in (edit["retype"] or {}).items():
            n = existing(raw)
            if isinstance(spec, dict):
                t, opts = spec.get("type"), spec.get("options")
            else:
                t, opts = spec, None
            new_def = _fields.check_def(n, t, opts)
            old_def = next(f for f in new_fields if f["name"] == n)
            new_fields = [new_def if f["name"] == n else f for f in new_fields]
            kept = lost = 0
            for s in state.values():
                if n not in s[2]:
                    continue
                try:
                    s[2][n] = _fields.normalize(new_def, _as_input(old_def, new_def, s[2][n]))
                    kept += 1
                except FieldError:
                    s[2].pop(n)
                    lost += 1
            changes.append(f"change {_fields.describe(old_def)} → {_fields.describe(new_def)}")
            if kept or lost:
                effects.append(f"{n}: {kept} value(s) converted, {lost} cleared")

        added = _fields.check_defs(edit["fields"])
        for d in added:
            if d["name"] in names():
                raise CollectionsError(f"Field {d['name']} already exists; use retype_fields "
                                       "or rename_fields to change it.")
            new_fields.append(d)
            changes.append(f"add field {_fields.describe(d)}")
    except FieldError as e:
        raise CollectionsError(str(e))

    new_status = _plan_status(old["status"], edit, state, changes, effects)
    new_sections = old["sections"]
    if edit["sections"] is False and old["sections"]:
        k = sum(1 for s in state.values() if s[1])
        for s in state.values():
            s[1] = None
        new_sections = False
        changes.append("turn sections off")
        if k:
            effects.append(f"{k} item(s) lose their section")
    elif edit["sections"] is True and not old["sections"]:
        new_sections = True
        changes.append("turn sections on")

    if not changes:
        raise CollectionsError("Nothing to change — pass the schema change to make. "
                               f"Current schema: {_schema_lines(old)}")
    schema = {"fields": new_fields, "status": new_status, "sections": new_sections}
    updates = [(k, *v) for k, v in state.items() if v != before[k]]
    return schema, updates, changes, effects


def _plan_status(old_st, edit, state, changes, effects):
    statuses, closed, smap = edit["statuses"], edit["closed"], edit["status_map"]
    if smap and not (statuses or closed):
        raise CollectionsError("status_map only applies together with new statuses.")

    if edit["status"] is False:
        if statuses or closed:
            raise CollectionsError("status=false contradicts statuses.")
        if not old_st:
            return None
        k = sum(1 for s in state.values() if s[0])
        for s in state.values():
            s[0] = None
        changes.append("turn status off")
        if k:
            effects.append(f"{k} item(s) lose their status")
        return None

    if statuses or closed:
        base = old_st or DEFAULT_STATUS
        states = [str(x).strip().lower() for x in statuses or base["states"]]
        if closed is None:
            closed = [x for x in base["closed"] if x in states]
        new_st = _check_status(states, closed)
        if new_st == old_st:
            return old_st
        smap = {str(k).strip().lower(): str(v).strip().lower() for k, v in (smap or {}).items()}
        bad = {k: v for k, v in smap.items() if v not in new_st["states"]}
        if bad:
            raise CollectionsError(f"status_map targets {sorted(set(bad.values()))} are not "
                                   f"among the new states {new_st['states']}.")
        default = _default_state({"status": new_st})
        moved: dict[tuple, int] = {}
        stranded: dict[str, int] = {}
        for s in state.values():
            cur = s[0]
            if old_st is None or cur is None:
                target = default
            elif cur in new_st["states"]:
                continue
            elif cur in smap:
                target = smap[cur]
            else:
                stranded[cur] = stranded.get(cur, 0) + 1
                continue
            moved[(cur, target)] = moved.get((cur, target), 0) + 1
            s[0] = target
        if stranded:
            detail = ", ".join(f"{n} in {st!r}" for st, n in stranded.items())
            raise CollectionsError(
                f"Items are in states the new list drops ({detail}). Pass status_map "
                f"{{old_state: new_state}} saying where they go; new states: {new_st['states']}."
            )
        was = _schema_lines({"fields": [], "status": old_st, "sections": False}).split(";")[0]
        now = _schema_lines({"fields": [], "status": new_st, "sections": False}).split(";")[0]
        changes.append(f"{was} → {now}" if old_st else f"turn {now} on")
        for (frm, to), n in moved.items():
            effects.append(f"{n} item(s) set to {to}" if frm is None or old_st is None
                           else f"{n} item(s) move from {frm} to {to}")
        return new_st

    if edit["status"] is True and not old_st:
        for s in state.values():
            s[0] = "open"
        changes.append("turn status on (open / done)")
        if state:
            effects.append(f"{len(state)} item(s) set to open")
        return DEFAULT_STATUS

    return old_st


def _as_input(old_def: dict, new_def: dict, value):
    """A stored value re-expressed so the new type's normaliser can read it."""
    if old_def["type"] == "money" and new_def["type"] == "number":
        return value["amount"]
    if old_def["type"] == "money" and new_def["type"] == "money":
        return value
    if isinstance(value, (dict, list)) and new_def["type"] != old_def["type"]:
        return _fields.render(old_def, value)
    return value


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


def _delete(conn, coll) -> str:
    name, cid = coll["name"], coll["collection_id"]
    n = conn.execute("SELECT COUNT(*) FROM items WHERE collection_id = ?", (cid,)).fetchone()[0]

    def _exec(c) -> str:
        cur = c.execute("DELETE FROM collections WHERE collection_id = ?", (cid,))
        c.commit()
        return f"Deleted {name} and its items." if cur.rowcount else f"{name} was already gone."

    return _request(
        f"Permanently delete collection '{name}' and all {n} of its items "
        "(including closed ones). Archiving would keep them.",
        lambda: _with_conn(_exec),
        ok=f"Collection '{name}' deleted.",
        cancel=f"Deletion of '{name}' cancelled — nothing changed.",
    )
