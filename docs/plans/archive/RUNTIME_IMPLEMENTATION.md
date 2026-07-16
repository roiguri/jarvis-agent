# Runtime Refactor — Gradual Implementation Plan

The step-by-step rollout for building the architecture in
[docs/architecture/RUNTIME.md](../../architecture/RUNTIME.md): the tool registry,
scoped loading, same-turn skill activation, and the `create_agent` →
`StateGraph` migration.

This is a **high-risk refactor**: it rewrites the agent loop, the tool
surface, the state schema, and the system prompt — every one of which can
silently regress LLM behavior in ways type-checks and unit tests do not catch.
The plan's entire purpose is to make the risk *incremental*: each step is small,
independently verifiable, and leaves Jarvis fully working. You can stop after
any green step and the branch is shippable.

---

## Ground Rules

1. **One branch, one step per commit.** Work on a dedicated refactor branch
   (e.g. `refactor/runtime`). The branch is merged only when the whole sequence
   is complete and green — so the architecture doc is written in the present
   tense and intermediate commits may describe partially-built structure. That
   is fine; nothing ships until the end.
2. **Each step keeps Jarvis working.** No step is allowed to leave the agent
   broken "until the next step fixes it." If a step can't stand alone, it's too
   big — split it.
3. **Verification is mandatory per step**, following CLAUDE.md's inner loop.
   After every step the implementer asks Roi to restart the service and run the
   step's Telegram checks — Claude cannot restart `jarvis.service` and must
   never infer or fake service state. Report exactly what was observed.
4. **Rollback = revert the step's commit.** Because every step is standalone and
   green, reverting one commit returns to a working state. No step depends on a
   later step to be correct.
5. **Behavior parity is the bar until Step 6.** Steps 1–5 must produce
   *identical* LLM behavior to today (same tools visible, same prompt, same
   answers). They are pure restructuring. Behavior only changes at Step 6, in
   isolation, so any regression is attributable.
6. **Capture a baseline first** (Step 0). You cannot detect a regression you
   never measured.

---

## Step 0 — Baseline & safety net

**Goal:** know what "unchanged" means before changing anything.

- Create the refactor branch.
- Record, on `main`'s current code, a behavior snapshot to diff against later:
  - 6–8 representative Telegram prompts and their answers (one pure-chat, one
    media query, one media action, one fitness log, one memory write, one
    heartbeat tick, one multimodal). Save verbatim in a scratch file (not
    committed).
  - The full assembled system prompt string and its token count (log it once
    from `agent.py`).
  - `git rev-parse HEAD` of the baseline.
- Confirm `threads.sqlite` is backed up (copy the file) so a botched
  checkpoint-schema step can be restored.

**Verify:** snapshot file exists; baseline prompt token count recorded.
**Rollback:** n/a (no code change).

---

## Step 1 — Tool registry, additive and unused

**Goal:** introduce `tools/registry.py` and `@tool_register` metadata
*alongside* the existing flat `jarvis_tools` list. The agent still consumes the
old list; the registry is built but not yet wired in.

- Add `tools/registry.py`: `@tool_register(namespace=..., destructive=...)`,
  `get_tools()`, `find()`, `compact_skill_list()`, `import_all()`.
- Decorate existing tools in place (no file moves yet) with `@tool_register`.
  `namespace="core"` for the Tier-1 set; `"media"` / `"fitness"` for Tier 2.
- Add a startup log line: `registered N core tools, M skill tools across K
  namespaces`.
- Assert at import time that the registry's full tool set equals the existing
  `jarvis_tools` set (a temporary guard; remove at Step 9).

**Verify:** restart; log shows the registry counts and the equality assertion
passes; all baseline prompts still answer identically (the agent is still on the
old path, so this should be trivially true).
**Rollback:** revert; registry was unused.

---

## Step 2 — Physical file reorganization

**Goal:** move tool modules into `tools/core/`, `tools/media/`,
`tools/fitness/` without changing behavior.

- `git mv` each module (preserves blame). Split `media_tools.py` /
  `fitness_tools.py` per the RUNTIME.md layout.
- Keep `tools/__init__.py` re-exporting the same names so `jarvis_tools` and any
  external imports still resolve. `import_all()` imports the new packages.
- No logic edits inside moved files beyond import-path fixes.

**Verify:** restart; registry counts unchanged from Step 1; baseline prompts
identical; `grep` shows no remaining imports of old module paths.
**Rollback:** revert the move commit.

---

## Step 3 — `StateGraph` migration, full tool set, old prompt *(highest-risk step — keep it isolated)*

**Goal:** replace `create_agent` with the hand-rolled `StateGraph` loop, but
bind the **entire** tool set every call and keep `_BASE_SYSTEM_PROMPT`
verbatim. This isolates the runtime-engine risk from the scoping risk — the only
thing that changes is *how the loop is built*, not what the LLM sees.

