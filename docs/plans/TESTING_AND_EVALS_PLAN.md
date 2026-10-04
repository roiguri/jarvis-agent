# Testing and Evals — Plan

**Date:** 2026-10-04 · **Status:** drafted; slices not started.
**Goal:** a real test suite that runs in CI and catches logic regressions, plus an eval
harness that measures what the model *does* (tool choice, heartbeat decisions, safety rails),
which nothing measures today.

---

## Slices

- [ ] **T1 — pytest foundation.** Move the `scripts/test_*.py` harnesses into a top-level
      `tests/` package as native pytest, share their fakes, and add a required `tests` CI job.
- [ ] **T2 — Snapshot what the model sees.** Golden files for the system prompt per scope and
      for the scoped tool surface, so every prompt or docstring change is a reviewable diff.
- [ ] **T3 — Unit tests where bugs have been.** Heartbeat gate and parser, time boundaries,
      memory sandbox, message trimming, registry scoping, fitness math.
- [ ] **T4 — Eval runner + heartbeat cases.** Top-level `evals/`: scenario cases run through
      the real graph and real Gemini, graded on the trajectory; about 10 heartbeat cases.
- [ ] **T5 — User-scope evals + workflow.** Tool-choice and safety-rail cases, a local
      case-drafting helper, and an eval step in the `code-review` skill.

Each slice ships and is verified on its own. T1 comes first because every later slice puts its
tests in `tests/`. T2 and T3 can be done in either order. T4 needs only T1.

---

## 1. Where things stand

**CI (`.github/workflows/ci.yml`)** runs four guards: path isolation, channel-agnosticism,
slash-command reply layout and timezone anchors. They enforce architecture rules. None of them
tests behavior.

**Harnesses (`scripts/test_*.py`)** are hand-run scripts, each with its own `check()` /
`FAILS` helper. They are not in CI, by the policy in DEPLOY.md. All pass as of 2026-10-04:

| Harness | Checks | Time | Notes |
|---|---|---|---|
| `test_triggers.py` | 121 | 31s | fake LLM, fake Outbox, real scheduler |
| `test_turn_lifecycle.py` | 87 | 5s | fake LLM, fake Outbox/channel |
| `test_travel.py` | 235 | 2s | tool functions directly |
| `test_app_blocks.py` | 27 | <1s | fails unless `JARVIS_ROOT` is exported; the rest make their own |
| `test_context_mirror.py` | 18 | 3s | |
| `test_app_confirmation.py` | 8 | <1s | |
| `test_webhooks.py` | — | — | posts to a live server: an ops tool, not a test |

**The seams already exist.** A scratch `JARVIS_ROOT` isolates every state path (`config` binds
them at import). `agent.llm` can be swapped for a scripted `FakeLLM`, and there are fakes for
the Outbox and the channel. Both tracks build on these. No new seam in app code is planned.

**Gaps.**
- No coverage for `heartbeat_state` (only indirectly, through the triggers harness), the
  memory sandbox, fitness, media, google_health, github, or registry scoping.
- No behavioral coverage at all. `test_travel.py` says it outright: "Whether the model reaches
  for the right tool with the right arguments is judged by hand, in chat." `AGENTS.md`,
  `heartbeat.md`, `SKILL.md` bodies and tool docstrings control most of the behavior, and
  nothing guards them.
- Telemetry can't see behavioral failures. In prod, 12,779 of 12,784 tool calls are
  `status: ok`, because tools return error strings rather than raise.
  `tool_calls.jsonl` also records only `args_size`, not the arguments.

**Where the bugs have been.** Of the 42 `fix` commits, the most-touched files are `agent.py`
(8), `heartbeat.py` (5) plus `heartbeat_state.py` (2), travel (4) and `tools/core/memory.py`
(3). Several more were day-boundary and timezone bugs. Nearly all are deterministic logic.

---

## 2. Decisions

