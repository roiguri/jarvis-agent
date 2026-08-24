# Form block — structured input from the app

**Status:** in progress — slices 0–1 done (1 verified live on staging 2026-08-24); the hub is
deployed with the form contract, so nothing remaining is blocked on it. Next: slice 2.
**Date:** 2026-08-15 (last updated 2026-08-24).
**Branch:** `feat/app-form-block`.
**Goal:** let a message carry a small set of labelled, prefilled boxes the owner corrects and
submits in one tap, and let the submitted values come back as ordinary inbound content. The
mechanism is a gateway capability with several possible callers; the agent's tool is one of them.

---

## Checklist

Slice detail is at the bottom; this is the tracking view. Slices 0–1 are done; 2–6 are
unblocked (the hub runs the form contract as of 2026-08-24).

- [x] **0 · Contract intake**
  - [x] Updated `contract.md` in `docs/architecture/channels/jarvis-app/` — now at
        `509c222e84e2c915`, the version the hub actually deployed and staging reports (the
        interim `b2dcd9534e07bd23` never shipped; the delta is prose-only)
  - [ ] Bump `PINNED_CONTRACT_VERSION` in `gateway/channels/jarvis_app/client.py` to
        `509c222e84e2c915` — deferred to the end of slice 3/4, since it names the version the
        adapter *speaks*, and silencing the skew warning before then hides the one signal that
        matters mid-build
- [x] **1 · Confirmation fixes** — independent of forms
  - [x] Expire an orphaned `callback_id` using the action update's own `message_id`
  - [x] Retain `callback_id → settled_state` past resolution so a re-tap re-affirms. Only the
        *state* needs retaining, not the message id: `handle_action` records the tap's own
        `message_id`, so the handle a restart forgets is handed back by the update that needs it
  - [x] Learn the settled state from a `MessageAlreadyResolved` refusal, so a further tap
        re-affirms what actually stands rather than losing the same argument again
  - [x] Keep the `ALREADY_HANDLED` guard (declining the handoff's "dead code" note)
  - [x] Verified live on staging (2026-08-24): confirm, cancel, re-tap, TTL eviction, orphan tap
        after a restart, and a Telegram regression pass
- [x] **2 · Neutral seam** — code done 2026-08-24; offline harness green, staging test pending
  - [x] `gateway/blocks/` package: `base.py` (`Block`, `Interactive`, `BlockAction`) +
        `form.py` (`Form`, `FormRow`, `TextField`, `NumberField`) — frozen dataclasses,
        construction-time validation, no wire shapes
  - [x] Size cap (6 rows) and the units-on-multi-field-row rule enforced at construction
  - [x] `callback_id` generated at construction (caller slug + our entropy)
  - [x] `Channel.supports_block(kind)` (default `False`) + `Channel.send_block(text, block)`
        (default raises) — kind-generic, so a future kind never edits the ABC
  - [x] Outbox `send_block_to_owner`; never a partial send. `SendOutcome` unchanged —
        `supports_block` is the pre-flight, so the permanent case never reaches the seam
  - [x] Telegram declines (inherits both defaults; implements neither)
- [x] **3 · Outbound** — jarvis-app; code done 2026-08-24
  - [x] Wire mapping in the channel adapter (`_BLOCK_WIRE` table keyed by block type; no
        `to_wire()` on the neutral model); output validated against the vendored FormBlock schema
  - [x] Origin routing via `CURRENT_THREAD_ID` (`factory.origin_channel`/`origin_outbox`)
  - [x] `PINNED_CONTRACT_VERSION` bumped to `509c222e84e2c915`
  - [x] Rider: the contract check now re-runs on reconnect, not only at startup
- [x] **4 · Inbound** — jarvis-app; code done 2026-08-24
  - [x] Router builds a neutral `BlockAction`; dispatch by kind — `confirmation` resolves below
        the LLM, `form` becomes an inbound turn, other kinds fall through
  - [x] `null` survives as "left empty" in the rendered turn text (`render_submission`)
  - [x] Submission persisted to `chat_history.jsonl` before the turn — free: the shared
        `process_inbound_message` already logs user text before `ask_jarvis`
  - [x] `PATCH` `logged` strictly after the turn; a crashed turn leaves the card live (re-tap
        is the recovery), a failed PATCH is logged and never fails the consumed update
- [x] **5 · The tool** — `tools/core/forms.py`; code done 2026-08-24
  - [x] Docstring guardrails: submit-unchanged precondition, anti-examples, evidence-only
        defaults, "(left empty) is an explicit no-value"
  - [x] Return string describes what was asked (`Form.describe()`: field ids + prefills) —
        the thread is the store
  - [x] Decline directive on an unsupported channel, echoing the prepared values back
- [ ] **6 · First real caller** — proactive post-workout form, prefilled from
      `query_exercise_history`

---

## Context

The jarvis-app hub is adding a `form` block kind: `rows` of `fields`, each field a `text` or
`number` box with an optional `unit` and `default`. A tap arrives on the existing updates
long-poll as a `type: "action"` update carrying `values` — every declared `field_id`, with `null`
meaning "seen and left empty". The agent resolves the card by `PATCH`ing `state` to `logged` or
`expired`. No new endpoint, no new auth.

This was a **contract change, not an addition**. The previously pinned contract
(`45e79b46aed20391`) already declared a `FormBlock`, but a placeholder one: fields were
`{field_id, label}` with no type, no default, no `callback_id`, and an open-string `state`. The
hub's shape replaces it — the vendored `contract.md` now carries the real one — so
`contract_version` moved and `PINNED_CONTRACT_VERSION` (`gateway/channels/jarvis_app/client.py:21`)
must eventually move with it.

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

Building this as "a tool that sends a form" would make it agent-only by construction. And
building the channel seam as `send_form` would make blocks a method-per-kind affair — every future
kind (`buttons` and `card` already exist in the contract) editing the ABC, the Outbox, and every
channel. So the capability is **blocks, not forms**; a form is the first kind carried, not the
shape of the seam. Layering:

- **Neutral model** in `gateway/blocks/` — `base.py` holds `Block` (kind, summary), `Interactive`
  (a `Block` with a `callback_id` — encoding which kinds can be *resolved* as a class check, since
  `card` has no callback and `form`/`buttons`/`confirmation` do), and `BlockAction` (the neutral
  inbound tap). `form.py` holds `Form`/`FormRow`/`TextField`/`NumberField`. Frozen dataclasses,
  channel-agnostic, no hub wire shape anywhere in the package. Every caller constructs these.
- **Channel capability** — `Channel.supports_block(kind)` defaulting to `False` and
  `Channel.send_block(text, block)` defaulting to raise. Kind-generic: adding a block kind never
  edits the ABC. jarvis-app opts in; Telegram doesn't. (Telegram is not actually blockless — a
  `buttons` block *is* an inline keyboard — which is what makes blocks a genuinely neutral concept
  with partial implementations rather than one channel's vocabulary hoisted into the gateway. A
  mature block layer could eventually subsume `ConfirmationUI`; designed-for, not built.)
