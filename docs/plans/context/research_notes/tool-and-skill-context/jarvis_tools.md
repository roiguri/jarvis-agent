# Jarvis tool and skill context cost (internal measurement)

Scope: read-only measurement of the staging tree `/app/jarvis_staging/code` (HEAD 8c6cd3f) and prod logs `/app/jarvis_data/logs/*.jsonl`. Prod runs 3fdd5f7 (`git -C /app/jarvis_code log -1`); `git diff 3fdd5f7 HEAD -- tools/ prompts/` is empty, so the staging tool schemas are the ones prod binds.

Method:
- Schema sizes come from the golden snapshots `tests/golden/tools/**/*.json`. These are produced by `convert_to_openai_tool(tool)["function"]` (tests/test_tool_surface.py:19-20) and pinned by CI. I read them as plain JSON with `python3 -I`. No project module was imported and no `.env` was touched. The test itself imports `agent` (tests/test_tool_surface.py:13), which is why I did not run it.
- Token figures use the repo's own estimator, characters ÷ 4 over compact JSON (tests/test_tool_surface.py:23-27, `tests/golden/surface.md`). This is an estimate. Gemini receives `FunctionDeclaration`s converted by langchain-google-genai, not OpenAI JSON, and its real per-declaration token count was not measured.
- Usage figures come from a join of `turns.jsonl` and `tool_calls.jsonl` on `turn_id` (the method in docs/plans/context/RESEARCH.md §2). The window is 2026-09-22 to 2026-10-06 20:00 UTC: 415 turns (314 user, 101 heartbeat) and 1,706 tool calls. One malformed line in `turns.jsonl` was skipped. `tool_calls.jsonl` has no scope or args fields, so scope comes from the turn and the target of `activate_skill` cannot be seen directly. It is inferred from changes in `active_skills_start`/`active_skills_end`.

---

## 1. Registry mechanism: how tools and skills reach each model call

### Takeaway
Core tools are always bound. A skill's tools are bound only while its namespace is in the thread's checkpointed `active_skills`. Both the tool set and the system prompt (including the SKILL.md rule bodies of active skills) are rebuilt on every LLM call. The catch is that activation is sticky: nothing ever expires a skill, and deactivation is effectively never used. In practice the owner thread has ratcheted up to nearly every skill being bound on every call.

### Cited Findings
- `@tool_register(namespace, destructive, scopes)` wraps an `@tool` BaseTool and records it in `_REGISTRY` at import time. `scopes=None` means any scope. — [tools/registry.py:40-83](tools/registry.py)
- Visibility rule: a tool whose `scopes` excludes the turn's scope is hidden. Core is always visible. Any other tool is visible only if `entry.namespace in active_skills`. — [tools/registry.py:92-102](tools/registry.py)
- `get_tools(scope, active_skills)` returns every visible tool, and that list is exactly what `bind_tools` receives. — [tools/registry.py:105-110](tools/registry.py)
- `find()` applies the same rule when dispatching, so a call to an inactive skill's tool becomes an "activate the skill first" ToolMessage. — [tools/registry.py:113-122](tools/registry.py)
- `_skill_meta` reads `tools/<ns>/SKILL.md` fresh on every call and returns (frontmatter `description`, body). — [tools/registry.py:169-180](tools/registry.py)
- `compact_skill_list` emits:
  - one `- ns: description` line per top-level skill, always;
  - sub-skill lines only when the parent is active;
  - a "Currently active" line;
  - a `## <ns> — rules` block with the full SKILL.md body for every active skill, including an active sub-skill whose parent is inactive.

  — [tools/registry.py:183-235](tools/registry.py)
