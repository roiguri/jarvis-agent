"""Travel skill — trips, saved places, per-trip wishlists, dated itineraries.

One module per table, so a tool and the rows it owns stay together; `_db.py`
holds the schema and the helpers they share. Importing this package imports each
module, running its ``@tool_register`` side-effects.
"""

from tools.travel.itinerary import manage_itinerary  # noqa: F401
from tools.travel.places import manage_place  # noqa: F401
from tools.travel.query import query_travel_db  # noqa: F401
from tools.travel.trips import manage_trip  # noqa: F401
from tools.travel.wishlist import manage_wishlist  # noqa: F401

__all__ = [
    "manage_itinerary",
    "manage_place",
    "manage_trip",
    "manage_wishlist",
    "query_travel_db",
]