- **Outbox method** — the owner-addressed seam, with the same log-on-success treatment every other
  send gets. This is what makes it reachable from the heartbeat, a reminder, a slash command, or a
  future skill tool, all as peers of the agent's tool.
- **Channel adapter** owns the wire mapping — an explicit table keyed by block type, deliberately
  *not* a `to_wire()` method on the neutral model, which would put the hub's JSON inside it and
  break the first time a second channel renders the same block — and the `PATCH`.
- **The tool** in `tools/core/` is a thin caller: build a `Form` from model args, call the seam,
  return a description. One caller among several.

Inbound gets the same symmetry: the router builds one neutral `BlockAction` and dispatches by kind
through a table (not an `if/elif` chain) — `confirmation` resolves below the LLM, `form` becomes an
`InboundMessage`. Per-kind resolution vocabularies (`confirmed|cancelled|expired`,
`logged|expired`, `buttons`' open string) live beside each kind's dataclass.

The test of this design is the cost of the **second** kind: adding `buttons` later should be a
dataclass in `gateway/blocks/buttons.py`, one entry in the adapter's wire table, and one entry in
the inbound dispatch table — no edits to the ABC, the Outbox, Telegram, or `Form`. Build only
`form` now: the `scopes` axis in `tools/registry.py` is the standing reminder of what speculative
generality earns.

**Validation lives in block construction**, not the adapter — a bad form should fail at the
caller with a clear message the model can correct, not as a `422` inside an async send after the
tool already returned a hopeful string. Only rules that stand on their own merits go there (units
on multi-field rows is a real accessibility rule); genuinely hub-specific limits stay in the
adapter.

What the vendored schema does and doesn't enforce, which decides what `Form` must carry:

- **`type`/`default` agreement is schema-enforced** — `TextField.default` is a string,
  `NumberField.default` a number, via a discriminated union on `type`. So is `minItems: 1` on a
  row's `fields`.
- **The units-on-a-multi-field-row rule is not.** `unit` is plain optional in the schema and the
  rule lives in a hub-side validator, so violating it is a runtime `422` with no local signal.
  This is the rule `Form` most needs to catch itself.
- **`rows` has no `maxItems`.** The hub imposes no size limit; the ~6-row cap is purely our policy
  and should not later be "corrected" to match the wire.
- **`default` is optional and nullable on both field types** — only `field_id` is required. The
  wire agrees a box can arrive legitimately empty, which is why mandatory defaults were rejected.
- **`values` is refused by the send *route*, not by the type** — the contract notes Pydantic
  cannot see which direction a block travels. The outbound `Form` therefore has no such field at
  all, making a pre-stamped form unconstructible rather than merely forbidden.

**We generate `callback_id`, at construction.** Uniqueness-per-live-form is a correctness property
and shouldn't be delegated to a model that will happily reuse `workout-today`. The caller supplies
a semantic slug and construction appends entropy — `push-day-a3f1` — so an id always exists by the
time anyone holds a block. Semantics from the caller, uniqueness from us.

**Origin routing, not the proactive default.** A form issued during a turn goes to the turn's
origin channel, the way `get_confirmation()` resolves through `CURRENT_THREAD_ID`
(`gateway/factory.py:163`). Otherwise a Telegram turn sends a card into the app, where the tap
arrives with no conversation around it. Proactive sends keep using the default channel.

### Channels that can't render a form

**The seam never falls back — it reports.** Nothing goes out partially: either the
message-with-form sends or nothing does, so the owner never receives a dangling opener. Every
caller then decides for itself — the model composes prose, a code caller sends its own
`notify_owner(...)`. The gateway invents no message.

`SendOutcome` itself stays untouched (a considered no-op: it is just `{ok, error}` and nothing
branches on it structurally today). The permanent case — "this channel has no forms" — is
distinguished from a transient failure by asking `supports_block` *before* sending, so it never
reaches the seam and never needs its own outcome value; whatever the seam returns after a positive
pre-flight is an ordinary transient failure.

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
sees from the pre-flight), so this is a strict subset of the deferred work. Revisit with telemetry
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
   action update carries `message_id`, so expiring an orphan never needed the in-memory map. The
   contract says as much directly — `callback_id` sits on the payload precisely so an agent can
   resolve a decision "without keeping a `message_id`→handle map it would lose on each deploy",
   which is the map `_message_ids` is.
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

