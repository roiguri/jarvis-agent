#!/usr/bin/env python3
"""Measure what tools and skills cost in real tokens, and whether changing the
bound tool set costs the prompt cache. Makes real model calls (needs the API
key), so it is run by hand on staging, not in CI.

    JARVIS_ROOT=/app/jarvis_staging ./venv/bin/python scripts/measure_context.py --yes

Two readings:

1. Cost. The same one-line request, sent with (a) the user-scope system prompt
   and no tools, (b) plus the core tools, (c) plus each skill in turn (its
   tools and its rules in the prompt). Differences in reported input tokens
   are the real cost of core and of each skill; chars are printed beside them
   so the telemetry store's char shares can be read as tokens.

2. Cache. The core request three times, then the same request with one skill
   added three times; the whole sequence twice. Cache-read tokens on the
   repeats show whether the provider's implicit prefix cache survives a change
   in the tool set.

Results are printed and saved as JSON under jarvis_data/observability/.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUEST = "Reply with just: OK. Do not call any tools."


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yes", action="store_true", help="make the calls (they cost tokens)")
    ap.add_argument("--cache-skill", default="fitness",
                    help="skill added in the cache reading (default: fitness)")
    ap.add_argument("--pause", type=float, default=2.0,
                    help="seconds between calls (default 2)")
    args = ap.parse_args()

    from langchain_core.messages import HumanMessage, SystemMessage

    import agent
    import config
    from tools import registry

    skills = sorted(registry.skill_namespaces())
    n_calls = 2 + len(skills) + 12
    if not args.yes:
        print(f"Would make {n_calls} model calls with {agent.llm.model} "
              f"(~20–60k input tokens each). Re-run with --yes.")
        return 1

    def active_for(ns: str) -> set[str]:
        parent = registry._parent_of(ns)
        return {ns, parent} if parent else {ns}

    def call(active: set[str] | None, tools: bool) -> dict:
        prompt = agent.build_system_prompt("user", active or set(), None)
        bound = registry.get_tools("user", active or set()) if tools else []
        runnable = agent.llm.bind_tools(bound) if bound else agent.llm
        t0 = time.perf_counter()
        response = runnable.invoke([SystemMessage(content=prompt), HumanMessage(content=REQUEST)])
        usage = response.usage_metadata or {}
        time.sleep(args.pause)
        return {
            "input": int(usage.get("input_tokens") or 0),
            "cache_read": int((usage.get("input_token_details") or {}).get("cache_read") or 0),
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "prompt_chars": len(prompt),
            "schema_chars": sum(b["schema_chars"] for b in agent._bound_tools(bound)),
            "tools": len(bound),
        }

    result: dict = {"model": agent.llm.model, "at": datetime.now(timezone.utc).isoformat()}

    print("## cost")
    bare = call(None, tools=False)
    core = call(None, tools=True)
    result["prompt_only"], result["core"] = bare, core
    print(f"  system prompt only : {bare['input']:>7,} tok  ({bare['prompt_chars']:,} chars)")
    print(f"  core tools         : {core['input'] - bare['input']:>7,} tok  "
          f"({core['schema_chars']:,} chars, {core['tools']} tools)")
    result["skills"] = {}
    for ns in skills:
        r = call(active_for(ns), tools=True)
        r["delta_input"] = r["input"] - core["input"]
        r["delta_chars"] = (r["prompt_chars"] - core["prompt_chars"]) + (r["schema_chars"] - core["schema_chars"])
        result["skills"][ns] = r
        print(f"  + {ns:<22}: {r['delta_input']:>7,} tok  ({r['delta_chars']:,} chars, "
              f"{r['tools'] - core['tools']} tools)")

    print(f"\n## cache (core ×3, then + {args.cache_skill} ×3; twice)")
    result["cache"] = []
    for run in (1, 2):
        for label, active in (("core", None), (f"+{args.cache_skill}", active_for(args.cache_skill))):
            for i in range(3):
                r = call(active, tools=True)
                r.update(run=run, label=label, repeat=i + 1)
                result["cache"].append(r)
                share = r["cache_read"] / r["input"] if r["input"] else 0
                print(f"  run {run} {label:<12} #{i + 1}: input {r['input']:>7,}  "
                      f"cached {r['cache_read']:>7,} ({share:.0%})")

    out_dir = os.path.join(config.DATA_DIR, "observability")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"measure_context-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\nSaved {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
