# WS2 Time Grounding — Execution Plan

**Parent:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) WS2 (rewritten 2026-08-01) — the
measurements, the reference-implementation read, and the timezone/day-edge verification live there
and are not repeated here.
**Date:** 2026-08-01 · **Status:** slices unstarted.
**Scope:** WS2 items 1–3 (time grounding) and the instrument that decides item 4. Item 4 itself
(trim hysteresis) is specified but gated — see [S3](#s3--per-call-cache-telemetry) / [S4](#s4--trim-hysteresis-gated-on-s3).

---

## Executive summary

`build_system_prompt` is called inside `_llm_node` (`agent.py:483`), so the envelope's
`[Current time: … HH:MM …]` line is rebuilt on **every LLM call**. Within one turn, call 1 reads
`06:14` and call 4 reads `06:15` — the model's "now" moves underneath it mid-reasoning, on turns that
may be computing a reminder's `fire_at`. Nothing logs it, nothing tests it. **That is the bug this
plan fixes.**

The cache benefit is real but secondary and was overstated: median heartbeat turn is 12.6s with zero
turns ≥60s, so minute-boundary crossings are ~23%, capping the clock's cache cost at ~5% of input
(~$1/mo). The larger cache line item is the sliding 50-message window (~$6.65/mo), which is item 4 —
deliberately **not** bundled here, because it is a different change with a different risk profile and
its own gate.

**Two small slices ship the fix**, in this order, because the reverse order leaves the model
temporally blind for a deploy window:

| | Slice | Nature | Revert |
|---|---|---|---|
| S1 | Per-turn stamp on the message | additive — clock still in the prompt | 1 commit |
| S2 | Envelope `Current time` → `Current date` | removal | 1 commit |
| S3 | `llm_calls.jsonl` per-call telemetry | additive, read-only | 1 commit |
| S4 | Trim hysteresis | **gated on S3's reading** | 1 commit |

---

## Settled design decisions

Carried from the parent's §2.3/§2.4 so this file is self-sufficient at the keyboard.

**Stamp format** — one uniform stamp, applied to every turn's input:

```
[Sat 2026-08-01 09:14 +03:00 | 06:14Z] <original text>
```

- **3-letter day-of-week.** OpenClaw's comment (`agent-timestamp.ts:47`): *"small models can't
  reliably derive day-of-week from a date, and may treat a bare 'Wed' as a typo. Costs ~1 token."*
- **Numeric offset, never `%Z`.** Python yields `IST` for Israel winter, which also reads as India
  Standard Time.
- **UTC reference included for every scope, not just the tick.** `AGENTS.md:1` requires
  `manage_reminder`'s `fire_at` in ISO 8601 UTC while all user-facing times are Israel-local, so the
  model does a hand conversion on every reminder — and reminders originate mostly in *user* turns.
  Handing it both removes the step. This is why no separate heartbeat-only `Reference UTC:` line is
  needed; the parent's design item 3 collapses into S1.
- **Computed once, from the turn's own start time, never recomputed.** That is the entire
  byte-stability property. A stamp that is ever rebuilt from `now` defeats the purpose.

**Where it is applied** — `ask_jarvis` (`agent.py:610`), the single entry to a turn. Its three call
sites (inbound channel message `main.py:66`, confirmation-outcome reply `main.py:181`, heartbeat tick
`heartbeat.py:87`) therefore cannot each forget. `ask_jarvis_once` (`agent.py:881`) is **out of
scope** — it bypasses the agent loop entirely (no system prompt, no tools, no history) and only
formats notification text.

**Envelope after S2** — `[Current date: 2026-08-01]` at day resolution, replacing the clock. One
cache break per day at Israel midnight (21:00/22:00 UTC), inside the overnight stretch where the gate
logs `nothing due — skipping model turn`. **No timezone line is added**: `prompts/AGENTS.md:1`
already carries it, and duplicating it costs bytes for nothing.

**Not building: a `get_current_time()` tool.** `manage_reminder` already echoes the real current time
in its create confirmation (`tools/core/scheduling.py:127`), covering the one path where precision
bites. WS8a's lesson is that whether the model calls a tool is docstring-driven and unreliable;
"sometimes forgets to check the time" is a worse failure than "off by the turn's duration".

**Accepted cost:** the model's "now" becomes turn-*start* time. Median turn is 12.6s (heartbeat) /
8.9s (user), worst case ~90s bounded by the heartbeat timeout. Below any threshold that matters for
"remind me tomorrow at 9", and self-correcting within the turn via the `manage_reminder` echo.

---

## S1 — Per-turn stamp

Additive: the envelope clock stays, so the model temporarily sees the time twice. Harmless, and it
means S1 can be verified in isolation before anything is removed.

- [ ] `agent.py` — add `_turn_stamp(now: datetime) -> str` next to the existing `_today_israel`
      helpers. Formats the spec above from an explicit `now`, never reading the clock itself.
- [ ] `agent.py` `ask_jarvis` — take `now = datetime.now(timezone.utc)` once at the top, alongside
      the existing `turn_id`/ContextVar setup, and prefix `user_input`. Must land **before** the
      media branch at `agent.py:657` so it covers both shapes (bare string, and
      `content[0]["text"]`).
- [ ] Guard: skip if `user_input` already matches the stamp pattern. Cheap insurance — no current
      caller passes a stamped string, but the confirmation path feeds synthesized text back in.
- [ ] Leave `main.py:58` alone — it writes raw text to `chat_history.jsonl` *before* `ask_jarvis`, so
      the log stays unstamped and keeps its own `ts`. This is load-bearing: the injected chat slice
      renders `[HH:MM] message` from that log, and a stored stamp would double up there.

**Verify.**

- [ ] Format across both 2026 DST transitions and both offsets: `+03:00` in summer, `+02:00` in
      winter, day-of-week correct, `Z` value consistent with the offset.
- [ ] Live (staging, then prod after restart): send a Telegram message, confirm the reply reasons
      from the right time; confirm `chat_history.jsonl`'s stored text is **unstamped**.
- [ ] Ask for a reminder in relative terms ("in 20 minutes"); check the `fire_at` echoed by
      `manage_reminder` against wall-clock.
- [ ] One heartbeat tick: confirm the tick body carries the stamp and the tick still acks normally.
- [ ] Confirm the stamp appears exactly once per message in the checkpoint (no double-stamping).

**Revert:** one commit; no state migration — old unstamped messages in the window are fine, and the
window self-heals within a day.

---

## S2 — Envelope: clock → date

- [ ] `agent.py:407` — `[Current time: %A, %Y-%m-%d %H:%M Israel time]` → `[Current date: %Y-%m-%d]`.
- [ ] `docs/architecture/MEMORY.md:115` — envelope shape in the prompt-assembly diagram.
- [ ] `CLAUDE.md:117` — same, in the System Prompt Architecture block.
- [ ] Check `prompts/AGENTS.md` and `prompts/heartbeat.md` for prose that points at the envelope for
      the time. (`AGENTS.md:1` states the timezone and the `fire_at` UTC rule, which stay correct;
      confirm nothing says "the current time is in the envelope".)

**Verify.**

- [ ] Read an assembled prompt through in **both** scopes — the parent's design item 5 (stable →
      volatile ordering) is *not* in this slice, so this is a content check only.
