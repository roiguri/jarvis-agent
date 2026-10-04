# Collections — runtime-schema lists, a skill, and a jarvis-app mini-app

**Date:** 2026-10-03 · **Status:** design settled in a grilling session (decisions below);
C1 done and staging-verified; C3 deferred until
the tools have been used for real.
**Sibling plan:** [TASKS_PLAN.md](TASKS_PLAN.md) — todo/tasks are a separate model and ship
after this plan is complete.
**Goal:** replace the markdown lists in memory (reading, shopping, books) with a structured
store that the agent manages through a skill and the owner browses and ticks off in jarvis-app,
while keeping new list shapes cheap to add.

---

## Slices

Each slice ships and is verified on its own. Order is deliberate: collections end-to-end
(tools → migration → app), then the tasks plan.

**C1 — `collections` skill (agent).**
- [x] `tools/collections/_fields.py`: the field-type vocabulary — validation + normalisation
      per type. Private to the skill; whether `tasks` shares it (it needs only `tags`) is
      decided when T1 starts.
- [x] `tools/collections/_db.py`: `jarvis_data/collections/collections.sqlite`, schema below.
- [x] Tools: `manage_collection`, `add_items`, `update_item`, `list_items`, `delete_item`.
- [x] Confirmation for custom-field creation, custom statuses, schema edits, collection delete;
      the pending creation carries the first items.
- [x] `tools/collections/SKILL.md` (description + rules, see §6).
- [x] `tests/tools/test_collections.py` — every gated and ungated path, confirm and cancel.
- [x] Verify on staging **by creating real collections through Jarvis**, which also leaves the
      data C3 is built against: `reading` and `shopping` from staging's own markdown copies,
      plus one collection per path — plain, default status, sections, custom schema (confirm +
      cancel), custom statuses, schema edit (add / rename / remove),
      archive / unarchive, delete (confirm). This doubles as the C2 runbook rehearsal.
      Done 2026-10-04 against copies of the prod list files. Every path above passed, plus
      retype, choice options, status on/off, sections off, and a state list with no closed
      state. The reading rehearsal matched the source 35/35 (title, url, section, read state,
      notes). Left on staging: only `reading`; the other test collections were deleted, so C3
      needs a shopping-shaped collection recreated. C2 note: ask Jarvis to *archive* each
      markdown file — unprompted it deleted `reading_list.md` (with confirmation).

**C2 — Prod migration, by talking to Jarvis (no code, no script).** After C1 is deployed.
- [ ] `reading` — every entry from `reading_list.md`, sections and done state preserved.
- [ ] `shopping` — every entry from `shopping_list.md`, specs intact.
- [ ] `books` — every entry from `book_list.md`.
- [ ] Re-fetch and correct the three misattributed `x.com` reading entries (the 2026-08-06
      decision; see §7).
- [ ] Rewrite the `reading-list-suggestion` heartbeat task to use the collection tools.
- [ ] Archive the three markdown files (move under `archive/`), update the `MEMORY.md` index.
- [ ] Checks: per-collection item counts match the source files; spot-check rich entries.

**C3 — Collections app (agent + jarvis-app).**
- [ ] Agent: `gateway/apps/collections.py` — GET entries (collections, one collection) and
      POST entries (quick actions, §5); one import line in `gateway/apps/specs.py`.
- [ ] jarvis-app client: `AppQueryClient.post()` — the first app write path (the hub already
      relays `POST /v1/apps/{ns}/q/{entry_id}`).
- [ ] jarvis-app: collections home + one generic schema-driven collection screen + detail sheet.
- [ ] Verify against the staging data C1 created; then on prod after deploy.

---

## 1. Problem

Prod memory holds three list-shaped markdown files the agent edits through the general memory
tools:

- `shopping_list.md` — purchases being *considered*, not groceries; one entry carries a URL,
  price, colour, dimensions and a nested specs block.
- `reading_list.md` — ~30 links grouped under `###` topic headings, `[ ]`/`[x]` read state,
  a one-line summary per entry.
- `book_list.md` — books grouped by topic, author + note.

Nothing structured exists: no app can show them, filtering is the model reading the whole file,
and every new list is another free-form file. No code references these files (only `MEMORY.md`
and the `reading-list-suggestion` heartbeat task do).

Markdown stays available to the agent for anything it judges better as prose — this plan adds a
store, it does not remove the memory tools.

## 2. Model

### Why collections are their own model (and tasks are not one of them)

The lists differ less in fields than in **behaviour over time**. Shopping, reading and books
share a lifecycle — a pile of things to consider, grouped by topic, ending done or dropped, with
little proactive behaviour. Todo items are about time (due dates, reminders, overdue, repeats)
and integrate with triggers and the heartbeat. So tasks get their own model
([TASKS_PLAN.md](TASKS_PLAN.md)); everything else is a **collection**.

