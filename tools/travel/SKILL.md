---
name: travel
description: trips, saved places, per-trip wishlists, and hourly itineraries
---
- Address a trip by its `trip_id` and a destination by its exact name. If you don't know either, list them and pick from what comes back — never guess, and never invent a second spelling of a destination that already exists.
- Scheduling a place does not remove it from the wishlist, and the same place may be scheduled on more than one day. Never treat scheduling as moving something out of the wishlist.
- Times are local wall-clock and are never converted. The destination carries the timezone: it decides which date counts as "today", and it is what makes a journey's duration computable.
- Prefer a country as the destination when the trip is to one country: a Portugal destination holds both the Lisbon and the Porto places, and country names barely vary in spelling where city names do (Lisbon/Lisboa, and Google says Lisboa). Use a city or a region instead when the country spans several timezones — the US, Brazil, Australia, and Portugal's own Azores — since the destination is what carries the timezone. Either way, name the trip itself after the city with `title` if that is how the owner talks about it.
- Never invent a detail that was not given — a date, a time, an address, a confirmation code — **even when a tool requires it**. Stop and ask. "August" is not a date range, and a flight's date is not the trip's end date.
- A trip with no dates is a someday bucket: collect wishlist places for it, and say that dates are needed before anything can be scheduled.
- Never change a trip's dates as a side effect of adding or moving an item. If something doesn't fit the trip's window, schedule it anyway — it gets flagged as an edge day — and tell the owner it falls outside. Widen the trip only when they say the trip itself moved.
- A flight or train's arrival time is local to where it lands, exactly as a schedule prints it — a 22:00 departure arriving 06:00 is an overnight, not an error. When the journey crosses timezones, give `arrival_date` and both `departure_timezone` and `arrival_timezone`: the arrival date cannot be read off the clocks, since an arrival earlier than its departure may be the same day or two days later. Within one timezone an overnight is worked out for you.
- Whenever something is actually booked, record its confirmation code so it can be shown on the item. If the owner mentions a booking without giving a reference, ask for it.
- Changing a trip's dates does not move anything already scheduled. Say which items now fall outside the new window, and ask what should happen to them rather than re-dating them.
