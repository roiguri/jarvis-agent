# Time Grounding — Plan

**Date:** 2026-10-03 · **Status:** implemented on `feat/time-grounding`; offline checks pass, staging checks pending.
**Problems addressed:** [context/PROBLEMS.md](context/PROBLEMS.md) B1 (primary), B2 (side effect).
Readings are in PROBLEMS.md §0 and are not repeated here.
**Supersedes:** PR #70 (`docs/ws2-time-grounding`). Its core idea (stamp the turn's input once)
carries forward. Three of its parts do not:

- its two-slice split (see §3);
- adding the UTC offset and UTC time to the stamp, which is B4 and has a better fix (§5);
- its S3 (per-call telemetry) and S4 (trim hysteresis). S4 is obsolete because the turn-lifecycle
  work resolved B3, and S3 isn't needed to ship this (§4).

---

## 1. Problem

`_llm_node` calls `build_system_prompt` on every model call, and the envelope's first line is
`[Current time: <weekday>, <date> <HH:MM> Israel time]`, read from the clock each time. Within one
turn, call 1 can read `06:14` and call 4 `06:15`, so the model's "now" moves while it reasons,
including on turns that compute a reminder's `fire_at`. In `/tz` away mode, the
`[Owner local time: …]` line has the same flaw.

Secondary: the envelope is the first bytes of the request, so a minute rollover mid-turn changes
the whole prefix, and the cache can't be reused for the rest of the turn. PR #70 measured this at
~5% of input. It is now worth a little more: the history no longer shifts mid-turn (B3 resolved),
so the clock is the main thing still moving the prefix within a turn.

## 2. Design

**Move the time out of the system prompt and onto the turn's input, read once at turn start. The
content stays exactly what the model sees today.**

Before, rebuilt on every call:

```
system:  [Current time: Saturday, 2026-10-03 14:05 Israel time]
         [Active scope: user]
         …
human:   remind me in 20 minutes
```

After, fixed for the whole turn:

```
system:  [Current date: 2026-10-03]
         [Active scope: user]
         …
human:   [Saturday, 2026-10-03 14:05 Israel time] remind me in 20 minutes
```

- **Computed once in `ask_jarvis`**, the single entry to a turn, from the turn's start time, and
  never recomputed. All callers (inbound channel messages, confirmation outcomes, heartbeat ticks)
  pass through it, so none can forget.
- **The envelope keeps the date only.** It changes once a day, at Israel midnight, so the system
  prompt stays byte-stable through the day.
- **Away mode moves the same way.** The stamp carries both clocks:
  `[Saturday, 2026-10-03 14:05 Israel time | owner local: Saturday, 2026-10-03 19:05 Asia/Taipei]`.
  The envelope keeps the stable half as `[Owner timezone: Asia/Taipei — times the owner speaks are
  local to them]`. At home both are absent, as today.
- **Not stamped:**
  - The pending-mirror block. It carries its own send times.
  - `chat_history.jsonl`. It is written raw before `ask_jarvis`, with its own `ts`. The heartbeat's
    chat slice renders from it, so a stored stamp would show up twice.
- **No `get_current_time` tool.** Whether the model calls a tool is driven by its docstring and is
  unreliable (PROBLEMS.md F1). "Off by the turn's duration" is a better failure than "sometimes
  forgot to check".

**Accepted cost:** the model's "now" is turn start. Turns are bounded at 300s (user) and 120s
(heartbeat), and the median is ~10s. `manage_reminder` echoes the real time on create, which covers
the one case where that would matter.

**Accepted edge:** without an offset, the stamp is ambiguous for the repeated hour at the autumn DST
change (01:00–02:00 reads the same twice). Today's envelope line has the same property. B4 (§5) is
where an offset would earn its place.

## 3. One slice

Stamp and envelope change ship in **one commit**. Split, the middle state shows the model two
"nows" (a frozen stamp and a still-moving envelope clock) that can disagree by a minute, and B1 is
not yet fixed. Together, the switch is atomic, and reverting is one commit.

**Changes:**
- [x] `agent.py`: a stamp helper that formats from an explicit `now` (never reads the clock
      itself); `ask_jarvis` takes `now` once and prefixes the input, before the media branch, so
      both input shapes (plain text and media content) carry it. No already-stamped guard: every
      caller (channel messages, confirmation outcomes, heartbeat ticks) passes raw text.
- [x] `agent.py` `build_system_prompt`: `[Current time: …]` → `[Current date: …]`;
      `[Owner local time: …]` → `[Owner timezone: …]`.
- [x] Docs: the envelope shape in `docs/architecture/MEMORY.md` and `CLAUDE.md`. Check
      `prompts/*.md` for prose that points at the envelope for the time.

**Verify (offline):**
- [x] The stamp renders the right local time and weekday on both sides of both 2026 DST
      transitions, at home and in away mode.
- [x] Exactly one stamp per human message in the checkpoint, including a confirmation-outcome
      turn and a turn with media.
- [x] `chat_history.jsonl` stays unstamped (structural: it is written in `main.py` from the raw
      inbound text; the stamped string never leaves `ask_jarvis`).
- [x] Assembled prompt read through in both scopes and in away mode: the date only, no clock,
      byte-identical across every call of a multi-call turn.

**Verify (staging):**
- [ ] "What time is it?" answered correctly to the minute.
- [ ] A relative reminder ("in 20 minutes") lands at the right `fire_at`.
- [ ] One heartbeat tick acks normally, including a time-shaped decision (the crossfit "starts in
      under 2 hours" rule).

## 4. Measuring the cache effect

No new telemetry. Compare `cache_read / input` per scope over 3 full days before and after deploy,
choosing days with no paused tasks (PROBLEMS.md E10). If it doesn't move, record that and stop:
correctness alone justifies the change. Per-call attribution (E7) stays a separate item.

## 5. Follow-up, not in this plan: B4

`manage_reminder`'s docstring and `prompts/AGENTS.md` require `fire_at` in UTC, so the model
converts from Israel time by hand on every reminder. The tool itself already accepts any explicit
offset (`datetime.fromisoformat`, and it rejects only a missing offset). Letting the model write
Israel-local time with its offset (`2026-10-03T14:25+03:00`) removes the conversion outright. PR #70
instead put UTC in the stamp, which only made the conversion easier. It is a separate, small change,
and it would add the offset to the stamp only if it turns out the model needs to see it.
