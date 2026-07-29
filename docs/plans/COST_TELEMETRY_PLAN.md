# Cost telemetry — correctness, then visibility, then a repeatable review

**Issue:** _(unfiled)_ — successor concern to #33 "reduce token spend".
**Date:** 2026-07-29.
**Inputs:** live `models.list` against the production key; the Gemini pricing page
(read 2026-07-29); a tool-calling smoke test of `gemini-3.6-flash` /
`gemini-3.5-flash-lite` through the installed `langchain-google-genai` 4.2.6;
30-day rollup of `turns.jsonl` (757 turns, 3,076 LLM calls).
**Companion:** [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) — WS2/WS3 attack the
spend this plan learns to *measure*. Slice A corrects a number that plan quotes.

---

## Manager summary

**Problem.** We cannot currently answer "what does Jarvis cost" correctly. Three
compounding faults:

1. `MODEL_PRICES` (`observability/usage.py:32`) prices `gemini-3-flash-preview` at
   $0.075/$0.30 per M tokens. The real rate is **$0.50/$3.00**. Every cost figure
   the system has ever produced — `/usage`, the `$3.7/month` in
   [CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md#L41) — is **6.5x low**. Real
   30-day spend is ~$22, not ~$3.37.
2. An unknown model silently prices at **$0.00** (`MODEL_PRICES.get(model, _ZERO_PRICE)`).
   Swapping `agent.py:165` without touching `usage.py` makes `/usage` report zero
   forever, with no warning.
3. Output tokens are majority *reasoning* tokens on every Gemini 3.x model, and we
   don't record them. `thinking_level` is the single biggest cost/quality dial
   available, and we are blind to the only observable it moves.

**Plan.** Three slices, strictly ordered. Each is independently shippable and
revertable; none requires deciding on a model upgrade.

| # | Slice | Fixes | Size | Deliverable |
|---|---|---|---|---|
| A | Price-table correctness | faults 1 + 2 | ~1h | `/usage` tells the truth, retroactively |
| B | Reasoning-token capture | fault 3 | ~2h | The `thinking_level` dial becomes measurable |
| C | `cost-review` skill | recurrence | ~2h | The analysis is repeatable, not a one-off chat |

**Why this order.** A is pure read-side and **retroactively corrects all history** —
`usd_cost` is computed at read time by `estimate_usd` inside `summarize_usage`, never
stored in `turns.jsonl`. So fixing the table repairs every rollup back to May without
touching a log line. B is write-side and only accrues data *forward* from deploy, so
the sooner it lands the sooner a baseline exists. C consumes both and is worth little
before they are correct.

**Explicitly out of scope.** The model upgrade itself. This plan makes the decision
*measurable*; it does not make it. See [Appendix: the upgrade question](#appendix--the-upgrade-question).

---

## Measured baseline (30 days to 2026-07-29)

| scope | turns | LLM calls | input | cache read | output |
|---|---|---|---|---|---|
| heartbeat | 587 | 2,619 | 46.2M | 19.6M | 1.05M |
| user | 170 | 457 | 11.9M | 4.2M | 0.12M |
| **total** | **757** | **3,076** | **58.1M** | **23.8M** (41%) | **1.17M** |

At corrected rates: **$21.83 / 30d**. Heartbeat is ~80% of it. Reported today: $3.37.

---

## Slice A — price-table correctness

**Goal.** `/usage` reports real dollars, and a model swap can never again silently
report zero.

### A1 — correct the rate and date the source

`observability/usage.py`, the `MODEL_PRICES` dict and the comment above it. Current
entry is wrong; the comment's "best-effort estimate … verify against the live pricing
page" hedge is *why nobody caught it*. Replace the hedge with a verification date.

Rates read from the pricing page on 2026-07-29, USD per M tokens:

| model | input | cache read | output |
|---|---|---|---|
| `gemini-3-flash-preview` (current) | 0.50 | 0.05 | 3.00 |
| `gemini-3.6-flash` | 1.50 | 0.15 | 7.50 |
| `gemini-3.5-flash` | 1.50 | 0.15 | 9.00 |
| `gemini-3.5-flash-lite` | 0.30 | — (no context cache) | 2.50 |
| `gemini-3.1-flash-lite` | 0.25 | 0.025 | 1.50 |
| `gemini-2.5-flash` | 0.30 | 0.03 | 2.50 |

Pre-populate **all** of them, not just the one in use — that is what makes a future
model swap a one-line change in `agent.py` instead of a silent-zero incident.

`gemini-3.5-flash-lite` has no standard-tier context caching. Encode it honestly
(`cache_read_per_m` equal to the input rate, with a comment) so `estimate_usd`'s
subtract-then-reprice arithmetic still yields the correct total rather than a
discount that does not exist.

### A2 — make an unpriced model visible

`_ZERO_PRICE` must stay as the fallback (records legitimately carry `model: null` when
a turn made no LLM call — raising would break `/usage` on real history). But silence
is the bug. Surface it instead:

- `_empty_bucket` gains an `unpriced_models: set()`.
- `summarize_usage` adds any `model` that missed `MODEL_PRICES` **and is not `None`**.
- `_row_line` / `format_usage_table` append a `⚠ unpriced: <name>` marker when the set
  is non-empty.

A cost of `$0.0000` next to a warning is honest. A cost of `$0.0000` alone is a lie.

### A3 — known limitation, documented not fixed

Audio input bills at 2x text ($1.00/M vs $0.50/M on the current model) and Jarvis
takes Telegram voice notes. `MODEL_PRICES` is single-rate per model and cannot express
this, so audio-heavy days under-report slightly. Document it in the module docstring;
do not build modality tiering for a rounding error.

### A5 — `/usage` rendering (added 2026-07-29, not in the original plan)

Folded in while the file was open. Presentation, not correctness — but one part
*was* a latent bug: `_row_line` emitted a **literal `•`**, which is not a markdown
list item. Telegram's converter only rewrites `^[-*+]\s`, so the literal bullet
passed through fine there — but a CommonMark client (the app channel) treats the
bare newlines between rows as soft breaks and flows every row onto one line. This
is the identical defect fixed for `/help` in `899fe67`.

- Rows become real `- ` list items; blank line after the title and around the
  breakdown, so both renderers keep the blocks apart.
- `**bold**` throughout. `*group*` was hitting `_inline`'s *italic* rule
  (`markdown_to_html.py:103`), not bold — inconsistent with the `**` title the
  handler passes in.
- The eight-field totals line splits into one metric family per line; it was
  wrapping mid-metric on a phone.
- `_usd()` replaces `:.4f`: cents at or above $0.01, 4dp below. `$21.1543` was
  noise on a monthly total, while a sub-cent day still needs the precision.
- Thousands separators on turn/call counts.

Verified by rendering through the real `markdown_to_html.convert()`, plus an
assertion that no emitted line is anything but a list item or a header.

### A4 — doc sync

| file | change |
|---|---|
| `docs/plans/CONTEXT_HANDLING_PLAN.md:41` | `~$3.7/month` → corrected figure. It is the stated justification for WS2 — the true number (~$22/mo, 80% heartbeat) makes that case *stronger* |
| `observability/usage.py` module comment | hedge → "verified against the pricing page 2026-07-29" |

`docs/architecture/OBSERVABILITY.md` needs no change in this slice — it documents the
schema, and no schema field moves.

### Verify

Offline first, no restart needed (pure read side):

```bash
JARVIS_ROOT=/app/jarvis_staging venv/bin/python -c "
from observability import summarize_usage, israel_last_n_days
since, until = israel_last_n_days(30)
rows = summarize_usage(since=since, until=until, group_by='scope')
for r in rows: print(r['group'], round(r['usd_cost'], 2))"
```

Expect heartbeat ≈ \$18, user ≈ \$4. Then **ask the owner to restart staging** and run
`/usage week` on Telegram — the figure should jump ~6.5x versus what it printed before.

**Done when** the offline number matches the table above and `/usage` on the live
staging bot agrees.

---

## Slice B — reasoning-token capture

**Goal.** Make `thinking_level` measurable before there is any reason to change it.

### Why this is not a cosmetic counter

Reasoning tokens are the *majority* of output on Gemini 3.x. On an identical trivial
prompt the smoke test returned `reasoning: 59` of 76 output tokens for
`gemini-3-flash-preview` and `reasoning: 74` of 91 for `gemini-3.6-flash`. Output is
the expensive side of the bill ($3.00/M today, $7.50/M on 3.6). Without this field we
cannot distinguish a successful `thinking_level="low"` tuning from a quality
regression, nor see a model's reasoning appetite eating the budget before the invoice.

**Billing note, and the difference from `cache_read_tokens`:** `cache_read` is a
*discounted slice of input*, so `estimate_usd` subtracts it out — it changes the
arithmetic. `reasoning` is a slice of output billed at the **ordinary output rate**.
It is a **diagnostic, not a billing field**, and `estimate_usd` must stay untouched.
Getting this backwards would double-count.

### B1 — write side

`observability/telemetry.py`, mirroring the existing `cache_read` handling exactly:

- `record_turn_start`'s accumulator gains `"reasoning_tokens": 0`, positioned with the
  other token counters.
- `record_llm_call` reads `usage.get("output_token_details") or {}` then
  `.get("reasoning")`, with the same None-safe idiom already used for
  `input_token_details` (providers vary; the field is absent on non-thinking models).
- Update the `record_llm_call` docstring's `usage_metadata` shape sketch — it currently
  documents only `input_token_details`.

Field confirmed present in `langchain-google-genai` 4.2.6: the smoke test returned
`output_token_details={'reasoning': 59}`.

### B2 — read side

`observability/usage.py`: `_empty_bucket`, the `summarize_usage` accumulation loop, and
`_row_line`. Render it **conditionally**, following the `_cache_pct` precedent — records
written before this slice have no such key, and a rollup spanning the deploy boundary
must not print a misleading "0% reasoning" for the older half.

Backfill is a non-issue: the `int(t.get(...) or 0)` idiom already used throughout
handles absent keys.

### B3 — doc sync

| file | change |
|---|---|
| `docs/architecture/OBSERVABILITY.md` (~L50) | add `reasoning_tokens` to the `turns.jsonl` JSON sample **and** a prose paragraph beneath it |

That file documents every non-obvious field with its `usage_metadata` sourcing — the
`cache_read_tokens` paragraph is the template. The new paragraph must state the
diagnostic-not-billing distinction, or a future reader will "fix" `estimate_usd` to
subtract it.

`DEVELOPMENT.md` needs no change in this slice — no new runtime constant. Its
`LLM_MODEL` / `LLM_TEMPERATURE` rows are the *upgrade's* problem, not this plan's.

### Verify

Restart staging (owner), send one Telegram message, then:

```bash
JARVIS_ROOT=/app/jarvis_staging venv/bin/python -c "
import json
r = [json.loads(l) for l in open('/app/jarvis_staging/jarvis_data/logs/turns.jsonl') if l.strip()][-1]
print({k: r[k] for k in ('model','output_tokens','reasoning_tokens')})"
```

**Done when** the newest record carries a non-zero `reasoning_tokens` strictly less
than its `output_tokens`, and `/usage today` renders the new figure while `/usage`
over a pre-deploy date still renders cleanly without it.

---

## Slice C — the `cost-review` skill

**Goal.** Turn this conversation into something re-runnable monthly, so price drift
and model drift get caught by a procedure instead of by chance.

**Placement decision.** A **Claude Code skill** at `.claude/skills/cost-review/SKILL.md`,
alongside the existing `code-review` and `heartbeat-assert`. Deliberately *not* a
Jarvis skill under `tools/`: the owner-facing runtime surface already exists as
`/usage` (`gateway/commands/handlers.py:297`), and a `tools/cost/` skill would
duplicate it while adding tokens to every prompt. What is missing is the **dev-side
audit** — the part that reads the live pricing page and the live model list, which
Jarvis has no business doing hourly.

### C1 — define the boundary against `heartbeat-assert`

`heartbeat-assert` already claims "token usage vs baseline" in its description. Left
alone, the two skills drift into contradicting each other. The split:

| | `heartbeat-assert` | `cost-review` |
|---|---|---|
| Question | is the heartbeat **healthy**? | is our spend **correct and efficient**? |
| Window | last 48h | last 30d |
| Unit | ticks, acks, gate decisions | dollars, tokens, rates |
| External calls | none | pricing page + `models.list` |

`cost-review` must not re-run heartbeat health checks; where the heartbeat's *share* of
spend looks wrong, it defers to `heartbeat-assert` by name. Trim the token clause from
`heartbeat-assert`'s description if it reads as overlapping once C ships.

### C2 — the skill itself

Frontmatter per house style: `name`, `description` with explicit triggers ("what does
Jarvis cost", "cost review", "are we on the right model", "check model pricing").
`disable-model-invocation: true`, following `heartbeat-assert` — this is an
owner-initiated audit that makes external web calls, not something to fire
opportunistically.

Report-only, like both existing skills. It proposes edits; it does not make them.

Steps, each ending in a verdict backed by evidence lines:

1. **Ground truth** — read `agent.py:165` for the model actually configured. Never
   assume.
2. **Rate drift** — fetch the pricing page; diff every entry in `MODEL_PRICES` against
   it. Any mismatch is a **finding**, because it silently corrupts every downstream
   figure. This is the check that would have caught the 6.5x.
3. **Coverage** — assert the configured model has a `MODEL_PRICES` entry, and that no
   model appearing in the last 30d of `turns.jsonl` is unpriced (consumes A2's marker).
4. **Spend rollup** — 30d by scope via `summarize_usage`; report total, per-scope split,
   cache hit rate, and (post-B) reasoning share of output.
5. **Model-landscape check** — live `models.list` against the key; flag newly available
   models and any configured-model deprecation notice. Note that shutdown dates in
   Google's table are *earliest possible*, not scheduled.
6. **Comparison table** — recompute the 30d bill under each priced model at current
   token volumes. State the two traps explicitly: cache-less models do not deliver
   their sticker discount, and holding output constant misprices models whose default
   `thinking_level` differs.
7. **Verdict** — one of: rates correct + model appropriate; rates stale (list them); or
   model worth revisiting (with the delta).

Include the working `models.list` and rollup snippets from this session so the skill is
executable rather than aspirational.

### C3 — doc sync

There is **no index of Claude Code skills anywhere in the repo** — verified: neither
`CLAUDE.md` nor `DEVELOPMENT.md` mentions `.claude/skills/`, and the only reference in
`docs/` is a passing one to `code-review` in `DEPLOY.md:159`. So this is not a
row-append.

Cheapest honest option: nothing. The skills are self-describing and Claude Code
discovers them. Do **not** create a three-row table that then rots.

If a pointer feels warranted, one line in `CLAUDE.md`'s "Key Files to Know" table
noting that `.claude/skills/` holds dev-session skills is the whole change — and it
should be a separate decision, not smuggled in with slice C.

### Verify

Run `/cost-review` against the current tree **after A and B have landed**. It must
independently reproduce the numbers in this document's baseline table. If it cannot,
the skill is wrong — this doc's figures were derived by hand from the same sources and
are the fixture.

**Done when** a clean run reports "rates correct" (A having fixed them) and a
deliberately corrupted `MODEL_PRICES` entry makes step 2 fail loudly.

---

## Sequencing & house rules

Branch: `feat/cost-telemetry` off `main`. One commit per slice, so any slice reverts
alone.

```
A ──► B ──► C
│     │     └── consumes both; fixture is this doc's baseline table
│     └── forward-only data; land early to start the baseline
└── retroactive; unblocks every cost claim in CONTEXT_HANDLING_PLAN.md
```

- **A and B each need an owner restart** before the live `/usage` reflects them. Claude
  cannot restart the service — after each slice, ask, then verify from the journal and a
  Telegram round-trip. Never infer service state.
- **No commit without approval.** Implement, verify, summarize, then ask.
- Slice C touches no runtime code and needs no restart.
- After B lands, let **two weeks** of reasoning-token data accrue before drawing any
  conclusion about `thinking_level`. A day is not a baseline.

## Checklist

- [x] A1 — corrected `MODEL_PRICES` (6 models priced) + source dated 2026-07-29
- [x] A2 — unpriced-model marker through `_empty_bucket` / `summarize_usage` / `_row_line` / `format_usage_table`; rendered on the totals line too, since single-bucket rollups skip the per-row breakdown
- [x] A3 — audio-rate limitation documented above `MODEL_PRICES` (adjacent to the table it constrains) rather than the module docstring
- [x] A4 — `CONTEXT_HANDLING_PLAN.md` baseline restated: the whole dollar block was wrong, not just the `:41` monthly figure
- [x] A5 — `/usage` rendering: real `- ` list items (fixes app-channel line collapse), `**bold**`, split totals block, magnitude-aware `_usd()`
- [ ] A — **restart + `/usage week` verified live** (needs the owner)
- [ ] B1 — `reasoning_tokens` captured in `record_turn_start` / `record_llm_call`
- [ ] B2 — rolled up + conditionally rendered in `usage.py`
- [ ] B3 — `OBSERVABILITY.md` schema block + prose paragraph
- [ ] B — restart + non-zero `reasoning_tokens` on a fresh turn verified live
- [ ] C1 — boundary against `heartbeat-assert` settled (and its description trimmed if needed)
- [ ] C2 — `.claude/skills/cost-review/SKILL.md`
- [ ] C3 — skill listed wherever the other two are
- [ ] C — clean run reproduces this doc's baseline; corrupted-rate run fails loudly
- [ ] Two-week reasoning-token baseline accrued → revisit the upgrade question

---

## Appendix — the upgrade question

Not part of this plan; recorded so the context is not lost.

The configured model, `gemini-3-flash-preview`, is **neither the cheapest nor the most
capable** option the key can reach — it is 4th of 5 on price. At current volumes:
`gemini-3.1-flash-lite` $10.92/30d · `gemini-2.5-flash` $13.92 · `gemini-3.5-flash-lite`
$20.34 · **current $21.83** · `gemini-3.6-flash` $63.75.

Nothing forces a move: `gemini-3-flash-preview` has **no announced shutdown date**
(unlike `gemini-3-pro-preview`, retired 2026-03-09). The real blockers on any move to a
3.x-latest model are behavioral, not commercial — `temperature` is accepted but
**silently ignored**, which nullifies `agent.py:166`'s `temperature=0.2` and its
"deterministic tool usage" rationale; and `thinking_level` replaces it as the control
surface. Neither can be evaluated responsibly until slice B is measuring.

Revisit after the two-week baseline, and after
[CONTEXT_HANDLING_PLAN.md](CONTEXT_HANDLING_PLAN.md) WS2/WS3 — halving heartbeat context
changes every row in that comparison.
