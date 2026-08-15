# Form block — structured input from the app

**Status:** planned, not started. Slice 0 blocked on the hub's updated contract.
**Date:** 2026-08-15.
**Branch:** `feat/app-form-block`.
**Goal:** let a message carry a small set of labelled, prefilled boxes the owner corrects and
submits in one tap, and let the submitted values come back as ordinary inbound content. The
mechanism is a gateway capability with several possible callers; the agent's tool is one of them.

---

## Checklist

Slice detail is at the bottom; this is the tracking view. Slice 0 gates 2–6; slice 1 is
independent and can land first.

- [ ] **0 · Contract intake** — *blocked on the hub*
  - [ ] Updated `contract.md` in `docs/architecture/channels/jarvis-app/`
  - [ ] New `PINNED_CONTRACT_VERSION` in `gateway/channels/jarvis_app/client.py`
- [ ] **1 · Confirmation fixes** — independent of forms
  - [ ] Expire an orphaned `callback_id` using the action update's own `message_id`
  - [ ] Retain `callback_id → (message_id, settled_state)` past resolution so a re-tap re-affirms
  - [ ] Keep the `ALREADY_HANDLED` guard (declining the handoff's "dead code" note)
- [ ] **2 · Neutral seam** — no callers yet, testable alone
  - [ ] `FormSpec` in `gateway/`, channel-agnostic, with construction-time validation
  - [ ] Size cap (~6 rows) enforced at construction
  - [ ] `Channel.send_form` + `can_send_form` (default `False`)
  - [ ] Outbox method with a distinct unsupported-channel `SendOutcome`, never a partial send
  - [ ] Telegram declines
- [ ] **3 · Outbound** — jarvis-app
  - [ ] `FormSpec` → hub wire mapping
  - [ ] `callback_id` generation (caller slug + our entropy)
  - [ ] Origin routing via `CURRENT_THREAD_ID`, not the proactive default
- [ ] **4 · Inbound** — jarvis-app
  - [ ] Route `block_kind == "form"` into an `InboundMessage` instead of `handle_action`
  - [ ] Carry structured values alongside rendered text; `null` survives as "left empty"
  - [ ] `PATCH` `logged` — **timing still open**, see Open questions
- [ ] **5 · The tool** — `tools/core/`
  - [ ] Docstring guardrails: submit-unchanged precondition, anti-examples, evidence-only defaults
  - [ ] Return string describes what was asked (field ids + prefills) — the thread is the store
  - [ ] Decline directive on an unsupported channel, echoing the prepared values back
- [ ] **6 · First real caller** — proactive post-workout form, prefilled from
      `query_exercise_history`

---

## Context

The jarvis-app hub is adding a `form` block kind: `rows` of `fields`, each field a `text` or
`number` box with an optional `unit` and `default`. A tap arrives on the existing updates
long-poll as a `type: "action"` update carrying `values` — every declared `field_id`, with `null`
meaning "seen and left empty". The agent resolves the card by `PATCH`ing `state` to `logged` or
`expired`. No new endpoint, no new auth.

This is a **contract change, not an addition**. The pinned contract already declares a `FormBlock`
(`docs/architecture/channels/jarvis-app/contract.md:1339`), but a placeholder one: fields are
`{field_id, label}` with no type, no default, no `callback_id`, and an open-string `state`. The
hub's new shape replaces it, so `contract_version` moves and `PINNED_CONTRACT_VERSION`
(`gateway/channels/jarvis_app/client.py:21`) must move with it.

Deploy order is one-way: the hub validates strictly, so a `form` sent to an older hub is a `422`,
not a degraded render.

**Today's baseline.** Blocks exist only on the jarvis-app channel, and only one kind is wired:
`confirmation`. An action update is handled below the LLM (`gateway/channels/jarvis_app/router.py:292`
→ `AppConfirmationUI.handle_action`), never reaching the model. `values` is dropped on the floor —
the router doesn't read it. Telegram has no block concept at all.

---

## Decisions carried in from discussion

### A form is content, not a protocol

Confirmation earns its store because something is genuinely pending: `action_fn` is a live closure
waiting for a yes, and losing the `callback_id` loses the action. A form holds none of that.
Nothing is deferred, nothing runs on submit, nothing is lost if we forget it. The submit is
content arriving — more structured than free text, same nature.

Consequences, all of which shrink the build:

- **No pending store.** The action update carries `message_id`, `callback_id`, `block_kind`, and
  `values` — everything needed to `PATCH` and to run a turn. Nothing to look up, nothing to forget
  across a restart.
- **The thread is the store.** The message that sent the form is already in the LangGraph thread,
  so the submit lands in a context that contains the question. This only holds if the sending
  tool's **return string describes what it asked** (field ids and prefills), not just "form sent".
  Correctness never depends on our memory — only intelligibility does, and the worst case is the
  agent asking what a stale submit refers to rather than a card hanging live forever.
- **No TTL, no sweep.** With nothing pending, an untapped form going stale costs nothing. Tapped
  late, it arrives as content and the agent handles it in context. Revisit only if a real problem
  appears.
- **Most of the handoff's §3 dissolves.** "Unknown `callback_id` → expire" and "already-resolved →
  re-affirm" are rules for a system holding pending state. Holding none, every submit is answerable
  the same way. A `MessageAlreadyResolved` on the `PATCH` stays a log line, not a branch.
- **The submit runs a model turn.** Rejected: a deterministic handler that maps `field_id` → a DB
  write with no model in the path. That carries confirmation's semantics onto something that has
  none, and the model is already the thing that composed the form. The inbound path should still
  carry the structured values alongside the rendered text, so a future caller *can* register a
  resolver without re-plumbing — but no resolver registry is built now.

### The mechanism is a gateway capability, not a tool

Building this as "a tool that sends a form" would make it agent-only by construction. Layering:

- **Neutral model** in `gateway/` — a `FormSpec` of rows and fields, channel-agnostic, no hub wire
  shape in it. Every caller constructs this.
- **Channel capability** — `Channel.send_form(spec)` plus a `can_send_form` property defaulting to
  `False`. jarvis-app implements it; Telegram doesn't.
- **Outbox method** — the owner-addressed seam, with the same log-on-success / `SendOutcome`
  treatment every other send gets. This is what makes it reachable from the heartbeat, a reminder,
  a slash command, or a future skill tool, all as peers of the agent's tool.
- **Channel adapter** owns the wire mapping and the `PATCH`.
- **The tool** in `tools/core/` is a thin caller: build a spec from model args, call the seam,
  return a description. One caller among several.

**Validation lives in `FormSpec` construction**, not the adapter — a bad form should fail at the
caller with a clear message the model can correct, not as a `422` inside an async send after the
tool already returned a hopeful string. Only rules that stand on their own merits go there (units
on multi-field rows is a real accessibility rule); genuinely hub-specific limits stay in the
adapter.

**We generate `callback_id`.** Uniqueness-per-live-form is a correctness property and shouldn't be
delegated to a model that will happily reuse `workout-today`. The caller supplies a semantic slug
and we append entropy — `push-day-a3f1`. Semantics from the caller, uniqueness from us.

**Origin routing, not the proactive default.** A form issued during a turn goes to the turn's
origin channel, the way `get_confirmation()` resolves through `CURRENT_THREAD_ID`
(`gateway/factory.py:163`). Otherwise a Telegram turn sends a card into the app, where the tap
arrives with no conversation around it. Proactive sends keep using the default channel.

### Channels that can't render a form

**The seam never falls back — it reports.** An unsupported channel is a distinct `SendOutcome`, not
a generic failure, and nothing goes out partially: either the message-with-form sends or nothing
does, so the owner never receives a dangling opener. Every caller then decides for itself — the
model composes prose, a code caller sends its own `notify_owner(...)`. The gateway invents no
message.

The tool turns that outcome into a directive the model recovers from inside the same turn, echoing
the prepared values back so the recovery is a rewrite rather than a re-derivation:

> `Forms aren't available on telegram — nothing was sent. Say this conversationally instead. You
> had prepared: Bench press 8 reps @ 60kg · Cable fly @ 22.5kg · Core: plank 3×45s.`

That note is strictly agent-facing. Appending "(a form could not be sent)" to the owner's message
exposes plumbing to someone on a channel that never had cards and doesn't know it's missing
anything.

**Rejected: auto-sending the message's `text` as the fallback.** A form's payload carries no prose,
so every send already has an authored sentence — but that sentence is written *for a card* and
references an affordance ("fill in what you hit") that doesn't exist elsewhere. Requiring it to
read well in both situations produces a sentence that's mediocre in both.

**Rejected: `summary` as the fallback.** The contract gives `summary` no description on any block
kind; our own confirmation code guesses at its purpose. It's plausibly a notification preview or a
collapsed-list label, and reads as a label rather than something said to a person.

**Rejected (for now): capability-scoped tool binding.** Not binding the form tool at all on a
channel that can't render one is cleaner in principle, but it's a genuinely new registry axis —
`scopes` (`tools/registry.py:43`) filters on *why* a turn runs, not *where*, and folding the two
together would make the value space a union of unrelated vocabularies. It's also *less* legible,
not more: with the tool always bound, the decline is visible in the transcript as a `ToolMessage`,
whereas a silently absent tool is the thing that's hard to explain when reading a log weeks later.
The decline path is needed regardless (as the bind-then-send backstop, and as what a code caller
reads off `SendOutcome`), so this is a strict subset of the deferred work. Revisit with telemetry
if the model reaches for forms on form-less channels often. If it is ever built it must be
**capability-shaped** (`requires=("forms",)`), never channel-shaped — tools must not name a
channel. Weak supporting evidence: `scopes` was built speculatively and no tool uses it today.

### Keeping the tool from being over-used

A generic form tool is one the model will reach for. Ranked by how much they can be trusted:

1. **Size cap** (~6 rows). A twelve-row form is a survey, and a survey is a conversation someone
   decided to skip. Mechanical, enforced at construction.
2. **Docstring with anti-examples.** Tool usage in this repo is driven by docstrings, so this is
   the real lever: a form is appropriate only when the expected outcome is *submit unchanged*.
   Anything the owner has to compose belongs in chat, where they can be vague, correct themselves,
   and get a follow-up — none of which a form can do.
3. **Defaults must be evidence, never guesses.** Last session's weight, yes; a plausible-looking
   number for an exercise with no history, no.
4. **The channel gate is free.** Forms don't exist on Telegram, so that traffic is unaffected.
5. **Measure it.** Sent-vs-tapped ratio is the empirical over-use signal; sends are already in
   `tool_calls.jsonl`. Forms sent and ignored mean the model is reaching where a sentence would do.

**Rejected: mandatory `default` on every field.** The intent was to make questionnaire-misuse
structurally impossible, but it forces a caller with no history to invent a prefill — and an
invented prefill is the most dangerous thing on the card, because submit-unchanged is the designed
happy path. The owner taps through and the DB holds a lift nobody did. It also fights the
contract's own model, where `null` exists precisely so a box can be empty *meaningfully*.
Fabricated evidence is worse than a blank box.

### Two confirmation bugs found on the way

Both are about the confirmation protocol, which does keep state, and both stand alone — worth
fixing whether or not forms ship.

1. **Orphaned confirmations never expire.** An unknown `callback_id` routes into `store.resolve`,
   hits `ALREADY_HANDLED` (`gateway/confirmation/store.py:139`), which maps to `None` in
   `_WIRE_STATE` (`gateway/channels/jarvis_app/confirmation.py:31`) and returns without `PATCH`ing.
   After a restart the owner's card sits live forever. The fix is in the handoff's own detail: the
   action update carries `message_id`, so expiring an orphan never needed the in-memory map.
2. **"Re-affirm, don't expire" is impossible as written.** `apply_outcome` does
   `self._message_ids.pop(...)` (`gateway/channels/jarvis_app/confirmation.py:60`), discarding the
   handle at resolution. Re-affirming needs `callback_id → (message_id, settled_state)` retained
   past resolution, in a bounded set with its own eviction.

**Declining one instruction from the handoff.** It says the hub now refuses a second tap while one
is unacked, so any defensive dedup is dead code. Our `ALREADY_HANDLED` path is not that defense —
it's the deploy-amnesia path and the failed-`PATCH` path, both still live and both required by §3.
Keep it; read that sentence as scoped to hub-side duplicate delivery.

---

## Open questions

- **`PATCH` timing.** On submit arrival, or after the turn that handles it succeeds? Arrival is
  honest to the hub's relay model and can't leave a card hanging if the turn crashes. After-the-turn
  makes the card's state mean "this was acted on", which is how the owner will read it. Leaning
  arrival, with the agent's reply carrying the real outcome — a UX call more than an architectural
  one.
- **Double-logging.** A workout logged in chat, then the stale card gets tapped. Thread context
  mostly covers it and the agent can see and mention it, but it replaces "lost state" as the
  failure mode to watch.
- **Taxonomy gaps for the first caller.** No `choice` type, so `pain_level` (0–3) becomes a number
  box with `unit: "0-3"`, and `prehab_done` (a bool) has no clean representation — the handoff
  warns against leaning on the bool→1 coercion. Tolerable while the form is a suggestion the model
  interprets; it would not be if values bound straight to a function signature.

---

## Slices

**0 — Contract intake.** *Blocked on the hub.* Updated `contract.md` and the new
`PINNED_CONTRACT_VERSION`. Nothing else can start against a schema we don't have.

**1 — Confirmation fixes.** The two bugs above. Independent of forms; ships alone.

**2 — Neutral seam.** `FormSpec` + construction-time validation, `Channel.send_form` /
`can_send_form`, the Outbox method and its unsupported outcome, Telegram declining. No callers, so
it is testable on its own.

**3 — Outbound.** jarvis-app wire mapping and send; `callback_id` generation.

**4 — Inbound.** Route `block_kind == "form"` in the router into an `InboundMessage` carrying
rendered text plus the structured values (`null` surviving as "left empty", distinguishable from
zero — it is the one distinction the hub went out of its way to preserve). `PATCH` per the timing
decision above.

**5 — The tool.** `tools/core/`, with the size cap, the docstring guardrails, and the decline
directive.

**6 — First real caller.** A proactive post-workout form, prefilled from `query_exercise_history`.

Slices 2–4 are the architecture; 5–6 are the product. Slice 6 is where the premise gets tested:
for reactive chat a form is not obviously better than typing "bench 8x62.5", which already works.
The case that justifies the build is the **unprompted** one — the heartbeat sees a session ended,
pushes a prefilled card, and one tap logs a workout that today simply doesn't get logged.
