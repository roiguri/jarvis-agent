"""Files skill — find stored files, send one back, delete them.

Opening a file is `open_file`, a core tool: markers appear in ordinary turns.
Importing this package imports each module, running its ``@tool_register``
side-effects.
"""

from tools.files.library import delete_file, find_files, send_file  # noqa: F401

__all__ = [
    "delete_file",
    "find_files",
    "send_file",
]