- **D1 — Tests live in `tests/`, evals in `evals/`, never in `scripts/`.** `scripts/` is for
  operator and dev entry points. A test is not something you run by hand, and an eval is
  neither a test nor an ops tool. `scripts/ci/` keeps the four guards: they are static scans
  or app-boot checks that the pre-commit hook and `deploy.sh` call directly, not tests.
  (`check_command_replies.py` is closest to a test, but it stays because CI and the guard docs
  already treat it as a guard.)
- **D2 — The test suite is a required CI gate.** This reverses the DEPLOY.md line "the
  harnesses are deliberately not in CI". The suite needs no network and no secrets and takes
  about 45s. It is not in the pre-commit hook, for the same reason `command-replies` isn't:
  it boots the app.
- **D3 — Evals are on demand, never a CI gate.** They call the real model and are
  non-deterministic. You run them before merging a change to a prompt, a docstring, a
  `SKILL.md` or the model. Results are pass *rates* compared against a committed baseline.
- **D4 — One scratch root per test session.** `config` binds paths at import, so
  `tests/conftest.py` sets `JARVIS_ROOT` (and a dummy `GOOGLE_API_KEY`) before any project
  import. Isolation between tests comes from fixtures that reset state directories and
  restore swapped module attributes, plus a distinct thread id per test. No subprocess per
  file.
- **D5 — Eval tools are default-deny.** In an eval run, every tool outside an explicit
  local-safe allowlist is replaced by a recorder that returns the case's canned output. Local
  tools (memory, triggers, heartbeat tasks, travel, the fitness DB reads) run for real against
  the scratch root. Everything that reaches outside (media, github, google_health, web,
  anything that talks to Arbox) is stubbed. This matters because staging's Arbox credentials
  book real gym seats.
