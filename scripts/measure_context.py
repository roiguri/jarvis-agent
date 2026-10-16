#!/usr/bin/env python3
"""Measure what tools and skills cost in real tokens, and whether changing the
bound tool set costs the prompt cache. Makes real model calls (needs the API
key), so it is run by hand on staging, not in CI.

    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/measure_context.py --yes
    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/measure_context.py --cache --yes

Default — cost. The same one-line request, sent with (a) the user-scope system
prompt and no tools, (b) plus the core tools, (c) plus each skill in turn (its
tools and its rules in the prompt). Differences in reported input tokens are
the real cost of core and of each skill; chars are printed beside them so the
telemetry store's char shares can be read as tokens.

--cache — does a tool-set change cost the cached conversation? A request shaped
like a real call (system prompt, tools, ~20k tokens of filler conversation, a
one-line ask) is sent three times to warm the provider's implicit prefix cache,
then changed one way at a time, with a warm baseline between changes:

    tools removed (prompt unchanged)  where the tool declarations sit in the cached prefix
    tools and rules removed           what switching a skill off actually changes
    a skill added (tools and rules)   what activating a skill mid-turn changes

Each change's cached tokens, against the warm baseline's, say whether the
conversation stayed cached. Every round starts its system prompt with a unique
marker, so no earlier request can pre-warm it; two rounds, because the cache is
best-effort and one reading can miss.

Results are printed and saved as JSON under jarvis_data/observability/.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUEST = "Reply with just: OK. Do not call any tools."

# The cache test's baseline has this skill active; "added" activates the second.
CACHE_SKILL = "fitness"
CACHE_ADDED_SKILL = "travel"
FILLER_TURNS = 40          # user/assistant pairs
FILLER_CHARS_PER_MSG = 1100  # ~40 pairs x 2 x 1.1k chars / 4.3 ≈ 20k tokens

_WORDS = (
    "plan week walk steps class trip flight hotel booking reminder movie show "
    "episode download note list item price order call meeting tomorrow tonight "
    "morning evening check update change move cancel confirm summary details "
    "results score lift workout rest sleep recovery heart rate goal progress "
    "today yesterday weekend schedule time place restaurant coffee read article"
).split()


def _filler(rng: random.Random, chars: int) -> str:
    words = []
    while sum(len(w) + 1 for w in words) < chars:
        words.append(rng.choice(_WORDS))
    return " ".join(words).capitalize() + "."


def _usage(response) -> tuple[int, int]:
    usage = response.usage_metadata or {}
    return (int(usage.get("input_tokens") or 0),
            int((usage.get("input_token_details") or {}).get("cache_read") or 0))


def run_cost(args, agent, registry) -> dict:
    from langchain_core.messages import HumanMessage, SystemMessage

    skills = sorted(registry.skill_namespaces())

    def active_for(ns: str) -> set[str]:
        parent = registry._parent_of(ns)
        return {ns, parent} if parent else {ns}

    def call(active: set[str] | None, tools: bool) -> dict:
        prompt = agent.build_system_prompt("user", active or set(), None)
        bound = registry.get_tools("user", active or set()) if tools else []
        runnable = agent.llm.bind_tools(bound) if bound else agent.llm
        t0 = time.perf_counter()
        response = runnable.invoke([SystemMessage(content=prompt), HumanMessage(content=REQUEST)])
        latency_ms = int((time.perf_counter() - t0) * 1000)
        input_tokens, cache_read = _usage(response)
        time.sleep(args.pause)
        return {
            "input": input_tokens,
            "cache_read": cache_read,
            "latency_ms": latency_ms,
            "prompt_chars": len(prompt),
            "schema_chars": sum(b["schema_chars"] for b in agent._bound_tools(bound)),
            "tools": len(bound),
        }

    result: dict = {}
    print("## cost")
    bare = call(None, tools=False)
    core = call(None, tools=True)
    result["prompt_only"], result["core"] = bare, core
    print(f"  system prompt only : {bare['input']:>7,} tok  ({bare['prompt_chars']:,} chars)")
    print(f"  core tools         : {core['input'] - bare['input']:>7,} tok  "
          f"({core['schema_chars']:,} chars, {core['tools']} tools)")
    result["skills"] = {}
    # Sorted, so a parent is measured before its sub-skills; a sub-skill's cost
    # is measured against its parent being active, since it needs the parent.
    for ns in skills:
        r = call(active_for(ns), tools=True)
        parent = registry._parent_of(ns)
        base = result["skills"][parent] if parent in result["skills"] else core
        r["delta_input"] = r["input"] - base["input"]
        r["delta_chars"] = (r["prompt_chars"] - base["prompt_chars"]) + (r["schema_chars"] - base["schema_chars"])
        result["skills"][ns] = r
        print(f"  + {ns:<22}: {r['delta_input']:>7,} tok  ({r['delta_chars']:,} chars, "
              f"{r['tools'] - base['tools']} tools)" + (f"  [over {parent}]" if parent else ""))
    return result


def run_cache(args, agent, registry) -> dict:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    base_active = {CACHE_SKILL}
    added_active = {CACHE_SKILL, CACHE_ADDED_SKILL}
    shapes = {
        # name: (skills whose rules are in the prompt, skills whose tools are bound)
        "baseline": (base_active, base_active),
        "tools removed": (base_active, set()),
        "tools and rules removed": (set(), set()),
        "skill added": (added_active, added_active),
    }
    sequence = ["baseline", "baseline", "baseline", "tools removed", "baseline",
                "tools and rules removed", "baseline", "skill added"]

    rounds = []
    for round_no in (1, 2):
        marker = f"[cache-test {secrets.token_hex(4)}]"
        rng = random.Random(marker)
        conversation = []
        for _ in range(FILLER_TURNS):
            conversation.append(HumanMessage(content=_filler(rng, FILLER_CHARS_PER_MSG)))
            conversation.append(AIMessage(content=_filler(rng, FILLER_CHARS_PER_MSG)))
        prompts = {name: f"{marker}\n{agent.build_system_prompt('user', rules, None)}"
                   for name, (rules, _) in shapes.items()}
        tools = {name: registry.get_tools("user", bound) for name, (_, bound) in shapes.items()}

        print(f"\n## cache round {round_no} {marker}")
        calls = []
        for step, name in enumerate(sequence, 1):
            runnable = agent.llm.bind_tools(tools[name]) if tools[name] else agent.llm
            response = runnable.invoke(
                [SystemMessage(content=prompts[name])] + conversation + [HumanMessage(content=REQUEST)]
            )
            input_tokens, cache_read = _usage(response)
            share = cache_read / input_tokens if input_tokens else 0
            calls.append({"step": step, "shape": name, "input": input_tokens, "cache_read": cache_read,
                          "tools": len(tools[name]), "prompt_chars": len(prompts[name])})
            print(f"  {step}. {name:<24} input {input_tokens:>7,}  cached {cache_read:>7,} ({share:.0%})")
            time.sleep(args.pause)

        verdicts = {}
        for i, c in enumerate(calls):
            if c["shape"] == "baseline":
                continue
            warm = calls[i - 1]["cache_read"]  # the baseline call right before the change
            # The system prompt is a few thousand tokens and the filler conversation
            # ~20k, so keeping at least half the warm cache means the conversation
            # stayed cached; losing it leaves only the prompt (or nothing).
            kept = warm > 0 and c["cache_read"] >= 0.5 * warm
            verdicts[c["shape"]] = {"cached": c["cache_read"], "warm_baseline": warm, "conversation_kept": kept}
        rounds.append({"marker": marker, "calls": calls, "verdicts": verdicts})

    print("\n## verdict (cached tokens on the changed call vs the warm baseline before it)")
    for shape in ("tools removed", "tools and rules removed", "skill added"):
        parts = []
        for r in rounds:
            v = r["verdicts"][shape]
            if v["warm_baseline"] == 0:
                parts.append("baseline not warm — no reading")
            else:
                parts.append(f"{'conversation still cached' if v['conversation_kept'] else 'conversation NOT cached'}"
                             f" ({v['cached']:,} of {v['warm_baseline']:,})")
        print(f"  {shape:<24}: " + " | ".join(parts))
    return {"rounds": rounds, "skill": CACHE_SKILL, "added_skill": CACHE_ADDED_SKILL}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yes", action="store_true", help="make the calls (they cost tokens)")
    ap.add_argument("--cache", action="store_true",
                    help="run the cache test instead of the cost measurement")
    ap.add_argument("--pause", type=float, default=3.0,
                    help="seconds between calls (default 3)")
    args = ap.parse_args()

    import agent
    import config
    from tools import registry

    if not args.yes:
        if args.cache:
            print(f"Would make 16 model calls with {agent.llm.model} (~25-30k input tokens each, "
                  "~$0.20). Re-run with --yes.")
        else:
            n = 2 + len(registry.skill_namespaces())
            print(f"Would make {n} model calls with {agent.llm.model} (~5-25k input tokens each). "
                  "Re-run with --yes.")
        return 1

    result: dict = {"model": agent.llm.model, "at": datetime.now(timezone.utc).isoformat(),
                    "mode": "cache" if args.cache else "cost"}
    result.update(run_cache(args, agent, registry) if args.cache else run_cost(args, agent, registry))

    out_dir = os.path.join(config.DATA_DIR, "observability")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"measure_context-{result['mode']}-"
                                f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
