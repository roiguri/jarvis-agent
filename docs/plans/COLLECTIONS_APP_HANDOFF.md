# Handoff — Collections app (`roiguri/jarvis-app`)

**For:** the Android client. **Agent side:** `gateway/apps/collections.py` in
`roiguri/jarvis-agent` (C3 of [COLLECTIONS_PLAN.md](COLLECTIONS_PLAN.md)). **The samples below are
real responses** from that module against realistic data, trimmed. If this document and the payload
disagree, the payload is right.

This supersedes the contract section of the earlier mock-UI prompt: `set_status` became the general
`update_item`, and `quick_add` takes an optional `section`.

---

## 1. Wiring

Same as Travel: one `AppCatalog.kt` entry for namespace `collections` (an icon, a tagline, a
footer, a screen). A tile is drawn only when the agent declares the namespace **and** the catalog
has an entry.

| Entry | Method | Params | Returns |
|---|---|---|---|
| `home` | GET | — | `{collections: [Summary]}` |
| `collection` | GET | `collection_id` | `{collection: Summary + schema, items: [Item]}` |
| `update_item` | POST | `item_id`, `changes` | `{item: Item}` |
| `quick_add` | POST | `collection_id`, `title`, `section`? | `{item: Item}` |
| `delete_item` | POST | `item_id` | `{deleted: <item_id>}` |

GETs go through `AppQueryClient` as Travel's does. The three POSTs are the **first app write
path**: they need an `AppQueryClient.post()` for `POST /v1/apps/collections/q/{entry_id}` (the hub
already relays it). All params are strings on the wire.

**Errors:** `not_found` — the collection or item doesn't exist (e.g. deleted from chat while the
screen was open; refetch). `invalid_request` — the value can't be applied: an unknown status, a
malformed URL, a write to an archived collection, malformed `changes`. Its message is readable; show
it as a transient error. Anything else is an internal fault.

---

## 2. Payloads

### `home`

```json
{"collections": [
  {"collection_id": 3, "name": "Gift Ideas", "archived": false, "has_status": false,
   "open_count": null, "item_count": 1, "updated_at": "2026-10-04T22:05:40Z"},
  {"collection_id": 1, "name": "Shopping", "archived": false, "has_status": true,
   "open_count": 1, "item_count": 2, "updated_at": "2026-10-04T22:05:40Z"}
]}
```

Sorted by name. Archived collections are included with `archived: true`; the client hides them
behind a toggle. `open_count` is `null` exactly when `has_status` is false.

### `collection`

```json
{"collection": {
   "collection_id": 1, "name": "Shopping", "archived": false, "has_status": true,
   "open_count": 1, "item_count": 2, "updated_at": "2026-10-04T22:05:40Z",
   "schema": {
     "fields": [{"name": "url", "type": "url"}, {"name": "price", "type": "money"}],
     "status": {"states": ["considering", "bought", "dropped"], "closed": ["bought", "dropped"]},
     "sections": true}},
 "items": [
   {"item_id": 1, "title": "כורסה (Armchair) - Patton \"Suits\"",
    "notes": "Color: Olive Green (ירוק-זית)\nMeasurements: 74.5W x 77D x 74.5H cm",
    "section": "Furniture & Home", "status": "considering", "closed": false,
    "fields": {"url": "https://patton.co.il/products/suits",
               "price": {"amount": 3400, "currency": "ILS"}},
    "position": 1, "created_at": "2026-10-04T22:05:40Z", "updated_at": "2026-10-04T22:05:40Z"},
   {"item_id": 2, "title": "Desk lamp", "notes": null, "section": null,
    "status": "bought", "closed": true,
    "fields": {"price": {"amount": 45, "currency": "USD"}}, "…": "…"}
 ]}
```

A collection without status (a reference list):

```json
"schema": {
  "fields": [{"name": "rating", "type": "rating"},
             {"name": "colour", "type": "choice", "options": ["red", "white", "rosé", "orange"]},
             {"name": "tags", "type": "tags"},
             {"name": "tasted_on", "type": "date"}],
  "status": null, "sections": false}
…
{"item_id": 3, "title": "2019 Rioja Reserva", "notes": null, "section": null,
 "status": null, "closed": null,
 "fields": {"rating": 4, "colour": "red", "tags": ["spanish", "oak"], "tasted_on": "2026-10-01"}}
```

**Rules the client can rely on:**
- `schema.status` is `null` for a collection without status, and then every item's `status` and
  `closed` are `null`. Otherwise `closed` is precomputed; never derive it.
- A status may have **no** closed states (`"closed": []`). Then there is nothing to hide: no tabs.
- `items` arrive in **display order**: by section (unsectioned last), then position. Group
  consecutive items under one heading; don't re-sort. `section` is `null` when sections are off.
- `fields` holds only fields that have a value; a missing key means empty. Values by type:

| Type | JSON | Render |
|---|---|---|
| `text` | string | wrapping text |
| `url` | string, http(s) | domain chip → opens the browser |
| `number` | number | as-is (units live in the field name, e.g. `weight_kg`) |
| `money` | `{amount, currency}` | `₪3,400`, `$45`, formatted by currency code |
| `date` | `"YYYY-MM-DD"` | `1 Oct` |
| `rating` | integer 1–5 | ★★★★☆ |
| `choice` | one of `options` | small label |
| `tags` | list of lowercase strings | chips (+ a filter row) |

- Timestamps are UTC ISO 8601 with `Z`.

### `update_item`

`changes` is a **JSON object in a string**: the only way typed values cross a string-only param.
Keys: `title`, `notes`, `section`, `status` (strings), `fields` (`{name: value}`). Send only what
changes; `""` clears notes, section or a field value (a title can't be cleared). Values are
validated exactly as chat validates them: money accepts `3400`, `"45 USD"` or
`{"amount": 45, "currency": "USD"}`, and tags accept a list or a comma-separated string.

```
POST update_item  item_id=1  changes={"status":"bought"}
→ {"item": {"item_id": 1, "status": "bought", "closed": true, "…": "…"}}
```

v1 of the screen sends only `{"status": …}` (checkbox / status buttons). The rest is there so
in-app editing can come later without an agent change.

### `quick_add`

Title only, plus `section` when the user adds from inside a section group. Leave `section` out for
a collection without sections, or it's refused. Status defaults to the first open state.

### `delete_item`

Returns `{"deleted": <item_id>}`.

---

## 3. Screens (plan §5)

- **Home:** one row per collection: name, open count (only when `has_status`), last updated.
  Archived behind a toggle, read-only (no quick actions inside).
- **Collection:** one generic screen driven by `schema`, no per-collection screens. Open / Closed
  tabs only if status is on **and** `closed` is non-empty; tag chips only if a `tags` field exists;
  section headings only if `sections`; rows show only the schema's fields. Default open/done
  collections get a checkbox; custom states show a label. Quick-add input at the bottom.
- **Detail sheet:** read-only fields and full notes, one button per status state (current
  highlighted), Delete with a lightweight confirm.

## 4. Stays in chat

Creating, archiving or deleting a collection, and any schema change: those need the confirmation
plane, which lives in the conversation. Rich adds ("add this link" → fetch + summary) need the model.
