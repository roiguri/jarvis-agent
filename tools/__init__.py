"""Tool package.

Importing this package populates the tool registry: ``registry.import_all()``
imports every ``tools.*`` module, whose ``@tool_register`` decorators record
each tool. The agent gets its per-turn tool set from the registry
(``registry.get_tools(scope, active_skills)``) — there is no flat tool list.

To add a tool: drop it in the right package (``tools/core`` for always-on,
``tools/<skill>`` for an activatable skill) and decorate it with
``@tool_register(namespace=..., destructive=...)`` above ``@tool``. Nothing
else to wire.
"""

import logging as _logging

from tools import registry as _registry

_registry.import_all()
_core_n, _skill_n, _ns_n = _registry.registered_counts()
# Startup observability: confirms the tool surface at a glance. WARNING (not
# INFO) on purpose — this module is imported before logging.basicConfig runs.
_logging.getLogger(__name__).warning(
    "[tool-registry] registered %d core tools, %d skill tools across %d namespaces",
    _core_n,
    _skill_n,
    _ns_n,
)