### Item shape: a small core, opt-in features, custom fields

**Core — every item, always:**
`title`, `notes` (optional free text, the universal escape hatch), `position` (manual order),
`created_at`, `updated_at`.

**Opt-in features — per collection, meaning fixed in code** (code and the app *behave* on them,
so they cannot be user-named fields):
- **status** — the item has a lifecycle. The collection names its states and marks which are
  *closed* (hidden by default). Default: `open` / `done` (done closed).
- **sections** — one heading per item; drives grouping on screen.

**Custom fields — per collection, named at runtime, typed from a code vocabulary:**

| Type | Stored as | App renders | Notes |
|---|---|---|---|
| `text` | string | wrapping text | specs, author |
| `url` | string, validated http(s) | domain chip → opens browser | |
| `number` | number | as-is | units go in the field name (`weight_kg`) |
| `money` | amount + currency code | `₪3,400` | currency defaults to ILS, overridable per value |
| `date` | ISO date | `12 Oct` | |
| `rating` | integer 1–5 | ★★★★☆ | fixed scale |
| `choice` | one of the schema-declared options | small label | |
| `tags` | list of strings, lowercased + trimmed | chips + filter | open vocabulary; a new tag is data, never needs confirmation |

Deliberately absent: `bool` (status or `choice` covers it, and would be confused with status),
per-field scales/units, `image` (no media store; a `url` can point at one). New types are code
changes.

### Schemas are runtime, per collection

A collection's schema (its custom fields, its status states if enabled, whether sections are on)
is created at runtime through the agent and stored with the collection. There is no `kinds`
table: a collection *is* its schema. (A `copy_schema_from` shortcut was designed and dropped
in C1 — too specific a scenario for the code it needs.)

### Examples (the migrated lists, and two that show the range)

| Collection | status | sections | custom fields |
|---|---|---|---|
| reading | unread / read / dropped | ✓ | `url` (+ `topics` tags if wanted) |
| shopping | considering / bought / dropped | ✓ | `url`, `price` (money), `specs` (text) |
| books | to-read / read / dropped | ✓ | `author` (text) |
| favourite restaurants | ✗ (reference list) | ✓ by city | `url`, `rating` |
| packing — Lisbon | open / done | ✗ | — |

### Storage sketch

`jarvis_data/collections/collections.sqlite`, following the travel skill's `_db.py` pattern
(foreign keys on per connection, a read-only URI for the app):

- `collections` — `collection_id`, `name` (UNIQUE, `COLLATE NOCASE`), `schema` (JSON: fields
  `[{name, type, options?}]`, `status` `{states, closed}` or null, `sections` bool),
  `archived_at`, timestamps.
- `items` — `item_id`, `collection_id` (FK, cascade on collection delete), `title`, `notes`,
  `section`, `status`, `extra` (JSON, validated against the schema on every write),
  `position`, timestamps.
- Tag filtering via SQLite `json_each` over `extra` — no tags table.

Closed items are kept forever (hidden by default) so "did I read that?" / "when did I buy it?"
stay answerable. No retention job.

## 3. Confirmation rules

Runtime schemas need a gate so the agent cannot invent shapes unseen. The gate applies only
where drift or data loss is possible:

| Action | Confirmation |
|---|---|
| Create a collection with the core only, default status, and/or sections | No |
| Create with custom fields or custom status states | **Yes** — the prompt lists fields, types and the first items |
| Edit a schema: add a field | **Yes** (harmless, but still a schema change) |
| Edit a schema: rename / retype / remove a field, change status states | **Yes** — prompt states how many items are affected / lose data |
| Add / update / delete a single item | No |
| Archive / unarchive a collection | No |
| Delete a collection | **Yes** — removes every item and its history |

Confirmation in this codebase is fire-and-forget (`request_confirmation_sync` returns at once,
the action runs on tap). So the pending create **carries its first items**; on Confirm, the
collection and those items are written together and "start a wine list with these three"
completes on the tap rather than needing a second turn.

Drift control: `manage_collection` responses for create / edit list field names and types
already in use across collections, nudging reuse (`rating`, not a new `score`); `add_items` /
`update_item` responses on a tags field list that collection's existing tags.

## 4. Agent tools (`collections` skill)

Activatable skill (`tools/collections/`), not core — lists are frequent but not every-turn, and
same-turn activation keeps "add X to reading" a one-message request.

