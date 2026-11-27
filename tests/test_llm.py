"""Tests for the unified model entry point (llm.py) and its scope routing."""

import pytest

import agent
import compaction
import config
import daily_log
import llm
from tests.fakes import FakeLLM


@pytest.fixture(autouse=True)
def clean_llm_cache():
    llm.clear_cache()
    yield
    llm.clear_cache()


def test_every_expected_profile_exists():
    expected = {"user", "heartbeat", "dream", "daily_log", "compaction", "media"}
    assert expected.issubset(llm.PROFILES.keys())
    for name in expected:
        assert llm.PROFILES[name].name == name
        assert llm.PROFILES[name].model == config.MODEL
        assert llm.PROFILES[name].thinking_level in ("minimal", "low", "medium", "high")
    assert llm.PROFILES["heartbeat"].thinking_level == "low"


def test_unknown_profile_raises():
    with pytest.raises(KeyError):
        llm.get_client("nonexistent_profile")


def test_get_client_caches_per_profile():
    a = llm.get_client("user")
    b = llm.get_client("user")
    c = llm.get_client("heartbeat")
    assert a is b
    assert a is not c


def test_to_kwargs_omits_none_optional_fields():
    profile = llm.ModelProfile(
        name="bare",
        model="gemini-test",
        thinking_level=None,
        temperature=None,
        max_retries=2,
        timeout_s=45.0,
        max_output_tokens=None,
        media_resolution=None,
    )
    assert profile.to_kwargs() == {
        "model": "gemini-test",
        "max_retries": 2,
        "timeout": 45.0,
    }


def test_to_kwargs_includes_set_optional_fields():
    profile = llm.ModelProfile(
        name="full",
        model="gemini-test",
        thinking_level="low",
        temperature=0.2,
        max_retries=1,
        timeout_s=90.0,
        max_output_tokens=4096,
        media_resolution="LOW",
    )
    assert profile.to_kwargs() == {
        "model": "gemini-test",
        "max_retries": 1,
        "timeout": 90.0,
        "temperature": 0.2,
        "thinking_level": "low",
        "max_output_tokens": 4096,
        "media_resolution": "LOW",
    }


def test_model_client_lazy_construction_and_delegation(monkeypatch):
    import langchain_google_genai

    created = []

    class RecordingChatModel:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.invocations = []
            created.append(self)

        def bind_tools(self, tools, **kwargs):
            return ("bound", tools, kwargs)

        def with_structured_output(self, schema, **kwargs):
            return ("structured", schema, kwargs)

        def invoke(self, messages, **kwargs):
            self.invocations.append((messages, kwargs))
            return "ok"

    monkeypatch.setattr(langchain_google_genai, "ChatGoogleGenerativeAI", RecordingChatModel)

    client = llm.ModelClient(
        llm.ModelProfile(name="test", thinking_level="low", temperature=0.0, max_output_tokens=2048)
    )
    # Lazy: not constructed until first call
    assert created == []
    assert client.model == config.MODEL
    assert client.thinking_level == "low"
    assert created == []

    assert client.invoke(["hi"], timeout=30) == "ok"
    assert len(created) == 1
    assert created[0].kwargs["thinking_level"] == "low"
    assert created[0].kwargs["max_output_tokens"] == 2048
    assert created[0].invocations == [(["hi"], {"timeout": 30})]

    # Subsequent calls reuse the same inner instance
    assert client.bind_tools(["t1"]) == ("bound", ["t1"], {})
    assert client.with_structured_output("Schema", include_raw=True) == (
        "structured", "Schema", {"include_raw": True}
    )
    assert len(created) == 1


def test_with_overrides_returns_fresh_client_without_mutating_cached():
    base = llm.get_client("media")
    custom = base.with_overrides(timeout_s=120.0, max_output_tokens=8192, thinking_level="medium")
    assert custom is not base
    assert base.profile.timeout_s == 60.0
    assert base.profile.max_output_tokens == 4096
    assert base.thinking_level == "low"
    assert custom.profile.timeout_s == 120.0
    assert custom.profile.max_output_tokens == 8192
    assert custom.thinking_level == "medium"


def test_agent_client_for_routes_by_scope():
    assert agent._client_for("user").profile.name == "user"
    assert agent._client_for("heartbeat").profile.name == "heartbeat"
    assert agent._client_for("dream").profile.name == "dream"
    # Unknown scope falls back to user
    assert agent._client_for("unknown").profile.name == "user"


def test_agent_client_for_honors_use_llm_override(use_llm):
    fake = use_llm(FakeLLM([]))
    assert agent._client_for("user") is fake
    assert agent._client_for("heartbeat") is fake
    assert agent._client_for("dream") is fake


def test_background_jobs_use_their_own_profiles():
    assert compaction._summarizer_llm().profile.name == "compaction"
    assert daily_log._model().profile.name == "daily_log"
