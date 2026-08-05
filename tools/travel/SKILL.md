---
name: travel
description: trips, saved places, per-trip wishlists, and hourly itineraries
---
- Address a trip by its `trip_id`, never by destination name. If you don't know it, list the trips and pick from what comes back — never guess an id.
- Scheduling a place does not remove it from the wishlist, and the same place may be scheduled on more than one day. Never treat scheduling as moving something out of the wishlist.
- Times are local wall-clock at the destination and are never converted. A trip's `timezone` decides only which date counts as "today".
- A trip with no dates is a someday bucket: collect wishlist places for it, and say that dates are needed before anything can be scheduled.
- Never change a trip's dates as a side effect of adding or moving an item. If something doesn't fit the trip's window, schedule it anyway — it gets flagged as an edge day — and tell the owner it falls outside. Widen the trip only when they say the trip itself moved.
- Whenever something is actually booked, record its confirmation code. That code is the only thing that keeps the item where it is when the trip's dates move — anything without one is treated as an intention and shifts with the plan. If the owner mentions a booking without giving a reference, ask for it.
