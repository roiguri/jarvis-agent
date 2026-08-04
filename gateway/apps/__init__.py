"""Gateway-shared app registry. Channel-agnostic.

Import order matters: `specs` must load so its `register_app` calls run before
the first call to `list_apps`.
"""

from gateway.apps.registry import AppEntry, AppSpec, list_apps, register_app
from gateway.apps import specs as _specs  # noqa: F401 — register

__all__ = ["AppEntry", "AppSpec", "list_apps", "register_app"]
