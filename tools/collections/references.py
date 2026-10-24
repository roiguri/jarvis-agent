"""The file ids collection items hold, for the file store's where-used answers."""

from __future__ import annotations

import json
import os
from contextlib import closing

from files import references
from tools.collections import _db


@references.holder("collections", strength="strong")
def collection_files() -> list[str]:
    if not os.path.exists(_db.DB_PATH):
        return []
    found = []
    with closing(_db._get_db()) as conn:
        for coll in conn.execute("SELECT collection_id, schema FROM collections").fetchall():
            names = [f["name"] for f in json.loads(coll["schema"])["fields"] if f["type"] == "file"]
            if not names:
                continue
            for item in conn.execute("SELECT extra FROM items WHERE collection_id = ?",
                                     (coll["collection_id"],)):
                extra = json.loads(item["extra"] or "{}")
                found += [extra[n] for n in names if extra.get(n)]
    return found
