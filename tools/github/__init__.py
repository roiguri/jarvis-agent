"""GitHub project-management skill — repos, issues, pull requests.

Importing this package imports the submodules, running their
``@tool_register`` side-effects and exposing every public tool name.
"""

from tools.github.issues import *  # noqa: F401,F403
from tools.github.repos import *  # noqa: F401,F403