| Tool | Does |
|---|---|
| `manage_collection(action, …)` | `list` (with schemas and open counts), `create`, `edit_schema`, `archive` / `unarchive`, `delete`. `delete` and the gated creates/edits are `destructive`/confirmed per §3. |
| `add_items(collection, items=[…])` | One or many items; each `{title, notes?, section?, status?, fields?}`. Bulk is required — migration moves ~30 entries in a handful of calls. |
| `update_item(item_id, …)` | Edit title / notes / section / status / fields. Empty string clears a value (travel's `_clearable` convention). |
| `list_items(collection, status?, section?, tag?, search?)` | Returns ids. Default hides closed. `search` matches title, notes and URL. |
| `delete_item(item_id)` | For mistakes; no confirmation. |

Conventions: items addressed by id, collections by exact name; list before acting, never guess
(travel's rule). A bad field name or value is refused with the collection's actual schema in the
error, so the error teaches the shape.

Left out on purpose: a read-only SQL query tool (views + `search` should cover real questions;
add if one can't be answered), bulk update.

## 5. jarvis-app mini-app (`collections`)

Read + **quick actions only**; anything needing judgment stays in chat. Full in-app editing is a
possible future step.

| In the app (no model) | Through chat |
|---|---|
| Change an item's status (checkbox for default open/done; status buttons in the detail sheet for custom states) | Create a collection, design or edit a schema |
| Quick-add: title only, into the collection being viewed, default status, no section | Rich adds ("add this link" → fetch + summarise) |
| Delete an item | Sectioning, tagging, filling fields |
| | Archive / delete a collection |

App POST handlers call the same code paths as the tools, so the app can do nothing chat can't
and validation lives in one place.

**Collections home:** name, open count (only when status is on), last updated; archived behind a
toggle and read-only.

**Collection screen — one generic screen, rendered from the schema carried in the payload:**
Open / Closed tabs only if status is on; tag chips only if a tags field exists; section headings
only if sections are on; rows show only fields the schema has (money right-aligned, url as a
domain chip, rating as stars, tags as chips, status label). Tap → read-only detail sheet with
every field, notes, status buttons, Delete. A collection without status is a plain reference
list (no tabs, no checkbox). No per-collection special screens; shopping gets nothing bespoke.

Payload shape is designed in C3 (the app reads the DB directly, read-only URI, as
`gateway/apps/travel.py` does — never by parsing tool output).

## 6. Skill rules (`SKILL.md` body, draft points)

- Address items by id; list first if unknown. Collections by exact name — never create a second
  spelling of an existing one.
- Prefer the core + notes; propose custom fields only when the owner asks to track something
  structured. Reuse existing field names and types.
- Use status only where items have a lifecycle; a reference list has none.
- Never invent a field value that was not given (price, author, URL).
- Markdown memory remains for prose; lists of discrete items belong here.

## 7. Migration (C2) — by conversation, in prod

The owner chose migration by talking to Jarvis with the new tools rather than a script: it is
the real exercise of the tools, and C1's staging verification already rehearses the prompts.

Carried-in follow-up: on 2026-08-06 the owner decided three misattributed `x.com` entries in
`reading_list.md` would be fixed by Jarvis itself re-fetching them once the web skill was in
prod. That fix is folded into C2, applied to the migrated collection items:
`x.com/i/status/2084703057267286118` (Norvex on Anthropic's knowledge-graph guide),
`x.com/i/status/2084407746330296679` (Lunar summarising Karpathy) are wrongly attributed;
`2085023484913012992` (Matt Pocock, skills v1.2) is correct. Worth spot-checking the other
pre-web-skill entries at the same time.

The `reading-list-suggestion` heartbeat task (Fridays ~10:00) reads `reading_list.md` and must be
rewritten to use `list_items` before the file is archived.

## 8. Decisions (grilling session, 2026-10-03)

1. **Replace the markdown lists**, not a parallel system; reading and shopping definitely, books
   too; markdown memory stays as a separate tool for prose.
2. **Tasks and collections are separate models** — split by lifecycle/behaviour, not fields.
3. **Runtime schemas with confirmation** (over code-defined kinds) — the owner controls shape at
   creation time; a new shape costs no deploy.
4. **Minimal core** (title, notes, order, timestamps); **status and sections are opt-in
   features**; URL is an ordinary field type.
5. **`tags` is a field type.**
6. **Schema per collection**; no reusable kinds (`copy_schema_from` dropped in C1).
7. **Keep closed items; archive freely; confirm only collection delete** (and schema changes).
8. **Two skills, two DBs, not core.**
9. **Migration by conversation**, no script.
10. **Two apps, quick actions only**; full editing deferred.
11. **Field-type vocabulary** as in §2, "fine for now".

Rejected along the way: one generic item for all lists (todo is different in kind); a fixed kind
per list (every new kind = migration + Kotlin); code-defined kind specs (each new shape needs a
deploy); agent-defined schemas *without* confirmation (drift); reusable runtime kinds (two-level
management, multi-collection schema edits); a migration script.

## 9. Not in scope

Runtime-defined field types; kind-level behaviour (grocery-style "reset all to open", price
watching, bespoke screens); a read-only query tool; bulk update; in-app full editing; date or
field parsing in quick-add; images.