- `skill_namespaces()` is every registered non-core namespace plus derived parents. This makes `media`, which owns no tools, a valid activation target. — [tools/registry.py:261-270](tools/registry.py)
- `activate_skill` and `deactivate_skill` are core tools that return sentinel dicts (`_activate` / `_deactivate`). — [tools/core/activate_skill.py:32-77](tools/core/activate_skill.py)
- The tool node turns those sentinels into `delta["active_skills"] = (active | to_activate) - to_deactivate`. — [agent.py:619-641](agent.py)
- The `active_skills` reducer `_merge_skills` keeps the existing set when there is no update. It is a NotRequired, checkpoint-persisted field of `JarvisState`. — [agent.py:164-190](agent.py)
- Per LLM call, `_llm_node` reads `active_skills` from state, then runs `llm.bind_tools(registry.get_tools(scope, active))` and `build_system_prompt(scope, active, due_tasks)`. — [agent.py:509-545](agent.py) (bind at :536, prompt at :539)
- On the final call of an exhausted budget, no tools are bound. — [agent.py:532-534](agent.py)
- `build_system_prompt` appends `registry.compact_skill_list(scope, active_skills)` last, after SOUL, AGENTS, USER and the scope sections. — [agent.py:452](agent.py)
- `ask_jarvis` snapshots `active_skills_start` before the run and `active_skills_end` after it, for telemetry. — [agent.py:707-723](agent.py), [agent.py:934-950](agent.py)
- Design intent is "small and topical" tool surfaces: "A cooking conversation does not carry Sonarr/Radarr/Arbox tool schemas". — [docs/architecture/RUNTIME.md:7](docs/architecture/RUNTIME.md)
- Both threads start blank, and heartbeat activates per task as needed. — [docs/architecture/RUNTIME.md:93](docs/architecture/RUNTIME.md)
- Activations "carry across turns within a thread". Decay or idle timeout (e.g. 12h) is explicitly deferred. Skills persist "until explicitly deactivated or the thread is reset". — [docs/architecture/RUNTIME.md:127-130](docs/architecture/RUNTIME.md), [docs/architecture/RUNTIME.md:333](docs/architecture/RUNTIME.md)
- The only deactivation paths are the model calling `deactivate_skill`, or `/reset`, which wipes the thread's checkpoint rows. `/skills` and `/status` only read `active_skills`. — [gateway/commands/handlers.py:60-74](gateway/commands/handlers.py), [gateway/commands/handlers.py:77-88](gateway/commands/handlers.py)
- AGENTS.md tells the model to "Deactivate a skill when it is clearly no longer needed". — [prompts/AGENTS.md:5](prompts/AGENTS.md)
- The `deactivate_skill` docstring says to use it "when a skill is clearly no longer needed for the rest of the conversation". — [tools/core/activate_skill.py:61-64](tools/core/activate_skill.py)
- There is a single `owner` thread for all channels (CLAUDE.md "Two LangGraph Threads"), so "the rest of the conversation" never ends.

### Inferences
- Because one owner thread never ends, "per conversation" activation behaves as permanent activation. A soft prompt instruction is the only deactivation trigger, and section 4 shows it is ignored.
- Tools are re-sent on every LLM call within a turn: about 3.8 calls per heartbeat tick and 2.8 per user turn (section 4). Each schema byte is therefore paid several times per turn, softened only by implicit caching.

### Gaps
- I did not verify how langchain-google-genai serializes the schemas into Gemini `FunctionDeclaration` (field names, whether the full docstring is kept verbatim). The size is assumed close to the OpenAI-format JSON.

---

## 2. Per-tool schema size (exact chars from the golden snapshots)

### Takeaway
There are 78 registered tools: 12 core and 66 across 12 skill namespaces. The schemas total about 63k chars (≈15.8k tokens). Size is dominated by a handful of "manage_*" multiplexer tools whose docstrings carry long `Args:` prose. `manage_itinerary` alone is ≈1.4k tokens.

Docstrings are 56–78% of each namespace's schema bytes. langchain puts the whole docstring, including the `Args:` section, into `description`, and the parameter properties carry only types (e.g. `activate_skill` in tests/golden/tools/core.json).

### Cited Findings
Per namespace, all from `tests/golden/tools/<ns>.json`:

