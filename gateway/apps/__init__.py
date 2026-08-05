"""Gateway-shared app registry. Channel-agnostic.

Import order matters: `specs` must load so every app's `register_app` call runs
before the first call to `list_apps`.
"""

from gateway.apps.registry import (
    AppEntry,
    AppError,
    AppInvalidRequest,
    AppNotFound,
    AppSpec,
    dispatch,
    list_apps,
    register_app,
)
from gateway.apps import specs as _specs  # noqa: F401 — register

__all__ = [
    "AppEntry",
    "AppError",
    "AppInvalidRequest",
    "AppNotFound",
    "AppSpec",
    "dispatch",
    "list_apps",
    "register_app",
]