- Implement `build_graph()` (llm node + tool node + conditional edge),
  compiled once at startup, replacing `agent_executor`.
- llm node binds `registry.get_tools()` returning **all** tools (no filtering).
- System prompt: still the existing `system_prompt` string, prepended as a
  `SystemMessage` in the node.
- Custom tool node executes tool calls and returns `ToolMessage`s — but does
  **not** yet interpret any activation sentinel (no `activate_skill` exists
  yet). `JarvisState` is unchanged.
- `ask_jarvis()` keeps its exact signature; internally routes to the new graph.
  Preserve the streaming/`stream_mode="values"` response parsing and the
  multimodal `HumanMessage` construction unchanged.

**Verify (heaviest of the plan):** restart; run *every* baseline prompt and diff
answers against the Step 0 snapshot — including the multimodal and heartbeat
cases. Confirm checkpoint persistence (send a message, restart, reference it).
Confirm `PruningSqliteSaver` still keeps one row per thread. Confirm the
`_add_and_trim` window and `_strip_media_blobs` still apply.
**Rollback:** revert to the `create_agent` commit. This is why Step 3 changes
*only* the engine — a regression here is unambiguous.

---

## Step 4 — State schema: `scope` + `active_skills`, still not gating

**Goal:** add the two new `JarvisState` fields and their reducers, set `scope`
at the call sites, but **bind the full tool set regardless** of
`active_skills`. This de-risks the checkpoint-schema change on its own.

- Add `scope: str` and `active_skills` (with the set-merge reducer) to
  `JarvisState`.
- **Checkpoint migration:** existing `threads.sqlite` checkpoints predate these
  fields. All reads must use `state.get("scope", "user")` /
  `state.get("active_skills", set())` — never assume present. Verify an
  *existing* thread (created before this step) still answers correctly after
  the upgrade.
- `main.py` passes `scope="user"`; `heartbeat.py` passes `scope="heartbeat"`.
- `registry.get_tools()` still ignores `active_skills` and returns everything.

**Verify:** restart; a pre-existing thread (from before the deploy) continues a
conversation without error; new threads persist `scope`/`active_skills` across a
restart; baseline behavior still identical (tool surface unchanged).
**Rollback:** revert; `.get()` defaults mean old checkpoints are unaffected by
the revert too.

---

## Step 5 — `activate_skill` plumbing, still not gating

**Goal:** add `activate_skill` / `deactivate_skill` core tools and the tool
node's sentinel-dict handling that mutates `active_skills` — but
`get_tools()` *still* returns the full set, so activation has no effect on the
surface yet. This proves the state-mutation path in isolation.

- Implement the two meta-tools returning `{"_activate": [...]}` /
  `{"_deactivate": [...]}`.
- Tool node interprets the sentinel and returns updated `active_skills` in its
  state delta; all other tools keep returning plain strings.
- Add them to the core namespace; they appear in the prompt.

**Verify:** restart; ask Jarvis to "activate the media skill", confirm via logs
that `active_skills` becomes `{"media"}` and persists to the next turn and
across a restart. Tool answers still identical (surface still full).
**Rollback:** revert; meta-tools removed, state path gone.

---

## Step 6 — Flip the gate *(the only intended behavior change)*

**Goal:** `registry.get_tools(scope, active_skills)` now actually filters —
core always bound, Tier-2 hidden until its namespace is active. The tool node's
`find()` returns `None` for inactive-skill calls with the "activate the skill
first" `ToolMessage`.

- Enable filtering in `get_tools()` and `find()`.
- Keep the old `_BASE_SYSTEM_PROMPT` text for now (Step 7 replaces it) so this
  step changes *only* tool visibility, not prompt copy.

**Verify:** the behavior-change checks from RUNTIME.md:
- Pure-chat prompt → no media/fitness schemas in the logged bound-tool list.
- "Queue Severance season 2" → model calls `activate_skill(["media"])` then
  `search`/queue **in the same turn**; queue works.
- Heartbeat tick with a fitness task → it activates `fitness`, acts, and a
  follow-up *chat* turn can see the result via the daily/notification log.
- Diff token count of the bound-tool payload vs Step 0: substantially lower
  when no skill is active.
**Rollback:** revert this one commit → back to full-surface (Step 5 behavior),
still working.

---

## Step 7 — Generated prompt, delete hand-synced tool prose

**Goal:** replace the tool-enumerating body of `_BASE_SYSTEM_PROMPT` with the
generated `## Available skills` / `## Currently active` block from
`registry.compact_skill_list(scope, active_skills)`. Behavioral/scheduling rules
that are *not* tool catalogs stay (they move to the prompt builder; final
file-driven form is the memory/identity layer's concern, not this plan's).

- Prompt builder assembles: identity/rules preamble + scope framing + compact
  skill block. Core schemas come from `bind_tools`, not prose.