- ~~**`PATCH` timing.**~~ **Settled (2026-08-24), from the hub's own source** (`form-renderer`
  branch, the code staging runs): "unacked" is the updates cursor, not our PATCH — an acked
  update is deleted, and the `already_pending` refusal keys off presence in `bot_updates`. Our
  fetch loop acks within milliseconds of the tap, so the re-tap lock is transient and a turn that
  dies before PATCHing leaves the card live and re-tappable, never stranded. That kills the case
  for arrival-PATCHing, which was defensive against a freeze that cannot happen. **Decision:
  PATCH `logged` after the turn completes.** `logged` then means the turn actually ran; a crashed
  turn's recovery is the untouched card itself (re-tap → fresh update → the turn re-runs with
  thread context, so the model can avoid double-logging). Two hub facts back this up: `values`
  are stamped onto the block in the tap's own transaction, so the evidence survives our crash
  regardless; and the hub explicitly accepts losing an update to a crash between ack and turn
  (§7) — which is also why the submission is persisted to `chat_history.jsonl` before the turn.
- **Double-logging.** A workout logged in chat, then the stale card gets tapped. Thread context
  mostly covers it and the agent can see and mention it, but it replaces "lost state" as the
  failure mode to watch.
- **Taxonomy gaps for the first caller.** No `choice` type, so `pain_level` (0–3) becomes a number
  box with `unit: "0-3"`, and `prehab_done` (a bool) has no clean representation — the handoff
  warns against leaning on the bool→1 coercion. Tolerable while the form is a suggestion the model
  interprets; it would not be if values bound straight to a function signature.

---

## Slices

**0 — Contract intake.** *Done.* `contract.md` vendored at `509c222e84e2c915` — the version the
hub deployed and staging reports. The `PINNED_CONTRACT_VERSION` bump is deliberately deferred to
the end of slice 3/4.

**1 — Confirmation fixes.** *Done; verified live on staging 2026-08-24* (confirm, cancel, re-tap,
TTL eviction, orphan tap after a restart, Telegram regression). Shipped as its own commit.

**2 — Neutral seam.** The `gateway/blocks/` package (`Block`/`Interactive`/`BlockAction` +
`Form`/`FormRow`/`TextField`/`NumberField`) with construction-time validation and `callback_id`
generation; `Channel.supports_block`/`send_block`; the Outbox method; Telegram declining. No
callers, so it is testable on its own.

**3 — Outbound.** jarvis-app wire mapping (adapter-side table keyed by block type) and send;
origin routing via `CURRENT_THREAD_ID`. Ends with the `PINNED_CONTRACT_VERSION` bump alongside
slice 4.

**4 — Inbound.** The router builds a neutral `BlockAction` and dispatches by kind: `confirmation`
to the store as today, `form` into an `InboundMessage` carrying rendered text plus the structured
values (`null` surviving as "left empty", distinguishable from zero — it is the one distinction
the hub went out of its way to preserve), persisted to `chat_history.jsonl` before the turn runs
so a dead turn cannot lose what was typed. `PATCH` per the timing decision above.

**5 — The tool.** `tools/core/`, with the size cap, the docstring guardrails, and the decline
directive.

**6 — First real caller.** A proactive post-workout form, prefilled from `query_exercise_history`.

Slices 2–4 are the architecture; 5–6 are the product. Slice 6 is where the premise gets tested:
for reactive chat a form is not obviously better than typing "bench 8x62.5", which already works.
The case that justifies the build is the **unprompted** one — the heartbeat sees a session ended,
pushes a prefilled card, and one tap logs a workout that today simply doesn't get logged.