- [ ] "What time is it?" in chat — answered from the stamp, correct to the minute.
- [ ] A relative reminder again, post-change — this is the sharpest consumer of the clock.
- [ ] One heartbeat tick with a `<4h` time-shaped decision in it (the crossfit briefing rule), to
      confirm tick reasoning still has what it needs.

**Revert:** one commit.

---

## S3 — Per-call cache telemetry

Independent of S1/S2; can land any time. Read-only.

Per-turn telemetry cannot attribute a cache miss to a call — `record_llm_call` sums into the turn
accumulator (`observability/telemetry.py:112`), so ~5 calls report as one number.

- [ ] `observability/telemetry.py` — add `LLM_CALLS_LOG`; extend
      `record_llm_call(response, *, system_prompt=None, messages=None)` with optional kwargs; append
      one row per call: `ts`, `turn_id`, `call_index`, `input_tokens`, `cache_read_tokens`,
      `output_tokens`, `msg_count`, `head_hash` (sha1 of `messages[0]`, first 8 hex). Hashes only —
      no content, consistent with `tool_calls.jsonl` recording `args_size` rather than args.
- [ ] `agent.py:483` `_llm_node` — hoist the system prompt to a local (it is currently built inline
      inside `invoke(...)`) and pass both through.
- [ ] `main.py:168` — add `LLM_CALLS_LOG` to the trim tuple for the 90-day retention.
- [ ] `docs/architecture/OBSERVABILITY.md` — schema line.
- [ ] Optional: join it in `scripts/trace.py` so the per-turn timeline shows cache hits beside the
      tool calls it already prints.

