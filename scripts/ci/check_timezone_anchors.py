#!/usr/bin/env python3
"""CI guard: every timezone consumer declares which clock it follows.

Since /tz (PR #112) the system has two clocks: ISRAEL_TZ (home) and
owner_tz() (the owner's current zone — identical to home unless the owner
set an away zone). A module picks between them by one rule:

    A surface follows owner_tz() only when its underlying data source
    physically travels with the owner (the watch). Everything anchored to a
    fixed place or to Israel-dated files stays ISRAEL_TZ.

Timezone mistakes are invisible at home and only fire abroad, so no home
test catches a wrong pick — review time is the only feedback loop. This
guard makes the pick explicit, the same way check_command_replies.py makes
reply layout explicit: a module that starts importing timeutils fails CI
until it appears in ANCHORS below with a declared anchoring. The guard
forces the question, not the answer — it cannot know the gym is in Israel.

Two layers:
  1. Seam integrity — outside timeutils.py, nothing constructs
     ZoneInfo("Asia/Jerusalem") or opens owner_tz.json directly (quoted
     literal); every consumer goes through the timeutils seam.
  2. Declared anchoring — every module importing timeutils appears in
     ANCHORS as "home", "owner", or "both"; stale entries fail too, so the
     roster cannot rot.

Scope is app code: venv, caches, and scripts/ (dev tooling and these guards,
which seed Israel-dated fixtures) are excluded.

Run:  python3 scripts/ci/check_timezone_anchors.py    (exit 0 = clean, 1 = defect)
"""
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# path -> (anchoring, reason). "home" = ISRAEL_TZ only; "owner" = owner_tz()
# only; "both" = deliberately renders/uses both clocks.
ANCHORS: dict[str, tuple[str, str]] = {
    "agent.py": ("both", "Israel prompt envelope + away-only owner-local line"),
    "gateway/apps/fitness.py": ("home", "Arbox gym is physically in Israel"),
    "gateway/commands/handlers.py": ("both", "/tz shows both clocks; /logs and /usage cut Israel days"),
    "heartbeat.py": ("home", "daily-log naming and late-fire annotation are Israel by design"),
    "heartbeat_state.py": ("home", "due: windows are defined in Israel time"),
    "observability/usage.py": ("home", "usage buckets report the server's own Israel-dated activity"),
    "tools/core/history.py": ("home", "since= day boundary matches the Israel-dated logs it slices"),
    "tools/core/scheduling.py": ("both", "Israel rendering + away-mode owner-local echo"),
    "tools/fitness/_db.py": ("home", "workout dates are Israel-local strings; the gym is in Israel"),
    "tools/fitness/plans.py": ("home", "Sunday-anchored Israel week quotas"),
    "tools/fitness/reports.py": ("home", "reports over Israel-dated fitness rows"),
    "tools/google_health/google_health_tools.py": ("owner", "the watch travels with the owner"),
}

_VALID = {"home", "owner", "both"}
SEAM = "timeutils.py"
_SKIP_DIRS = {"venv", "__pycache__", ".git", "node_modules", "media_cache"}
_SKIP_TOP = {"scripts"}

_IMPORT_RE = re.compile(r"^\s*(?:from\s+timeutils\s+import|import\s+timeutils)\b", re.M)
_HARDCODED_ZONE_RE = re.compile(r"ZoneInfo\(\s*[\"']Asia/Jerusalem[\"']")
_OWNER_FILE_RE = re.compile(r"[\"']owner_tz\.json[\"']")


def _py_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        rel_dir = os.path.relpath(dirpath, REPO_ROOT)
        if rel_dir != "." and rel_dir.split(os.sep)[0] in _SKIP_TOP:
            dirnames[:] = []
            continue
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, name), REPO_ROOT))
    return sorted(out)


def main() -> int:
    failures: list[str] = []
    for path, (anchoring, _) in ANCHORS.items():
        if anchoring not in _VALID:
            failures.append(f"ANCHORS[{path!r}]: invalid anchoring {anchoring!r}")

    importers: set[str] = set()
    for rel in _py_files():
        with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as f:
            src = f.read()
        if rel != SEAM:
            for line_no, line in enumerate(src.splitlines(), 1):
                if _HARDCODED_ZONE_RE.search(line):
                    failures.append(
                        f"{rel}:{line_no}: constructs ZoneInfo('Asia/Jerusalem') directly — "
                        f"import ISRAEL_TZ (or owner_tz) from timeutils instead"
                    )
                if _OWNER_FILE_RE.search(line):
                    failures.append(
                        f"{rel}:{line_no}: touches owner_tz.json directly — only timeutils "
                        f"owns that file; use owner_tz()/set_owner_tz()/clear_owner_tz()"
                    )
        if rel != SEAM and _IMPORT_RE.search(src):
            importers.add(rel)

    for rel in sorted(importers - set(ANCHORS)):
        failures.append(
            f"{rel}: imports timeutils but has no ANCHORS entry in "
            f"scripts/ci/check_timezone_anchors.py — declare 'home', 'owner', or 'both'. "
            f"Rule: follow owner_tz() only if the data source physically travels with "
            f"the owner; fixed-place and Israel-dated surfaces stay ISRAEL_TZ."
        )
    for rel in sorted(set(ANCHORS) - importers):
        failures.append(
            f"ANCHORS lists {rel} but it no longer imports timeutils — remove the stale entry"
        )

    if failures:
        print("FAIL: timezone anchoring contract violated:")
        for f in failures:
            print("   ", f)
        return 1
    print(f"OK: {len(importers)} timezone consumers declared, seam intact (timeutils only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
