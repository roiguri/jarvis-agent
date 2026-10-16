"""Run the eval cases and compare pass rates with the committed baseline.

    venv/bin/python -m evals.run                    # every case, 3 attempts each
    venv/bin/python -m evals.run --case 'step*' -k 1
    venv/bin/python -m evals.run --update-baseline  # record this run as the baseline

Real model calls (about $0.03 per attempt): run by hand, before merging a change
to a prompt, a tool docstring, a SKILL.md, the model, or heartbeat logic —
never in CI. The API key comes from the environment, or else from the staging
instance's secrets file (never prod's). Each attempt runs in a scratch root;
nothing touches staging or prod state.

Results: one JSONL per run in evals/results/ (not committed); the baseline
(pass rates, model, date) in evals/baseline.json (committed).
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import sys
from datetime import datetime, timezone

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(EVALS_DIR))

from evals import harness  # noqa: E402  (no project imports at module level)

BASELINE = os.path.join(EVALS_DIR, "baseline.json")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")


def load_cases(pattern: str) -> list[dict]:
    import yaml

    cases = []
    root = os.path.join(EVALS_DIR, "cases")
    for dirpath, _, files in os.walk(root):
        for name in sorted(files):
            if not name.endswith(".yaml"):
                continue
            with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                case = yaml.safe_load(f)
            case.setdefault("name", name[:-5])
            if fnmatch.fnmatch(case["name"], pattern):
                cases.append(case)
    return sorted(cases, key=lambda c: c["name"])


def main() -> int:
    ap = argparse.ArgumentParser(description="Run eval cases against the real model.")
    ap.add_argument("--case", default="*", help="glob over case names (default: all)")
    ap.add_argument("-k", type=int, default=3, help="attempts per case (default 3)")
    ap.add_argument("--update-baseline", action="store_true", help="write this run as the baseline")
    ap.add_argument("--list", action="store_true", help="list the cases and exit")
    args = ap.parse_args()

    cases = load_cases(args.case)
    if args.list:
        for c in cases:
            print(f"{c['name']:<36} {c.get('description', '')}")
        return 0
    if not cases:
        print(f"no case matches {args.case!r}")
        return 1

    harness.bootstrap()
    from evals.grade import grade
    from observability.usage import estimate_usd

    baseline = {}
    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as f:
            baseline = json.load(f)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    started = datetime.now(timezone.utc)
    out_path = os.path.join(RESULTS_DIR, f"{started:%Y%m%dT%H%M%SZ}.jsonl")

    summary: dict[str, dict] = {}
    usd = 0.0
    model = None
    with open(out_path, "w", encoding="utf-8") as out:
        for case in cases:
            passed = 0
            for attempt in range(1, args.k + 1):
                result = harness.run_case(case)
                checks = grade(case.get("expect") or {}, result)
                ok = all(c[1] for c in checks)
                passed += ok
                model = model or result.model
                usd += estimate_usd(result.input_tokens, result.cache_read_tokens,
                                    result.output_tokens, result.model)
                failed = [f"{name} ({detail})" for name, good, detail in checks if not good]
                print(f"  {case['name']:<36} #{attempt} {'pass' if ok else 'FAIL'}"
                      + (f" — {'; '.join(failed)}" if failed else ""), flush=True)
                out.write(json.dumps({
                    "case": case["name"], "attempt": attempt, "passed": ok,
                    "checks": [{"check": n, "ok": g, "detail": d} for n, g, d in checks],
                    "calls": result.calls, "ack": result.ack, "sends": result.sends,
                    "tokens": {"input": result.input_tokens, "cache_read": result.cache_read_tokens,
                               "output": result.output_tokens},
                    "model": result.model, "error": result.error,
                }, ensure_ascii=False) + "\n")
            summary[case["name"]] = {"passed": passed, "runs": args.k}

    print(f"\n## pass rates ({args.k} attempts each; model {model}; ~${usd:.2f})")
    base_cases = baseline.get("cases", {})
    for name, s in summary.items():
        b = base_cases.get(name)
        mark = ""
        if b:
            delta = s["passed"] / s["runs"] - b["passed"] / b["runs"]
            mark = f"   (baseline {b['passed']}/{b['runs']})" + (" ↓" if delta < 0 else " ↑" if delta > 0 else "")
        print(f"  {name:<36} {s['passed']}/{s['runs']}{mark}")
    print(f"\nResults: {out_path}")

    if args.update_baseline:
        new = {"model": model, "date": started.date().isoformat(),
               "cases": {**base_cases, **summary}}
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2, sort_keys=True)
            f.write("\n")
        print(f"Baseline written: {BASELINE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
