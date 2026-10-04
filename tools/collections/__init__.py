"""Collections skill — structured lists (reading, shopping, books, ...) whose
schema is set per collection at runtime.

`_fields.py` is the field-type vocabulary, `_db.py` the schema and shared
helpers. Importing this package imports each tool module, running its
``@tool_register`` side-effects.
"""

from tools.collections.collections import manage_collection  # noqa: F401
from tools.collections.items import add_items, delete_item, list_items, update_item  # noqa: F401

__all__ = ["add_items", "delete_item", "list_items", "manage_collection", "update_item"]
