# Review — CONTEXT_REDESIGN_PLAN: fidelity to research, soundness of design

**Reviewer angle:** does the plan follow from the evidence, and is the design sound.
**Read:** the plan; PROBLEMS.md; reports LAYERED_MEMORY, COMPACTION_AND_DREAMING, TOOL_AND_SKILL_CONTEXT,
DEVIN_MEMORY; research_notes (openclaw_hermes, jarvis_baseline, compaction, dreaming, spot reads of the
rest); `agent.py` (`_add_and_trim`, `build_system_prompt`), `prompts/heartbeat.md`, staging `HEARTBEAT.md`.
No web access used.

## Verdict

The plan's architecture follows the research faithfully, and its strongest choices are well supported:
code-anchored compaction, op-based consolidation with quote checks, stateless ticks, and instrumenting
first. The weak points are at the joins between slices. The nightly cursor creates a daily
"forgetting boundary" that neither compaction nor the consolidator covers, the backstop trim evicts the
compaction summary first, and S4/S5 remove inputs (daily logs, notes, other tasks' sends) that live
heartbeat tasks still depend on. §9 measures cost but defines no reading that would show a slice
*failed*, which repeats the E3/E9 pattern PROBLEMS.md was written to stop.

---

## Blocker

### B1. The nightly cursor creates a daily forgetting boundary (challenges settled decision C3)

**Where:** §4 "Source" ("re-derived from episode rows since the last consolidation … only the previous
record's open requests are carried forward and re-verified"); §4 "Checks" (quotes must be a substring
of an owner row *in the span*); S5 "Hot-path writes narrowed"; S4 tick context; §5 "Inputs".

**Evidence.** COMPACTION_AND_DREAMING "A compaction design for Jarvis" justifies the horizon by saying
the consolidator "already owns durable memory", so the summary "never needs to carry anything older
than the last consolidation run". But the same report's consolidator prompt is built to *drop* exactly
the context in question: "Durable only. Drop anything tied to 'this time / this trip / today / tonight /
tomorrow'" (cookbook ephemera lexicon). LAYERED_MEMORY L3 says the latency gap between a fact being
stated and the nightly writer committing it is "largely fill[ed] already" by "the existing thread window
plus today's daily log". The plan removes the second half of that: daily logs move to the nightly job
(S5), and the plan does not say what either scope injects in its place (today `build_system_prompt`
injects today's log into user scope and yesterday's into heartbeat scope, `agent.py:439-447`).

**Failure in concrete terms.** At 22:00 the owner plans tomorrow's trip legs, says "don't ping me before
10 tomorrow", and asks a question Jarvis defers. The 03:00 consolidator correctly drops all three as
ephemeral, and the cursor advances. The first compaction of the morning re-derives from rows after
03:00 only. The rule, the plan and the deferred question leave L1 with no summary, no daily-log
bridge and no durable entry. This is the silent forgetting S3 is meant to end, moved to a fixed time
of day.

**Internal contradiction.** A carried-forward open request asked before the cursor cannot pass the
"substring of an owner row in the span" check, because its row is outside the span. So either every
cross-midnight open request is dropped at the first morning compaction, or the check is relaxed and a
carried request can persist indefinitely, which is the hermes revival pattern (#41607/#38364).
The plan defines neither.

**Fix.** Make the compaction horizon `max(cursor, now − 36h)` or "since the start of yesterday, Israel
time", not the cursor. Validate carried open requests against an owner row anywhere in the episode
store, and expire them after N days with a line in the record. State explicitly what each scope injects
after S5 (for example, yesterday's code-written daily log in both scopes). Add a staging check to S3: a
rule stated at 22:00 is still honoured at 09:00 after a nightly run and a morning compaction.

---

## Major

### M1. The backstop trim evicts the summary first, and "nothing is lost" assumes a store the plan allows to fail

**Where:** §4 "When" ("a skipped compaction costs one plain trim and nothing is lost (L2 has it)"),
"Placement" (one user-role message at the head), §3 "Write path" ("A failed store write never fails the
turn").

**Evidence.** `_add_and_trim` treats consecutive `HumanMessage`s as one turn's input
(`agent.py:148-158`). A head-of-window summary therefore joins the oldest kept turn. When the 50-message
backstop fires, which is exactly the case where compaction was skipped, stale or thrash-guarded, the
summary is the first thing cut, with no replacement. Separately, the "L2 has it" guarantee depends on
store writes the plan makes non-fatal. hermes #109687 (LAYERED_MEMORY "Operational incidents") is a
store that "keeps serving while silently dropping session writes". The S1 integrity check *detects*
that after the fact. It does not stop compaction from discarding messages that never reached L2.
hermes #97321 (the summary inserted twice by a race) is a further sign that summary placement is fragile.

**Fix.** Make the reducer summary-aware: keep a message tagged as the compaction record pinned at the
head through any trim, and re-render it from L2 if it is missing. Before compaction cuts messages, check
that their turn IDs exist in L2. If any are missing, write them first or skip the cut. Add a test that
the backstop trim preserves the record.

### M2. Hot-path writes are guarded only by prose, and the consolidator is told to protect them

**Where:** S5 "Hot-path writes narrowed" (an AGENTS.md wording change); §5 Inputs ("today's hot-path
writes as a git diff"); report prompt rule "Entries written today at the owner's request stand unless a
LATER episode contradicts them".

**Evidence.** The research's own headline is that prose-only rules fail: `deactivate_skill` was called 2
times against 90 activations (TOOL_AND_SKILL_CONTEXT), and F1/F2 make the same point. `write_memory`
stays a general always-bound tool, and nothing in code ties a write to an owner request. The diff is
labelled "written at the owner's explicit request" whether or not one existed. A write that follows a
web fetch (LAYERED_MEMORY: OpenClaw taints assistant output after network-sourced tool results) or a
model self-initiated write therefore lands unvalidated in injected USER.md. The consolidator is then
instructed to *keep* it, and it bypasses every check §5 applies: owner quote, 240 characters, relative
dates, instruction-text scan.

**Fix.** Have code stamp each hot-path write with its turn ID and the owner message that started the
turn. Run the consolidator's quote, length, date and secret checks on the previous day's hot-path diff.
A hot-path entry that fails them becomes a `flag` in the morning note, not a protected entry. Measure
hot-path write count per day in §9. If it does not fall after S5, the prose narrowing failed.

### M3. Over-cap rejection forces destructive restructuring onto the hot path

**Where:** S2 ("An over-cap write is rejected with … 'merge, move or archive first'"; verify "the model
recovers in the same turn"); S5 / §5 Application.

**Evidence and why it matters.** The plan's thesis (LAYERED_MEMORY conclusion) is to move memory
rewrites off the hot path because "models that rewrite memory prose destroy entries nobody asked them to
touch". S2 ships three slices before the consolidator and instructs the live model to merge or archive
USER.md mid-turn, unvalidated, with no loss limit. It is also verified as the *desired* outcome. After S5
the conflict persists: at cap, a "remember this" request triggers a hot-path rewrite. Nightly, a USER.md
at cap has every op rejected on any night whose net change is positive. The stateless consolidator
cannot learn from that, so it stalls silently night after night. That is the OpenClaw "promoted 0 for
weeks" shape (#121232), in a new place.

**Fix.** On the hot path, an over-cap "remember this" write should go to an overflow topic file plus a
`flag`, and never trigger a restructure. Give the consolidator an explicit "at cap" mode in which it is
asked for merges and removals first. Alarm in the morning note and telemetry after K consecutive nights
with a file's ops rejected.

### M4. Rejected or skipped ops are lost when the cursor advances

**Where:** S5 ("a changed file's ops are dropped and reported"; "cursor advances after the commit");
§5 Application ("any file that would break a hard limit has all its ops rejected").

**Evidence.** DEVIN_MEMORY suggested-change #1 says "drop that file's ops … **Retry on the next run**".
The plan drops the retry. With a single global cursor that advances after a successful commit, every
op dropped for a concurrent edit, a cap breach or the 25% loss limit has its evidence behind the cursor
and is never reconsidered. The weekly replay (S6, last slice) is the only path back, and it is
designed for patterns, not retries.

**Fix.** Use per-file cursors, or advance the global cursor only to the earliest evidence episode among
dropped ops. Alternatively, persist dropped ops to a pending list the next run receives. Report "N ops
carried over" in the note.

### M5. Expiry candidates as defined match nearly every entry, so the activity gate never closes

**Where:** S5 "No model call on a night with no new owner messages and nothing expired"; §5 Inputs
("code-computed expiry candidates"); the report defines them as "entries containing an absolute date
that has passed".

**Evidence and why it matters.** The plan requires every consolidator entry to end "(observed
YYYY-MM-DD)", which is a past absolute date by construction. Durable facts also carry past dates
(birthdays, start dates, "since 2024-03"). So the candidate list is effectively the whole of USER.md
and the topic files every night, and "nothing expired" is never true. The gate never closes. Every
night, the model is handed the whole profile labelled as expiry candidates, which nudges it toward
`remove`, the one op LAYERED_MEMORY says "no system still defends" applying destructively. The only
backstop is the 25% loss limit (see M6).

**Fix.** Define expiry in code: a date inside the entry body (not the observed suffix) that is in the
past *and* attached to a future-tense or one-time marker ("until", "trip", "stay", "booking"). Or have
writers tag time-bounded entries with an explicit `(until YYYY-MM-DD)`. Exclude the observed suffix
from date matching.

### M6. The quote check is a substring test, so it does not show the quote supports the entry

**Where:** §4 Checks (open requests and rules are substrings of owner rows); §5 Output ("every op
quotes the owner's words with an episode ID"); D4 ("Inferences: never written to USER.md").

**Evidence.** The report presents the check as stopping "both attribution laundering and invented
asks". It does neither when the quote is short: "yes", "ok", "sounds good" or a pasted article's
sentence are all substrings of owner rows. `kind: stated | inferred` is self-reported by the model, so
D4's barrier depends on the model labelling its own inference honestly. DEVIN_MEMORY records that the
reactions' central worry was exactly this ("how are those inferred memories validated…", "the model
that wrote an inferred memory will rubber-stamp it"). Owner rows also contain forwarded or pasted text
(emails, web snippets), and the plan has no provenance for that. LAYERED_MEMORY "Risks" says injection
"calls for provenance tagging at L2 write time".

**Fix.** Require a minimum quote length (for example ≥ 15 characters or ≥ 4 words), and require a
lexical overlap between the quote and the entry text above a threshold. Otherwise downgrade the op to
`flag`. Tag owner rows that are forwards or pastes (channel metadata, or length or quote heuristics)
as non-evidence. Count downgrades in the note.

### M7. The stateless tick drops inputs live tasks depend on, and the "read-only" rule contradicts the task contract

**Where:** S4 ("A tick sees: core memory, the due task blocks, each due task's last output, today's user
chat slice, tick rules"); §2 L5 ("read-only except notify, triggers, confirmation"); §6.

**Evidence.**
- *Notes files.* `prompts/heartbeat.md` step 1 has every task read `heartbeat/<task>.md`, and steps
  2–3 *write* to it. `running-post-check` reads `target_date` from `running_prep.md`, another task's
  notes, and writes "checked" "to avoid double-nudging". Per-task "last output text" does not carry
  cross-task state or the dedupe marker. If L5's "read-only" is applied, those tasks break. If it is
  not, the §2 table is wrong. S4's checklist does not implement it either way.
- *Daily log.* `running-evening-prep` step 1 says "Check the current daily log … for 'Running Schedule
  Update' notes". After S5 no current daily log exists during the day. S4 also drops the yesterday's
  log that heartbeat scope injects today.
- *Other tasks' sends.* On the persistent thread a tick could see what other tasks sent earlier the
  same day. Fresh context shows only the task's own last output and the owner chat, so overlapping
  tasks (readiness vs weekly attendance, both reporting health trends) lose cross-task deduplication.
- *A6 is listed as addressed,* yet notes (all eight together ≈325 tokens, PROBLEMS A6) are still
  reached by `read_memory` round-trips that re-send the context. *A10 is listed as addressed,* yet S2
  adds MEMORY.md to the heartbeat prompt.

**Fix.** Inject due tasks' notes files directly (tiny, and it actually closes A6). Inject today's
delivered notifications from `notifications.jsonl` (already the Outbox log). Replace "read-only" with
an explicit allowlist (notes files, notify, triggers, confirmation, skill-owned logging). Rewrite the
task bodies that reference "the current daily log" in the same deploy as S5. Drop A10 from "Problems
addressed" or say how it is addressed.

### M8. S1 removes a tool that a live heartbeat task calls until S5

**Where:** S1 ("`search_history` … replaces `get_chat_history` and `get_notification_history`");
Deploy steps (HEARTBEAT.md edits listed only for S5 and S6).

**Evidence.** The `daily-log` task body calls `get_notification_history(limit=30)` (staging
HEARTBEAT.md), and that task survives until S5. Between S1 and S5 every daily-log run calls a tool that
no longer exists. F1 says the docstring or task text is the behaviour driver, so the model will keep
trying.

**Fix.** Either keep `get_notification_history` until S5, or add an S1 hand edit of prod HEARTBEAT.md to
the Deploy steps. Grep HEARTBEAT.md, notes files and SKILL.md bodies for every retired tool name as an
S1 checklist item.

### M9. §9 records cost but defines no failure, and omits the new jobs' own spend

**Where:** §9 Readings; each slice's "Verify".

**Evidence.** PROBLEMS E9 ("a target was set without checking the composition") and E3 (claims from
recall) are why §9 exists, yet the table has blank cells with no targets, no thresholds and no
quality rows. It has no "After S2" column, although S2 *adds* injected tokens (MEMORY.md in both scopes),
and no "After S6" column. COMPACTION_AND_DREAMING's conclusion names the readings that settle its open
choices, "validator rejection counts, zero-promotion reasons, and post-compaction re-activation rates",
and none of them appear in §9. The new LLM work (compaction calls that re-read a day of rows, two
nightly calls, the weekly replay) has no spend row, so a slice could raise total cost while every row
shown improves.

**Fix.** Add rows, each with a stated failure threshold set from S0:
- compaction outcomes (ok / retried / fallback / skipped_stale);
- compactions per day and compaction input tokens per day;
- re-activations within 1 turn of a decay;
- cache-read share on the call after a compaction;
- consolidator ops applied and rejected by reason, nights with zero promotion and their reason, cursor
  lag, owner `/memory undo` count;
- hot-path writes per day;
- `search_history` calls per day (C2/C3 are capability goals, and nothing currently shows that recall
  happens);
- total spend per day including the new jobs.

For behaviour, define the failure signals per slice. For S3: count owner turns that restate something
already said ("I told you…") and revived tasks per week from transcripts. For S4: any task acting on a
different day than its pre-S4 baseline.

### M10. Skill decay has no go/no-go tied to the S0 cache measurement

**Where:** §7 ("Decay … is the main lever: about 5–8k tokens per user call"); §8 "Settled by
measurement … whether tool-set changes cost enough cache to matter"; S3.

**Evidence.** TOOL_AND_SKILL_CONTEXT "Decision 3" says the exchange rate is unknown. "If the tool block
turns out to sit before the system instruction and to be almost fully cached, the cost case for B
weakens to the accuracy case alone". It also notes that today's sticky set is "accidentally
cache-friendly" (the tool set changed in 4 of 314 turns). The 5–8k figure is characters ÷ 4 over golden
snapshots with an unexplained 6.8k vs 9.6k discrepancy. The plan states it as a fact and ships decay in
S3 whatever S0 finds.

**Fix.** Write the decision rule into S3 before S0 runs. For example: ship decay if
(uncached schema tokens saved per call × calls) exceeds (re-cache cost per compaction + re-activation
round-trips) at the S0-measured price. Otherwise ship only the S6 hygiene. Quote §7's number as an
estimate.

---

## Minor

- **m1. The plan drops the C3 overflow fallback it claims to adopt.** §4 says "never a summary of a
  summary", but the report's C3 recommendation is "(b), with merge as overflow fallback", and §8 adopts
  "all recommendations". On a travel day (up to 26 tool calls per turn, 397 `manage_itinerary` calls in
  two weeks), a 40-message trigger fires after nearly every turn. Each compaction re-reads the whole day
  since the cursor, so cost grows quadratically. State the span token budget, the fallback, and which
  model compacts. The report leaves "whether the summary model is the chat model" open.
- **m2. The compaction input filter is unstated.** §5 lists the consolidator's exclusions. §4 does not
  say compaction excludes earlier `compaction` and `system_record` rows, heartbeat rows and full tool
  bodies. Without that, "never a summary of a summary" is not enforced.
- **m3. The verbatim owner-messages anchor can revive tasks.** Re-showing cancelled asks ("book X" …
  "never mind") verbatim is the topic-overlap revival hermes fixed (#41607). OpenClaw added code
  rejecting "summaries that revive superseded tasks" (#123737 after #123668, openclaw_hermes notes), and
  LAYERED_MEMORY recommends copying it. S3's verification tests a finished request, not a *cancelled*
  one. Add that case, and render cancelled asks with their reversal beside them.
- **m4. The #118863 probe was dropped.** Report C5 made user-role placement conditional on "staging runs
  a probe for the hermes #118863-style failure". The plan keeps the placement without the probe, and the
  prod "check" of the mirror is not described. A second system-authored user-role block raises the
  exposure. Add the probe to S3's staging checks.
- **m5. "Old" tool results in the kept tail are undefined.** Clearing results inside the last 3 turns
  can blank the result the owner is about to ask about. Anthropic keeps the last 3 tool pairs
  (LAYERED_MEMORY L1). Specify: never clear the most recent turn's results.
- **m6. The 25% loss limit is per run and per file.** Across nights it compounds (seven nights at 24%
  can remove about 85%). On small files it blocks legitimate forgets: one removal from a 3-entry file
  is 33%, so all that file's ops are rejected, every night. Add a rolling 7-day loss bound and exempt
  ops backed by an explicit owner forget request.
- **m7. Undo and visibility are thinner than the decisions imply.** `/memory undo` reverts only the
  *last* consolidation commit, but a bad removal is often noticed days later. The report caps the note
  at about 8 highlights, so removals past 8 are invisible. List every `remove` and `supersede` in full,
  and let undo take a date or commit. Define behaviour when an intervening hot-path commit conflicts
  with the revert.
- **m8. Git details are missing.** `threads.sqlite*` must be in `.gitignore`, not just deny-listed.
  Owner hand edits are documented as a normal path (CLAUDE.md: edit SOUL.md and HEARTBEAT.md directly)
  and leave a dirty tree that compare-and-commit does not see. Commit a dirty tree as "owner edit"
  before reading inputs.
- **m9. The episode store becomes the only record of history with no backup.** Retiring
  `chat_history.jsonl` removes the redundancy that would survive a hermes-#109687-style loss. The
  integrity check only detects it. Add a nightly `.backup` of `episodes.sqlite`, which is cheap at
  1.4 MB per quarter. Writing only at turn end also loses the whole turn, including the owner's message,
  on a crash or SIGTERM mid-turn. Write the inbound row at turn start.
- **m10. `search_history` returns "full rows" with no size bound.** Full tool results (travel payloads)
  can flood a call. Web-derived tool results also resurface later without their original context,
  which is a delayed injection path the `read_memory` "data, not instructions" clause does not cover.
  Cap returned bytes per row with an expand option, and give the search tool's docstring the same
  data-not-instructions clause.
- **m11. Warm start re-creates the ratchet per task.** "Skills warm-start from the task's last run" keeps
  any skill activated once, for as long as the task runs. The report's option D (task-declared skills)
  avoids this. Warm-start only the skills actually *called* last run.
- **m12. "Last output text" is a one-deep F2 channel.** Model-authored text is replayed into the next
  run. That is far smaller than today's risk, but S4's verification should include a test that a
  corrected task body wins over its last output.
- **m13. Some "problems addressed" claims are unsupported.** Compaction runs *post-turn*, so it does
  not touch A5 (within-turn tool-result growth). #106 (a tick acted without notifying) gets only
  searchability, not the "silent-outcome carryover" LAYERED_MEMORY L5 recommends. The "~90k tokens a
  run" figure for daily-log (§1) has no source. jarvis_baseline says per-task cost "is not measurable
  from telemetry". Correct or source these, per E3.
- **m14. Consolidator scope "agent-written topic files" has no code definition.** State the allowlist
  mechanism, for example files whose git history has no owner-edit commit, or an explicit list. Without
  it the scope rule is prose.
- **m15. The S4 verify criteria are hard to read.** "The same ticks act on the same days" across a week
  is confounded by paused and gated tasks (E10; most staging tasks are paused). "No briefing gets
  blander" needs a *before* sample captured during S0. Save a week of pre-S4 notification texts in S0.

---

## Things the plan gets right

- **The order is sound.** Instrumenting (S0) and a durable episode store (S1) come before anything that
  discards context or moves writes. That is the lesson the hermes #109687 and OpenClaw flush-coverage
  incidents teach.
- **Code holds the anchors.** Owner messages, identifiers and live triggers come from stores that own
  them, not from model recall. This is the single best-supported idea in the research (Cline, hermes
  anchors, the report's conclusion).
- **The consolidator is about as strict as the evidence justifies.** One JSON call, handle-mapped ops,
  quote evidence, no file creation, SOUL.md read-only, inferences reduced to questions, and no append
  fallback (OpenClaw #142393). The plan correctly holds this line against Devin's looser design.
- **Compare-and-commit, the activity gate and a defined first run** were taken from the Devin review.
  These are cheap fixes for real gaps.
- **Stateless ticks are well grounded.** No reference system replays a third persistent thread, A4 is
  measured, and the reversal of the documented choice is stated openly rather than buried.
- **Decay is tied to compaction.** This makes orphaned `functionCall`s impossible by construction
  instead of depending on an untested Gemini behaviour, and there is a re-activation check modelled on
  Devin's CLI bug.
- **Code composes the morning note, with "promoted 0 because …".** This directly answers OpenClaw's
  silent-stall history (#121232, #164923).