- **D6 — Evals grade the trajectory, and use a judge only for fuzzy qualities.** Assertions
  run on the tool calls in the scratch thread's `AIMessage.tool_calls` (which carry full args)
  and on the heartbeat ack. An LLM judge (Gemini, the project's provider) is used only for
  things like the tone of a notification. No external eval platform: OBSERVABILITY.md already
  rules out external systems, and the existing fakes do most of the work.
- **D7 — Committed cases contain no real personal data.** Cases are written or sanitized by
  hand. The drafting helper (T5) reads prod logs locally and writes drafts to a gitignored
  directory. The owner reviews each draft before it becomes a case.
- **D8 — Evals read the API key from the environment only.** The runner requires
  `GOOGLE_API_KEY` to be set by whoever runs it and never reads a `.env` file.

---

## 3. T1 — pytest foundation

**Layout.**
```
tests/
├── conftest.py            # scratch JARVIS_ROOT + dummy key before imports; shared fixtures
├── fakes.py               # FakeLLM, FakeOutbox, FakeChannel (deduped from the harnesses)
├── test_triggers.py
├── test_turn_lifecycle.py
├── test_context_mirror.py
├── tools/test_travel.py
└── channels/jarvis_app/   # channel-internal tests; exempt from the channel-agnostic guard
    ├── test_blocks.py
    └── test_confirmation.py
pytest.ini                 # testpaths = tests
requirements-dev.txt       # -r requirements.txt, pytest, hypothesis
```
`requirements-dev.txt` keeps test deps out of prod, so `deploy.sh`'s pip step is unchanged.

**Checklist.**
- [ ] `requirements-dev.txt`, `pytest.ini`, `tests/conftest.py` (root + key before imports,
      with an assert that `config.DATA_DIR` is under the scratch root, as the harnesses do now).
- [ ] `tests/fakes.py`: one `FakeLLM` (scripted replies or exceptions, records requests,
      exposes the bound tools), `FakeOutbox`, `FakeChannel`. Fixtures install them and
      restore the originals afterwards (`agent.llm`, the factory's outbox, `heartbeat.TURN_LOCK`).
- [ ] Convert each harness with `git mv` and then edit, so history follows the file. Each
      numbered section becomes a test function. Sections that depend on earlier state share a
      module-scoped fixture or are merged into one test; nothing is reordered silently.
      - [ ] `test_triggers` · [ ] `test_turn_lifecycle` · [ ] `test_context_mirror`
      - [ ] `test_travel` · [ ] `test_app_blocks` · [ ] `test_app_confirmation`
- [ ] **Parity check per file:** the number of assertions after conversion is at least the
      number of `check()` calls before, recorded in the PR.
- [ ] `scripts/test_webhooks.py` → `scripts/fire_webhooks.py` (it fires at a live server;
      the `test_` prefix was misleading).
- [ ] Guards: `check_channel_agnostic.py` swaps its two named `scripts/test_app_*.py`
      exemptions for the `tests/channels/` tree. `check_timezone_anchors.py` skips `tests/`
      and `evals/` (fixtures seed Israel-dated data), as it skips `scripts/` today.
- [ ] CI: a `tests` job (`pip install -r requirements-dev.txt`, `pytest -q`), added to the
      branch-protection required checks.
- [ ] Docs: the DEPLOY.md regression-gate section (D2), the CLAUDE.md layout (`tests/`,
      `evals/` as top-level concepts), harness references in `docs/architecture/TRIGGERS.md`,
      and DEVELOPMENT.md (how to run the suite).
- [ ] Verify: `pytest` is green locally and in CI. A deliberately broken assertion turns the
      `tests` job red on a throwaway PR.

---

## 4. T2 — Snapshot what the model sees

The prompt and the tool schemas *are* the behavior surface. A golden file makes any change to
them visible in review, including accidental ones such as a tool silently becoming always-on.

- [ ] `tests/fixtures/memory/`: a small fixture SOUL.md, USER.md, HEARTBEAT.md and daily log.
- [ ] `tests/test_prompt_snapshot.py`: `build_system_prompt` for the `user` and `heartbeat`
      scopes, with no skill active and with each skill active, against the fixtures and a
      pinned clock (monkeypatch the module's time helpers). Compared with
      `tests/golden/prompt_<scope>[_<skill>].md`.
- [ ] `tests/test_tool_surface.py`: for each scope and active-skill set, the tools
      `registry.get_tools` returns: name, namespace, destructive flag, docstring and args
      JSON schema. Written as `tests/golden/tools_<scope>[_<skill>].json`, plus an estimated
      token count per set (characters ÷ 4, labelled as an estimate) so growth in the
      always-on surface shows up in the diff.
- [ ] `pytest --update-golden` (a conftest option) rewrites the golden files. A mismatch
      fails with a unified diff.
- [ ] Verify: editing one tool docstring fails exactly one tool-surface test with a readable
      diff, and `--update-golden` clears it.

---

## 5. T3 — Unit tests where bugs have been

Ordered by bug history. `hypothesis` is used where the input space is a clock.

- [ ] `tests/test_heartbeat_state.py`: the HEARTBEAT.md parser (grammar, `| gate:`, a
      malformed task degrading to always-due rather than being dropped); `any_due` over
      cadence, both lattice measurements and `due:` windows, including property tests over
      arbitrary `last_run` / now pairs; fail-open on gate errors.
- [ ] `tests/test_time_boundaries.py`: `timeutils` week bounds, the history and mirror day
      windows, and `/tz` away mode (owner-following boundaries vs. ones that stay on Israel
      time) — the area of several past fixes.
- [ ] `tests/tools/test_memory_sandbox.py`: `_get_safe_path` (traversal, alias spellings,
      symlinks leading out), protected-file guards (no delete; SOUL.md goes through
      confirmation; HEARTBEAT.md rejects direct writes), and the threads.sqlite deny-list.
- [ ] `tests/test_agent_messages.py`: `_add_and_trim` (whole turns, previous turn always
      kept), `_merge_skills`, `_strip_media_blobs`, and media MIME normalization.
- [ ] `tests/test_registry.py`: scoped `get_tools` (core always present, a skill's tools only
      when it is active, a sub-skill hidden until its parent is active) and SKILL.md parsing.
- [ ] `tests/tools/test_fitness.py`: completion and streak math on a seeded DB (no Arbox).
- [ ] Verify: each file is green. Reverting one past fix per file (from the commits behind
      §1's numbers) makes at least one test fail. Record which fix in the PR.

---

## 6. T4 — Eval runner + heartbeat cases

**Layout.**
```
evals/
├── run.py                 # python -m evals.run [--case GLOB] [-k 3] [--update-baseline]
├── harness.py             # scratch root, case seeding, tool stubbing (D5), pinned clock
├── grade.py               # trajectory/ack assertions; optional judge
├── cases/heartbeat/*.yaml
├── baseline.json          # committed pass rates per case
└── results/               # gitignored: one JSONL per run
```

**A case** (YAML):
- `scope`, a pinned `now`, and seeded files (memory files, today's chat and notification log
  rows, HEARTBEAT.md and its `last_run` stamps)
- prior thread messages, and the input (a user message, or a tick)
- `stubs`: canned outputs for stubbed tools, by name
- `expect`: assertions such as `called: {tool, args_match}`, `not_called`,
  `ack: {notify, acted_tasks}`, `judge: "<rubric>"` (optional)

**One run** builds a fresh scratch root, seeds it, swaps the stubbed tools in the registry,
runs the turn through the real graph (`ask_jarvis` or `heartbeat.run_heartbeat` with a fake
Outbox), then grades from the thread state and the ack. Each case runs k times (default 3).
The report gives per-case pass rates, the delta against `baseline.json`, and tokens used,
read from the scratch `turns.jsonl`.

**Checklist.**
- [ ] `harness.py`, `grade.py`, `run.py` as above. The runner refuses to start without
      `GOOGLE_API_KEY` in the environment (D8). The default-deny allowlist lives in
      `harness.py`.
- [ ] About 10 heartbeat cases, for example:
      - nothing worth sending, so `notify=False`
      - a due task with a real finding, so `notify=True` and the task is in `acted_tasks`
      - a task the owner already handled in today's chat is skipped
      - two due tasks, one acted on: only that one is in `acted_tasks`
      - a heartbeat turn may not `create` a heartbeat task
      - a task-linked wake sees its task's block and acts on it
      - a quiet wake makes no tool calls besides the ack
- [ ] First baseline committed with the model in use recorded in it.
- [ ] Verify: a full run is reproducible enough to compare (each case's pass rate across two
      runs of k=3 differs by at most one run). A deliberately weakened rule in `heartbeat.md`
      lowers at least one case's pass rate.

---

## 7. T5 — User-scope evals + workflow

- [ ] `evals/cases/user/*.yaml`, about 15 cases:
      - tool and skill choice: travel, fitness, triggers ("remind me…", "check X in 10
        minutes"), memory lookup through the MEMORY.md index
      - argument correctness: trigger time from a relative phrase under a pinned clock
      - safety rails: a SOUL.md edit goes through confirmation; a destructive media delete
        asks before acting; no booking on an ambiguous request
      - reply-to-proactive: a reply to a mirrored heartbeat notification resolves its
        antecedent
- [ ] `evals/draft_case.py`: given a turn id, reads prod `chat_history.jsonl` / `turns.jsonl`
      locally and writes a case skeleton to the gitignored `evals/drafts/` for hand sanitizing
      (D7).
- [ ] `code-review` skill: when a diff touches `prompts/`, any `SKILL.md`, a tool docstring
      or the model id, the review asks for an eval run and the delta against the baseline.
- [ ] DEVELOPMENT.md: how to run evals, how to read the report, and when to update the
      baseline.
- [ ] Verify: the full suite runs end to end, and one known past behavioral miss, written as
      a case, fails against an older prompt and passes against the current one.

---

## 8. Out of scope

- Mocking the external services (Sonarr, Radarr, Jellyseerr, Arbox, Google, GitHub) for unit
  tests. Fixtures get recorded only for a parser that has actually broken.
- Coverage targets. The suite grows where bugs and changes happen.
- Running evals in CI or on a schedule.
- Changing telemetry to record tool arguments. Evals read them from the thread state instead.