| Namespace | Tools | Schema chars | ≈Tokens | Share that is description |
|---|---|---|---|---|
| core | 12 | 11,640 | 2,910 | 74% |
| travel | 6 | 15,728 | 3,932 | 78% |
| fitness | 14 | 13,289 | 3,322 | 72% |
| collections | 5 | 7,001 | 1,750 | 64% |
| media/sonarr | 11 | 4,107 | 1,026 | 58% |
| media/radarr | 11 | 3,935 | 983 | 56% |
| github | 8 | 2,860 | 715 | 42% |
| google_health | 3 | 2,168 | 542 | 77% |
| web | 2 | 1,273 | 318 | 78% |
| media/jellyseerr | 3 | 1,115 | 278 | 56% |
| media/system | 2 | 660 | 165 | 70% |
| media/prowlarr | 1 | 386 | 96 | 64% |

The largest individual tools (total compact JSON chars / ≈tokens / description chars):

| Tool | Namespace | Total chars | ≈Tokens | Description chars |
|---|---|---|---|---|
| manage_itinerary | travel | 5,574 | 1,393 | 4,377 |
| manage_collection | collections | 4,060 | 1,015 | 2,825 |
| send_form | core | 2,619 | 654 | 2,107 |
| manage_place | travel | 2,502 | 625 | — |
| manage_wishlist | travel | 2,404 | 601 | — |
| manage_trip | travel | 2,371 | 592 | — |
| manage_heartbeat_task | core | 2,279 | 569 | 1,907 |
| log_cardio_stats | fitness | 2,183 | 545 | — |
| manage_fitness_plan | fitness | 1,959 | 489 | — |
| manage_trigger | core | 1,807 | 451 | 1,356 |
| log_workout | fitness | 1,751 | 437 | — |
| manage_destination | travel | 1,748 | 437 | — |
| heartbeat_respond | core, `scopes=['heartbeat']` | 1,250 | 312 | — |
| query_travel_db | travel | 1,129 | 282 | — |
| query_fitness_db | fitness | 1,128 | 282 | — |

All are from [tests/golden/tools/](tests/golden/tools). The smallest tools are media getters at about 60–100 tokens each.

- The core set in detail, from tests/golden/tools/core.json:
  - send_form 654
  - manage_heartbeat_task 569
  - manage_trigger 451
  - heartbeat_respond 312 (heartbeat only)
  - activate_skill 175
  - write_memory 159
  - get_chat_history 157
  - delete_memory 123
  - deactivate_skill 83
  - get_notification_history 76
  - read_memory 76
  - list_memory 69
- The repo's own summary gives the always-on cost as: user 11 tools ≈2,617 tokens; heartbeat 12 tools ≈2,932 tokens (adds `heartbeat_respond`). Per-skill increments: travel +3,972, fitness +3,349, collections +1,759, sonarr +1,029, radarr +986, github +716, google_health +547, web +321, jellyseerr +279, system +166, prowlarr +97, media +0. — [tests/golden/surface.md](tests/golden/surface.md)
- The decorator locations of the large docstrings are as follows. Line references are to the `def`; the docstring follows it.
  - manage_itinerary — [tools/travel/itinerary.py:360](tools/travel/itinerary.py)
  - manage_collection — [tools/collections/collections.py:39](tools/collections/collections.py)
  - send_form — [tools/core/forms.py:72](tools/core/forms.py)
  - manage_trigger — [tools/core/scheduling.py:56](tools/core/scheduling.py)
  - heartbeat_respond — [tools/core/heartbeat.py:96](tools/core/heartbeat.py)
  - manage_heartbeat_task — [tools/core/heartbeat.py:130](tools/core/heartbeat.py)

### Inferences
- Six tools (manage_itinerary, manage_collection, send_form, the three other travel manage_* tools, manage_heartbeat_task) account for about 5.5k tokens. That is more than all 30 media tools combined (≈2.5k).
- The multiplexed-`action` design keeps the tool count low but moves the size into prose. Any trimming effort should target these, not the many small tools.

