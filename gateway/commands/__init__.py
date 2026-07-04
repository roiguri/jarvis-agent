"""Gateway-shared slash-command dispatch. Channel-agnostic.

Import order matters: `handlers` must load so its `@command` decorators
register before the first call to `try_handle_command` or `list_commands`.
"""

from gateway.commands.router import (
    Command,
    command,
    list_commands,
    try_handle_command,
)
from gateway.commands import handlers as _handlers  # noqa: F401 — register

__all__ = ["Command", "command", "list_commands", "try_handle_command"]
