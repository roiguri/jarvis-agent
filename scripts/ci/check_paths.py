#!/usr/bin/env python3
"""CI guard: no module may hardcode a production state path.

Every state path derives from JARVIS_ROOT (config.MEMORY_DIR / config.DATA_DIR).
This asserts that stays true: it imports the whole app under a throwaway root and
fails if any module-level constant still points at /app/jarvis_memory or
/app/jarvis_data. A hardcoded path re-added months from now fails here instead of
silently sharing prod's memory, database, and logs.

Run:  python3 scripts/ci/check_paths.py     (exit 0 = clean, 1 = leak)
Must run in a fresh interpreter — it sets JARVIS_ROOT before the app imports.
"""
import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROD_MARKERS = ("/app/jarvis_memory", "/app/jarvis_data")


def _scan_for_leaks() -> list[str]:
    """Every module-level str constant in a repo module that still names a prod tree."""
    this_file = os.path.abspath(__file__)
    leaks = []
    for name, mod in list(sys.modules.items()):
        path = getattr(mod, "__file__", None) or ""
        if not path.startswith(REPO_ROOT) or "/venv/" in path or "/site-packages/" in path:
            continue
        # Skip this script itself — its PROD_MARKERS *define* the strings we hunt for
        # (as __main__ when run directly, __mp_main__ under multiprocessing).
        if os.path.abspath(path) == this_file:
            continue
        for attr, val in vars(mod).items():
            if attr.startswith("__"):  # skips __doc__ etc. — docstrings may mention paths as prose
                continue
            if isinstance(val, str):
                candidates = [val]
            elif isinstance(val, (list, tuple)):
                candidates = [x for x in val if isinstance(x, str)]
            elif isinstance(val, dict):
                candidates = [x for x in val.values() if isinstance(x, str)]
            else:
                continue
            for v in candidates:
                if any(m in v for m in PROD_MARKERS):
                    leaks.append(f"{name}.{attr} = {v!r}")
    return leaks


def main() -> int:
    # The root must be set BEFORE the app imports, or config binds first.
    assert "config" not in sys.modules and "agent" not in sys.modules, \
        "check_paths must run in a fresh interpreter (config/agent not yet imported)"

    scratch = tempfile.mkdtemp(prefix="jarvis-check-paths-")
    try:
        os.makedirs(os.path.join(scratch, "secrets"))
        # agent.py asserts GOOGLE_API_KEY at import (after load_dotenv); a dummy
        # lets the import complete without real secrets or any network call.
        with open(os.path.join(scratch, "secrets", ".env"), "w") as f:
            f.write("GOOGLE_API_KEY=dummy-for-check-paths\n")
        os.environ["JARVIS_ROOT"] = scratch
        sys.path.insert(0, REPO_ROOT)

        # derive() is a pure function — sanity-check both a normal and a trailing-slash root.
        import config
        d = config.derive("/tmp/x/")
        assert d["INSTANCE"] == "x", d
        assert d["MEMORY_DIR"] == "/tmp/x/jarvis_memory", d

        import main  # noqa: F401 — imports the whole app graph, populating sys.modules

        leaks = _scan_for_leaks()
        if leaks:
            print("FAIL: module-level constants still hardcode a production state path:")
            for leak in leaks:
                print("   ", leak)
            print("\nDerive them from config.MEMORY_DIR / config.DATA_DIR instead.")
            return 1
        print(f"OK: no module hardcodes {' or '.join(PROD_MARKERS)}")
        return 0
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
