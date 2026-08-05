---
name: travel
description: trips, saved places, per-trip wishlists, and hourly itineraries
---
- Address a trip by its `trip_id`, never by destination name. If you don't know it, list the trips and pick from what comes back — never guess an id.
- Scheduling a place does not remove it from the wishlist, and the same place may be scheduled on more than one day. Never treat scheduling as moving something out of the wishlist.
- Times are local wall-clock at the destination and are never converted. A trip's `timezone` decides only which date counts as "today".
- A trip with no dates is a someday bucket: collect wishlist places for it, and say that dates are needed before anything can be scheduled.
