"""Instance configuration: the single declared fact (JARVIS_ROOT) and every state
path derived from it.

Imports nothing from the project, so it can be the FIRST project import in every
entrypoint — several modules resolve a state path or read the environment at import
time, and all of them must see a validated root first.

JARVIS_ROOT arrives from the process environment (the service unit, or the shell for
a one-off run), never from a file: ENV_FILE itself is ROOT/secrets/.env, so the root
cannot be read from the file it points at. An undeclared root is a hard error — an
unconfigured checkout must refuse to start rather than guess a root and silently
share another instance's memory, database, and bot token.
"""
import os


def derive(root: str) -> dict:
    """Pure derivation of an instance's paths from a root directory. No side
    effects and no environment reads, so it can be asserted on directly."""
    root = os.path.normpath(root)  # /app/ -> /app, so basename() is never ""
    return {
        "ROOT": root,
        "MEMORY_DIR": os.path.join(root, "jarvis_memory"),
        "DATA_DIR": os.path.join(root, "jarvis_data"),
        "ENV_FILE": os.path.join(root, "secrets", ".env"),
        "INSTANCE": os.path.basename(root),  # log label only: "app", "jarvis_staging"
    }


def _load() -> dict:
    root = os.environ.get("JARVIS_ROOT")
    if not root:
        raise RuntimeError(
            "JARVIS_ROOT is not set. Every state path derives from it, so an "
            "unconfigured process refuses to start rather than guess a root and "
            "risk sharing another instance's memory, database, and token. Set it "
            "in the service unit (deploy/jarvis.service) or export it for a one-off run."
        )
    paths = derive(root)
    if not os.path.isdir(paths["ROOT"]):
        raise RuntimeError(
            f"JARVIS_ROOT={paths['ROOT']!r} does not exist. A mistyped root must "
            "fail loudly, not silently self-initialize an empty instance."
        )
    # Materialize the state subtrees (ROOT itself must pre-exist — see above).
    # DATA_DIR/logs explicitly: the activity-log writers open their files without a
    # lazy mkdir, so a fresh instance would fail on its first append without it.
    for path in (paths["MEMORY_DIR"], paths["DATA_DIR"], os.path.join(paths["DATA_DIR"], "logs")):
        os.makedirs(path, exist_ok=True)
    return paths


_paths = _load()
ROOT = _paths["ROOT"]
MEMORY_DIR = _paths["MEMORY_DIR"]
DATA_DIR = _paths["DATA_DIR"]
ENV_FILE = _paths["ENV_FILE"]
INSTANCE = _paths["INSTANCE"]