- Delete the per-tool description list from the old prompt.

**Verify:** restart; logged prompt no longer enumerates tools; token count drops
again; baseline prompts still answer correctly (the model now learns Tier-2
tools exist via the compact list, not prose).
**Rollback:** revert → previous prompt text restored.

---

## Step 8 — Scope framing + shared awareness — **DONE in Phase 3**

> **Status: delivered by Phase 3 (file-driven identity), not Phase 2.** It was
> deferred out of Phase 2 because an interim Python-constant framing block
> would have been throwaway — Phase 3's job was precisely to move that content
> out of code into files. Phase 2 ended at Step 7 + Step 9; this work landed
> in Phase 3 Steps 7–8.
>
> **As delivered (Phase 3):**
> - Per-scope framing: `_USER_FRAMING` (conversational) vs `_HEARTBEAT_FRAMING`
>   (terse) selected by `scope` in `build_system_prompt`; the
>   `[NO_ACTION]`/tick-behavior wording moved into `prompts/heartbeat.md`,
>   appended **only** on heartbeat-scope turns (no longer carried on user
>   turns).
> - `user` scope auto-loads today's daily log; `heartbeat` scope auto-loads
>   `HEARTBEAT.md` + the last 3 daily logs — assembled by the builder.
> - Per-skill prompt fragments (issue #23): each skill's rules live in
>   `tools/<ns>/SKILL.md` and render only when that skill is active.
>
> **Awareness bridge:** unchanged and still valid — heartbeat writes the daily
> log + `notifications.jsonl`; `get_chat_history`/`get_notification_history`
> are core tools, and the user-scope prompt now also auto-loads today's daily
> log, so a chat turn can answer "what did you do earlier?" without a tool
> call.

---

## Step 9 — Cleanup & docs

**Goal:** remove scaffolding, finalize. Phase-2 closer (Step 8 deferred).

- Delete the flat `jarvis_tools` list and the Step-1 equality assertion;
  collapse `tools/__init__.py` to `registry.import_all()` + the
  `[tool-registry]` startup count log (kept — useful operational line).
- Remove the temporary verification traces: the `[prompt-size]` line and
  the `[tool-bind]` `TEMP-TRACE` line. Drop the now-dead
  `registry.registered_tool_names()`; fix stale registry/agent docstrings
  ("filtering not yet enabled" → enabled).
- Delete the Step-0 safety backup `/app/backups/threads.sqlite*.bak-*`
  (and the memory pointer that tracked it).
- Update CLAUDE.md + [DEVELOPMENT.md](../../../DEVELOPMENT.md) tool-package
  sections, "add a new tool" pointer, and System Prompt Architecture to the
  registry/`build_system_prompt` reality; record the Step-8 → Phase-3
  deferral in this plan and in RUNTIME.md.
- Note Step 2.5 (media split into per-integration modules) was inserted
  between Steps 2 and 3; issues #18–#23 filed for deferred/out-of-scope work.
- Final regression pass against the Step 0 oracle for unchanged cases;
  confirm intended changes (scoped surface, ~−50% prompt, −79% bound-tool
  tokens, same-turn activation) hold.

**Verify:** restart; clean boot; `[tool-registry]` count correct; `grep`
confirms no remaining `jarvis_tools` / `TEMP-TRACE` / old module paths;
oracle sweep semantically matches.
**Rollback:** revert; prior step was already green.

---

## Risk Register

| Risk | Step | Mitigation |
|---|---|---|
| Loop rewrite silently changes LLM behavior | 3 | Engine-only change with full tool set + old prompt; heaviest verification; single-commit revert. |
| Old checkpoints lack `scope`/`active_skills` | 4 | All reads via `.get(..., default)`; explicitly test a pre-existing thread; `threads.sqlite` backed up at Step 0. |
| Model fails to call `activate_skill` and gets stuck without tools | 6 | `find()` returns a `ToolMessage` instructing activation rather than erroring; compact skill list (Step 7) advertises what exists; verify the same-turn activate-then-use path explicitly. |
| Prompt-copy regression mistaken for scoping regression | 6 vs 7 | Tool-visibility change (6) and prompt-copy change (7) are separate commits, never combined. |
| Heartbeat loses visibility of its own actions in chat | 8 | Awareness uses existing daily/notification logs + core `get_notification_history`; explicitly tested cross-thread. |
| Can't verify because Claude can't restart the service | every | Verification steps are written for Roi to run; report observed output, never infer service state. |

---

## See Also

- [docs/architecture/RUNTIME.md](../../architecture/RUNTIME.md) — the target architecture this plan builds.
- [docs/plans/ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) — rationale, rejected alternatives, concurrency model.
- [CLAUDE.md](../../../CLAUDE.md) — deployment inner loop and hard rules.
