"""This channel's old media cache — no longer written.

TODO(#185): remove this module and the cache dir once rolling back past the file
store is no longer wanted. Inbound files now go to the file store; this only
lists what the cache still holds, so the store can import it at startup.
"""

import os

SOURCE = "jarvis-app"
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "media_cache")


def legacy_files() -> list[tuple[str, str]]:
    """(absolute path, source) for every file still in the old cache."""
    if not os.path.isdir(_CACHE_DIR):
        return []
    return [(os.path.join(_CACHE_DIR, name), SOURCE) for name in sorted(os.listdir(_CACHE_DIR))
            if os.path.isfile(os.path.join(_CACHE_DIR, name))]
