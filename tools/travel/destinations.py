"""The destinations table: somewhere you travel to, and the timezone it is in.

A destination outlives any one trip. Places belong to it, and — once the wishlist
is re-anchored — so does the list of things you want to do there, which is what
makes returning to a city find what you left rather than an empty page.

That only holds if a destination cannot fork on spelling, which is why `name` is
uniquely indexed case-insensitively and why the model is expected to `list`
before it names one.
"""

import sqlite3
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

from tools.registry import tool_register
from tools.travel._db import TravelError, _get_db

KINDS = ("city", "region", "country")


def _validate_kind(kind: str) -> str | None:
    k = (kind or "").strip().lower()
    if not k:
        return None
    if k not in KINDS:
        raise TravelError(f"Unknown kind {k!r}. Use one of: {', '.join(KINDS)}.")
    return k


def _validate_tz(timezone: str) -> str:
    tz = (timezone or "").strip()
    if not tz:
        raise TravelError(
            "A destination needs a timezone — every local time on the trip is read "
            "in it. Use an IANA name such as 'Europe/Lisbon' or 'Asia/Tokyo'."
        )
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise TravelError(
            f"Unknown timezone {tz!r}. Use an IANA name such as 'Europe/Lisbon'."
        )
    return tz


def _destination_lines(conn: sqlite3.Connection) -> str:
    """Every destination, with what depends on it. Used by `list` and by every
    refusal, so the two cannot drift apart."""
    rows = conn.execute(
        "SELECT d.*, "
        "  (SELECT COUNT(*) FROM places p WHERE p.destination_id = d.destination_id) AS places, "
        "  (SELECT COUNT(*) FROM trips t WHERE t.destination_id = d.destination_id) AS trips "
        "FROM destinations d ORDER BY d.name"
    ).fetchall()
    if not rows:
        return "(no destinations yet)"
    out = []
    for r in rows:
        bits = [b for b in (r["kind"], r["country"], r["timezone"]) if b]
        counts = f"{r['places']} place(s), {r['trips']} trip(s)"
        out.append(f"- [{r['destination_id']}] {r['name']} — {', '.join(bits)}  ({counts})")
    return "\n".join(out)