### Gaps
- I did not measure Gemini's actual tokenizer count per declaration. A `countTokens` call would need the API key, which is out of bounds here. Chars ÷ 4 may under- or over-count JSON punctuation.

---

## 3. Per-skill footprint (tools + SKILL.md description + rules body + sub-skills)

### Takeaway
An inactive skill costs only its one-line description. The whole always-on "Available skills" block is 665 chars (≈166 tokens). An active skill costs its tool schemas plus its full rules body, and the rules bodies add another ≈2.6k tokens when everything is active. The all-active skill block is 11,869 chars (≈3k tokens) on top of ≈15.5k tokens of schemas.

### Cited Findings
SKILL.md sizes, measured from the files (description chars / body chars):

| Skill | Description chars | Rules body chars | ≈Body tokens |
|---|---|---|---|
| collections | 95 | 1,480 | 370 |
| fitness | 46 | 2,512 | 628 |
| github | 46 | 1,018 | 254 |
| google_health | 107 | 1,370 | 342 |
| media (parent) | 54 | 222 | 55 |
| media/{jellyseerr, prowlarr, radarr, sonarr, system} | 55–62 each | 0 | 0 |
| travel | 63 | 2,731 | 682 |
| web | 42 | 1,196 | 299 |

Source: [tools/*/SKILL.md](tools)

- `media` is a parent with zero tools. Its 5 sub-skills are only advertised after `media` is activated (two-step discovery), and activating the parent does not cascade to its children. — [docs/architecture/RUNTIME.md:62-69](docs/architecture/RUNTIME.md)
- The skill block in the user prompt with nothing active is 665 chars (≈166 tokens). — [tests/golden/prompt_user.md:48-57](tests/golden/prompt_user.md)
- With every skill active, `compact_skill_list` is 11,869 chars (≈2,970 tokens). — [tests/golden/skills_all_active.md](tests/golden/skills_all_active.md)

Full per-skill cost when active (schemas + rules body, in tokens):

| Skill | Schemas | Rules | ≈Total |
|---|---|---|---|
| travel | 3,972 | 682 | 4,650 |
| fitness | 3,349 | 628 | 3,980 |
| collections | 1,759 | 370 | 2,130 |
| github | 716 | 254 | 970 |
| google_health | 547 | 342 | 890 |
| media/sonarr | 1,029 | 0 | 1,030 |
| media/radarr | 986 | 0 | 990 |
| web | 321 | 299 | 620 |

Derived from the tables above.

### Inferences
- Travel and fitness together are about 8.6k tokens when active, roughly a quarter of an average user call (section 5).

### Gaps
- None material.

---

## 4. Actual usage, 2026-09-22 → 2026-10-06 (prod logs)

### Takeaway
On a user call the bound set is almost always 8–10 skills. These were accumulated since the owner thread started on 2026-08-25 and never deactivated. Most of their tools were never called in 14 days.

The heartbeat thread has had a constant `{fitness, google_health}` since 2026-07-21. It calls only about half of those tools, and never any `log_*` fitness writer.

### Cited Findings
Per-call input, from turns.jsonl:

| Scope | Turns | LLM calls | Calls per turn | Input tokens per call | Cache-read per call |
|---|---|---|---|---|---|
| user | 314 | 874 | 2.78 | 36,978 | 17,636 |
| heartbeat | 101 | 384 | 3.80 | 20,702 | 11,640 |

These match the brief's ~34k user / ~21k heartbeat. Only 101 heartbeat turns in 14 days (of 336 hourly ticks) reflects the pre-LLM gate skipping about 70% of ticks.

**Active skills at the start of user turns.** fitness, media, media/radarr, media/system, travel and web were active in 314/314 turns. The others:
- media/sonarr: 246/314
- github: 139/314
- collections: 34/314
- google_health: 13/314

The most common start sets were:
- 7 skills (fitness, media, radarr, sonarr, system, travel, web): 107 turns
- 8 skills (the same plus github): 105 turns
- 6 skills: 68 turns
- 9 skills: 21 turns
- 10 skills: 13 turns

The active set changed in only 4 of 314 user turns.

**Activation timeline on the `owner` thread.** It is purely additive:

| Date | Skill(s) added |
|---|---|
| 08-25 | fitness |
| 08-27 | web |
| 08-28 | media, media/radarr |
| 09-04 | travel |
| 09-11 | media/system |
| 09-23 | media/sonarr |
| 09-26 | github |
| 10-04 | collections |
| 10-05 | google_health |

Removals: zero. `deactivate_skill` has been called 2 times in the whole log history (since 2026-07-07), against 90 `activate_skill` calls. The previous `jarvis-app_roi` and `telegram_508317402` threads show the same additive pattern.

**Heartbeat.** `active_skills_start == active_skills_end == {fitness, google_health}` for 101/101 ticks. It was set on 2026-07-21 (fitness at 08:11, google_health at 17:42) and never changed.

**Tool calls in user scope (1,094).**
- travel dominates: manage_itinerary 397, manage_place 283, manage_wishlist 44, query_travel_db 23, manage_trip 4, manage_destination 3. The 680 itinerary/place calls are concentrated in 146 turns, with up to 26 per turn.
- core: read_memory 60, write_memory 55, delete_memory 32, manage_heartbeat_task 18, list_memory 13, manage_reminder 11 (the pre-triggers tool), manage_trigger 3, send_form 3, activate_skill 4, get_chat_history 1.
- other skills: collections update_item 38; web_search 34, fetch_url 9.
- fitness: 9 different tools, 1–6 calls each.
- media: 8 calls total (search_sonarr 3, get_media_system_health 2, queue/wanted 3).
- github: 8 calls; google_health: 4.

**Tool calls in heartbeat scope (612).**
- core: read_memory 206, write_memory 183, heartbeat_respond 101, get_notification_history 20, then a scattering of others.
- fitness: fetch_upcoming_arbox_classes 63, then 8 other fitness tools at 1–5 calls.
- google_health: health_status 9, plus 3 other google_health calls.

All 1,706 calls have status ok.

**Tools never called in the 14-day window.** 48 distinct tools were called (source: `called.json` cross-checked against `tests/golden/tools`). Of the 78 registered tools, these were never called:

| Namespace | Never-called tools | ≈Schema tokens |
|---|---|---|
| media/radarr | 10 of 11 (only get_radarr_queue called) | 919 |
| media/sonarr | 8 of 11 | 779 |
| fitness | log_cardio_stats, manage_fitness_plan, get_today_workout_id | 1,142 |
| github | add_issue_comment, list_repo_pulls, read_pr_details, update_issue_status | 349 |
| media/jellyseerr | all 3 (not active on owner) | 278 |
| media/prowlarr | 1 (not active on owner) | 96 |
| media/system | get_library_overview | 82 |
| collections | delete_item | 81 |
| core | deactivate_skill | 83 |

**Bound but unused on heartbeat.**
- fitness writers: log_workout, log_cardio_stats, log_exercise_stats, log_wod_result, manage_fitness_plan, get_today_workout_id (≈2,040 tokens).
- core: activate_skill, deactivate_skill, delete_memory, get_chat_history (≈540 tokens).

Together that is ≈2.6k of the ≈6.8k tokens of heartbeat schemas (~38%), sent on every one of 384 heartbeat calls.

### Inferences
- The user thread pays for media/radarr + media/sonarr (≈2k tokens) on every call even though media saw about 8 calls in two weeks. The same goes for fitness (≈4k with rules) at about 30 calls, and github at 8 calls.
- The scoping mechanism exists, but the "never deactivate" behaviour defeats it. That is exactly the decay problem RUNTIME.md deferred.
- In heartbeat, the static {fitness, google_health} set is effectively an always-on heartbeat core. Read-only fitness tools would suffice for what the ticks actually do. That is a candidate for a `scopes=("user",)` restriction on writers, or a read/sync split (the triggers-plan memory notes "S3 needs fitness read/sync split").

### Gaps
- The argument target of `activate_skill` calls is not logged (only `args_size`), so the activation timeline is inferred from turns.jsonl diffs.
- `tool_calls.jsonl` has no per-call bound-tool list, so "bound but never called" is inferred from `active_skills_start` plus the golden schemas, not observed directly.

---

## 5. Share of per-call input that tools and skills represent

### Takeaway
Estimated shares of per-call input:
- **Heartbeat:** schemas ≈6.8k tokens plus skill block ≈1.1k, which is ≈38% of the ≈20.7k average call.
- **User (current owner set):** schemas ≈15.5k tokens plus skill block ≈2.9k, roughly 50% of the ≈37k average call.

Tool and skill context is therefore now the largest single fixed component of a user call. In heartbeat it has grown in share as history shrank. Both shares rest on the chars ÷ 4 estimate.

### Cited Findings
Schema totals are derived from [tests/golden/surface.md](tests/golden/surface.md) increments.

| Bound set | Composition | ≈Schema tokens |
|---|---|---|
| Heartbeat | core 2,932 + fitness 3,349 + google_health 547 | 6,828 |
| User, current owner set since 2026-10-05 | core 2,617 + collections 1,759 + fitness 3,349 + github 716 + google_health 547 + radarr 986 + sonarr 1,029 + system 166 + travel 3,972 + web 321 + media 0 | 15,462 |
| User, most common window set | 7 skills | 12,440 |
| User, with github | 8 skills | 13,156 |

Skill blocks:
- **Heartbeat:** about 4.6k chars (≈1.15k tokens). That is the 665-char list plus the fitness (2,512) and google_health (1,370) rules bodies plus headings.
- **User, owner set:** about the all-active 11,869 chars (≈2.9k tokens), less two jellyseerr/prowlarr lines.

Both are derived from the section 3 measurements.

Per-call shares:

| Scope | Schemas | Schemas + skill block |
|---|---|---|
| Heartbeat (6,828 of 20,702) | ≈33% | ≈38% |
| User, current set (15,462 of 36,978) | ≈42% | ≈50% |
| User, 7-skill set (12,440 of 36,978) | ≈34% | — |

Inputs per call are from section 4.

- **Earlier context measurement (≈43k heartbeat calls).** The 63/18/10/9 split recorded history 63% and tool results 18%. The remaining ~10% and ~9% were the system prompt and the tool schemas, but the docs do not label which is which. — [docs/plans/context/PROBLEMS.md:83-88](docs/plans/context/PROBLEMS.md), [docs/plans/context/RESEARCH.md:62-69](docs/plans/context/RESEARCH.md)
- **Caching.** Tools sit inside the cached prefix ("system instruction + tools + history"). — [docs/plans/context/PROBLEMS.md:131](docs/plans/context/PROBLEMS.md) Cache-read covers about 48% (user) and 56% (heartbeat) of input per call in this window (section 4).

### Inferences
- The heartbeat shift from about 9–10% to about 33% is mostly the denominator shrinking (43k to 21k after the context work). The schemas themselves grew a little (google_health health_status). In heartbeat they are now as large as the measured history.
- Because the schemas are in the cache prefix, adding or removing a skill mid-turn (or between turns) invalidates the cache from the tools onward. Any dynamic per-turn tool selection has to be weighed against cache hits.

### Gaps
- There is no direct split of input_tokens into schema, system prompt, history and results for current calls. The shares are computed from static sizes against averages.
- The system prompt (SOUL + AGENTS + USER + daily log) was not re-measured here. Golden prompts are 5,047 chars (user) and 9,177 chars (heartbeat), but they use fixture memory, not prod memory.

---

## 6. Duplication between docstrings, SKILL.md and prompts; existing decisions on scoping

### Takeaway
Several rules appear two or three times: in a docstring (always bound while the skill is active), in a SKILL.md rules body (in the prompt while it is active), and in AGENTS.md or heartbeat.md (always). The repo's own finding is that docstrings, not prompt prose, drive behaviour. That suggests which copy to keep: the docstring.

### Cited Findings
- **Travel.** "Scheduling a place does not remove it from the wishlist… same place may be scheduled on more than one day" appears in both. So does the overnight-arrival rule ("a 22:00 departure arriving 06:00 is… not a mistake/error", give `arrival_date` and both timezones when crossing zones). — [tools/travel/SKILL.md](tools/travel/SKILL.md) rules body; manage_itinerary docstring in [tests/golden/tools/travel.json](tests/golden/tools/travel.json)
- **heartbeat_respond.** Its docstring (acted_tasks exact names, never a task left for later, `[]` for a wake, notify / notification_text semantics) restates heartbeat.md lines 15-27 nearly clause for clause. — [prompts/heartbeat.md:15-27](prompts/heartbeat.md), [tests/golden/tools/core.json](tests/golden/tools/core.json)
- **Recurring vs one-shot routing.** The rule ("recurring/conditional → manage_heartbeat_task; fixed moment → manage_trigger", "never write_memory for HEARTBEAT.md", "cancel then create", "create exactly once") is stated in:
  - AGENTS.md:9-14;
  - heartbeat.md:22-25;
  - the manage_heartbeat_task docstring;
  - the manage_trigger docstring.

  — [prompts/AGENTS.md:9-14](prompts/AGENTS.md), [prompts/heartbeat.md:22-25](prompts/heartbeat.md), [tests/golden/tools/core.json](tests/golden/tools/core.json)
- **Activate-in-the-same-turn.** The instruction appears both in AGENTS.md:5 and in the activate_skill docstring. — [prompts/AGENTS.md:5](prompts/AGENTS.md), [tools/core/activate_skill.py:35-42](tools/core/activate_skill.py)
- **Long worked examples in docstrings.**
  - send_form has an inline JSON example for `rows` and a multi-bullet usage policy. — [tools/core/forms.py:72](tools/core/forms.py)
  - manage_itinerary documents 22 arguments in prose.
  - manage_collection explains its schema-evolution semantics in prose.
- **Docstrings drive behaviour.** A behaviour persisted after the prompt instruction was removed, because the bound tool docstring's worked example advertised it. "Docstring examples can be live bugs." — [docs/plans/context/RESEARCH.md:138-157](docs/plans/context/RESEARCH.md); see also [docs/plans/context/PROBLEMS.md:281](docs/plans/context/PROBLEMS.md)
- **Owner rule on always-on tools.** Weigh per-turn schema cost against frequency, and prefer slash command (zero context) over skill over core tool. A `manage_timezone` core tool was rejected for `/tz`: "spamming the context with a constant tool is not a good idea." — user memory `feedback_no_always_on_tools_for_rare_actions.md` (`/home/jarvis_user/.claude/projects/-app-jarvis-staging-code/memory/`)
- **Architecture.** Core is described as "~9 tools… cost is fixed and cheap"; there are actually 11/12 now, at ≈2.6–2.9k tokens. Per-scope deny-lists are "not built". — [docs/architecture/RUNTIME.md:36](docs/architecture/RUNTIME.md), [docs/architecture/RUNTIME.md:334](docs/architecture/RUNTIME.md)
- **Process.** CLAUDE.md requires a `--update-golden` run for any prompt or docstring change, so tests/golden/surface.md is a ready-made cost diff for any redesign. — CLAUDE.md "Change behavioral rules" row

### Inferences
- Deduplicating the heartbeat_respond and trigger/heartbeat-task routing prose would save a few hundred tokens per call. The larger wins are:
  - skill decay/deactivation for the owner thread (≈5–8k tokens per user call);
  - scope-restricting unused fitness writers in heartbeat (≈2k tokens per heartbeat call);
  - trimming the travel and collections `Args:` prose.
- Given F1, any trimming of docstrings must be behaviour-tested. Removing a docstring example can change behaviour, not just cost.

### Gaps
- I did not audit every fitness, collections or github docstring against its SKILL.md body line by line. Only the clearest overlaps are listed.
