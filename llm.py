"""Unified Gemini model entry point: named profiles and a reusable client.

Every LLM call in Jarvis — the agent loop (user, heartbeat, dream scopes),
compaction, daily log, and media/file descriptions — goes through this module
so model name, thinking level, sampling params, retries, timeouts, and output
caps live in one place as data, never as scattered ChatGoogleGenerativeAI calls.

Imports only ``config`` (never ``agent``), so background jobs and tools can
import it without circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

import config

ThinkingLevel = Literal["minimal", "low", "medium", "high"]

# One generation is otherwise unbounded: max_retries alone caps attempts, not
# the time any single attempt may take. A malformed-function-call run once spent
# 6m48s and 65k output tokens inside ONE call, which no step budget can catch —
# the graph never advances while it happens. 60s is ~2x the slowest per-call
# average ever logged (31s) and above the p99 duration of a WHOLE turn (37s).
DEFAULT_CALL_TIMEOUT_S = 60.0
BACKGROUND_TIMEOUT_S = 180.0


@dataclass(frozen=True)
class ModelProfile:
    """Declarative configuration for one model role (scope or background job)."""

    name: str
    model: str = config.MODEL
    thinking_level: ThinkingLevel | None = None
    # TODO(#199): newer Gemini models reject sampling params — set temperature
    # to None here once ready to upgrade past gemini-3-flash-preview.
    temperature: float | None = 0.0
    max_retries: int = 1
    timeout_s: float = DEFAULT_CALL_TIMEOUT_S
    max_output_tokens: int | None = None
    media_resolution: Any | None = None

    def to_kwargs(self) -> dict[str, Any]:
        """Kwargs for ``ChatGoogleGenerativeAI``, omitting unset optional fields."""
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_retries": self.max_retries,
            "timeout": self.timeout_s,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.thinking_level is not None:
            kwargs["thinking_level"] = self.thinking_level
        if self.max_output_tokens is not None:
            kwargs["max_output_tokens"] = self.max_output_tokens
        if self.media_resolution is not None:
            kwargs["media_resolution"] = self.media_resolution
        return kwargs


# Scope and job profiles. Differences belong here as data, not as branches at
# call sites (matching turn_budget.POLICIES).
PROFILES: dict[str, ModelProfile] = {
    "user": ModelProfile(
        name="user",
        thinking_level="medium",
        temperature=0.2,  # Lower temperature for more deterministic tool usage
        max_retries=2,
        timeout_s=DEFAULT_CALL_TIMEOUT_S,
    ),
    "heartbeat": ModelProfile(
        name="heartbeat",
        thinking_level="low",
        temperature=0.2,
        max_retries=2,
        timeout_s=DEFAULT_CALL_TIMEOUT_S,
    ),
    "dream": ModelProfile(
        name="dream",
        thinking_level="low",
        temperature=0.2,
        max_retries=2,
        timeout_s=BACKGROUND_TIMEOUT_S,
        max_output_tokens=16384,
    ),
    "daily_log": ModelProfile(
        name="daily_log",
        thinking_level="low",
        temperature=0.0,
        max_retries=1,
        timeout_s=BACKGROUND_TIMEOUT_S,
        max_output_tokens=16384,  # thinking tokens count against the output cap
    ),
    "compaction": ModelProfile(
        name="compaction",
        thinking_level="low",
        temperature=0.0,
        max_retries=1,
        timeout_s=BACKGROUND_TIMEOUT_S,
        max_output_tokens=8192,
    ),
    "media": ModelProfile(
        name="media",
        thinking_level="low",
        temperature=0.0,
        max_retries=1,
        timeout_s=DEFAULT_CALL_TIMEOUT_S,
        max_output_tokens=4096,
    ),
}


class ModelClient:
    """Reusable wrapper around ``ChatGoogleGenerativeAI`` driven by a ``ModelProfile``.

    Lazily instantiates the underlying LangChain client on first use so
    importing ``llm`` or inspecting profiles never requires network/auth, and
    tests that monkeypatch ``langchain_google_genai.ChatGoogleGenerativeAI``
    intercept construction cleanly.
    """

    def __init__(self, profile: ModelProfile) -> None:
        self.profile = profile
        self._inner: Any | None = None

    @property
    def model(self) -> str:
        return self.profile.model

    @property
    def thinking_level(self) -> ThinkingLevel | None:
        return self.profile.thinking_level

    def _build(self) -> Any:
        import langchain_google_genai

        return langchain_google_genai.ChatGoogleGenerativeAI(**self.profile.to_kwargs())

    def _client(self) -> Any:
        if self._inner is None:
            self._inner = self._build()
        return self._inner

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self._client().bind_tools(tools, **kwargs)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self._client().with_structured_output(schema, **kwargs)

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        return self._client().invoke(messages, **kwargs)

    def with_overrides(self, **changes: Any) -> ModelClient:
        """Return a fresh ``ModelClient`` with selected profile fields overridden."""
        return ModelClient(replace(self.profile, **changes))

    def __repr__(self) -> str:
        return (
            f"ModelClient(name={self.profile.name!r}, model={self.profile.model!r}, "
            f"thinking_level={self.profile.thinking_level!r})"
        )


_CLIENTS: dict[str, ModelClient] = {}


def get_client(name: str) -> ModelClient:
    """Return the cached ``ModelClient`` for profile ``name``."""
    client = _CLIENTS.get(name)
    if client is None:
        profile = PROFILES[name]  # raises KeyError for unknown profile names
        client = ModelClient(profile)
        _CLIENTS[name] = client
    return client


def clear_cache() -> None:
    """Drop all cached ``ModelClient`` instances (used by tests)."""
    _CLIENTS.clear()