def _require_destination(conn: sqlite3.Connection, ref: str | int) -> sqlite3.Row:
    """Resolve a destination by id or by exact name (case-insensitive).

    Never fuzzy: an unrecognised name is answered with the real list rather than
    matched to something close, because a wrong match here silently attaches a
    place to a city it is not in.
    """
    if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
        row = conn.execute(
            "SELECT * FROM destinations WHERE destination_id = ?", (int(ref),)
        ).fetchone()
    else:
        name = (ref or "").strip()
        if not name:
            raise TravelError(
                f"A destination is required. Existing:\n{_destination_lines(conn)}"
            )
        row = conn.execute(
            "SELECT * FROM destinations WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
    if row is None:
        raise TravelError(
            f"No destination {ref!r}. Existing:\n{_destination_lines(conn)}\n"
            "Use one of these, or create it with manage_destination."
        )
    return row


@tool_register(namespace="travel", destructive=True)
@tool
def manage_destination(
    action: str,
    name: str = "",
    kind: str = "",
    country: str = "",
    timezone: str = "",
    new_name: str = "",
    into: str = "",
) -> str:
    """Somewhere you travel to — a city or a country — and the timezone it is in.

    A destination is shared by every trip there and by every place saved in it,
    so it is created once and reused. ALWAYS call action='list' before naming a
    new one: a second spelling of a city you already have would split its saved
    places in two.

    Actions:
    - list: every destination, with how many places and trips depend on it.
    - create: needs a name and a timezone. The timezone is not optional — every
      local time on a trip there is read in it.
    - update: change a destination's name, kind, country or timezone. This
      affects every trip and place attached to it, which is the point.
    - merge: fold one destination into another, moving its places and trips
      across and deleting it. The way to recover from a duplicate, since a
      rename would collide with the name that already exists.

    Args:
        action: list | create | update | merge
        name: the destination's name — "Lisbon", "Portugal", "Japan".
        kind: city | region | country. Optional but worth setting.
        country: the country it is in, for a city.
        timezone: IANA name, e.g. "Europe/Lisbon". Required to create.
        new_name: the replacement name, for update.
        into: the destination to merge into, for merge.
    """
    action = (action or "").strip().lower()
    if action not in ("list", "create", "update", "merge"):
        return (
            f"Error: Unknown action {action!r}. Use one of: list, create, update, merge."
        )
    conn = _get_db()
    try:
        try:
            if action == "list":
                return _destination_lines(conn)

            if action == "create":
                nm = (name or "").strip()
                if not nm:
                    raise TravelError("create needs a name.")
                if conn.execute(
                    "SELECT 1 FROM destinations WHERE name = ? COLLATE NOCASE", (nm,)
                ).fetchone():
                    raise TravelError(
                        f"{nm!r} already exists. Existing:\n{_destination_lines(conn)}"
                    )
                tz = _validate_tz(timezone)
                k = _validate_kind(kind)
                cur = conn.execute(
                    "INSERT INTO destinations(name, kind, country, timezone) "
                    "VALUES(?,?,?,?)",
                    (nm, k, country.strip() or None, tz),
                )
                conn.commit()
                return f"Created destination {nm} ({tz}) [id {cur.lastrowid}]."

            dest = _require_destination(conn, name)

            if action == "update":
                sets, args, said = [], [], []
                if new_name.strip():
                    if conn.execute(
                        "SELECT 1 FROM destinations WHERE name = ? COLLATE NOCASE "
                        "AND destination_id != ?",
                        (new_name.strip(), dest["destination_id"]),
                    ).fetchone():
                        raise TravelError(
                            f"{new_name.strip()!r} already exists — merge into it instead "
                            "of renaming onto it."
                        )
                    sets.append("name = ?"); args.append(new_name.strip())
                    said.append(f"name → {new_name.strip()}")
                if timezone.strip():
                    tz = _validate_tz(timezone)
                    sets.append("timezone = ?"); args.append(tz)
                    said.append(f"timezone → {tz}")
                if kind.strip():
                    k = _validate_kind(kind)
                    sets.append("kind = ?"); args.append(k)
                    said.append(f"kind → {k}")
                if country.strip():
                    sets.append("country = ?"); args.append(country.strip())
                    said.append(f"country → {country.strip()}")
                if not sets:
                    return f"Nothing to update on {dest['name']} — pass a field to change."
                conn.execute(
                    f"UPDATE destinations SET {', '.join(sets)} WHERE destination_id = ?",
                    (*args, dest["destination_id"]),
                )
                conn.commit()
                return (
                    f"Updated {dest['name']}: " + "; ".join(said)
                    + ". This applies to every trip and place there."
                )

            if action == "merge":
                target = _require_destination(conn, into)
                if target["destination_id"] == dest["destination_id"]:
                    raise TravelError("A destination cannot be merged into itself.")
                n_places = conn.execute(
                    "UPDATE places SET destination_id = ? WHERE destination_id = ?",
                    (target["destination_id"], dest["destination_id"]),
                ).rowcount
                n_trips = conn.execute(
                    "UPDATE trips SET destination_id = ? WHERE destination_id = ?",
                    (target["destination_id"], dest["destination_id"]),
                ).rowcount
                conn.execute(
                    "DELETE FROM destinations WHERE destination_id = ?",
                    (dest["destination_id"],),
                )
                conn.commit()
                return (
                    f"Merged {dest['name']} into {target['name']}: moved {n_places} "
                    f"place(s) and {n_trips} trip(s). {dest['name']} no longer exists."
                )
        except TravelError as e:
            return f"Error: {e}"
    finally:
        conn.close()