**What it decides.** With the clock already out of the prompt, the question is binary:

| Observation across calls *k−1* → *k* | Conclusion |
|---|---|
| `head_hash` changed / `msg_count` dropped | the trim is costing the discount → **build S4** |
| head unchanged and `cache_read` still flat | Gemini's implicit cache → **do not build S4** |

The second row is the whole reason to measure first: today we would learn it only after shipping S4.

**Cost:** ~750 LLM calls/week in prod, ~200 bytes/row. Negligible.

---

## S4 — Trim hysteresis (gated on S3)

**Do not start until S3 has produced a reading.** Specified here so the gate has something concrete
to approve.

`_add_and_trim` (`agent.py:133`) is the reducer on `messages`, so it runs on every state update —
after each `_llm_node` return *and* each `_tool_node` return. Both threads sit at `MAX_MESSAGES`
(heartbeat 43, telegram 48 after the HumanMessage-boundary advance) and a tick adds ~11.5 messages,
so the head slides several times inside a single turn.

- [ ] Trim to a low-water mark only once past a high-water mark (e.g. 70/50) instead of slicing to
      exactly `MAX_MESSAGES` every update.
- [ ] Keep the HumanMessage-boundary advance (`agent.py:142`) — it exists so a slice cannot orphan a
      tool-call sequence, and hysteresis does not remove that hazard.

**Trade being accepted:** some turns carry more messages than today, i.e. more input tokens, bought
back at a 90% cache discount. Good trade at the measured rates, but a trade.

**Conflict:** WS7's tool-result pruning edits the same function. Land together or sequence
deliberately; they compose (pruning shrinks message *content*, hysteresis changes *when* the window
slides) but they will conflict textually.

---

## Deferred

- **Parent design items 5–6** (stable → volatile prompt reordering; moving `[Channel: …]` to the
  tail). Pure reordering, no behavior change, but it is a separate verification (a prompt
  read-through per scope) and buys nothing until S3 says caching is recoverable at all.
- **DST-gap window validation.** A `due:` window inside 01:00–03:00 Israel is genuinely ambiguous:
  02:30 falls in the spring-forward gap, and 01:30 occurs twice at fall-back (`datetime.combine`
  silently takes `fold=0`). No current task has one. Belongs in `manage_heartbeat_task` as an
  authoring-time warning, not in gate logic. See parent §2.4.
- **`morning-readiness-check` window margin.** `ticks left: 0` per `heartbeat-assert` §2b. Verified
  **not** an imminent DST loss — it is stamped at 06:00Z, in-window in both offsets. Remains a
  dropped-tick fragility. Left as-is by owner decision 2026-08-01.

---

## Verification instrument

Claude cannot restart either service. Each slice's live checks run after the owner restarts, and the
provenance block printed by `scripts/jrestart.sh` is what confirms which code is running — never
infer service state.

Staging first (`/app/jarvis_staging`, `jarvis-staging.service`, `JARVIS_ROOT=/app/jarvis_staging`),
then prod via `deploy/deploy.sh`.

`heartbeat-assert` is the regression net for anything that touches a tick: run it after S1 and after
S2 and expect §1 (ack discipline, delivery) and §3 (turns/day, input/day, `no_action` coherence) to
stay green. `TEST_HARNESS_PLAN.md:91`'s cache-prefix test is `xfail` today with the note "the clock
is line 1"; S2 is what makes it flippable, but it must compare *whole requests*, not just system
prompts.

Record before/after `summarize_usage(group_by="scope")` readings in each PR. Per the parent's §0.4:
"verified in production" without a recorded reading is worth nothing.
