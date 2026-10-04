"""Session setup: every test runs against one throwaway JARVIS_ROOT.

config binds every state path at import, so the root and a dummy API key are set
here, before any project module is imported. The dummy key replaces any real one
in the environment, so a test that slips past its fake LLM fails instead of
calling the model.
"""

import os
import shutil
import tempfile

_SCRATCH = tempfile.mkdtemp(prefix="jarvis-tests-")
os.environ["JARVIS_ROOT"] = _SCRATCH
os.environ["GOOGLE_API_KEY"] = "offline-test-dummy"

import config  # noqa: E402

assert config.ROOT == os.path.normpath(_SCRATCH), "scratch root not honored"

import pytest  # noqa: E402

from tests.fakes import FakeOutbox  # noqa: E402

LOG_DIR = os.path.join(config.DATA_DIR, "logs")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SCRATCH, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_logs():
    """Each test starts with empty activity logs and no mirror cursor."""
    import pending_mirrors

    for name in os.listdir(LOG_DIR):
        os.remove(os.path.join(LOG_DIR, name))
    if os.path.exists(pending_mirrors.CURSOR_PATH):
        os.remove(pending_mirrors.CURSOR_PATH)


@pytest.fixture
def use_llm(monkeypatch):
    """Install a fake model as ``agent.llm`` for this test: ``use_llm(FakeLLM([...]))``."""
    import agent

    def install(llm):
        monkeypatch.setattr(agent, "llm", llm)
        return llm

    return install


@pytest.fixture
def fake_outbox(monkeypatch):
    """A recording Outbox behind ``factory.default_outbox()``."""
    from gateway import factory

    outbox = FakeOutbox()
    monkeypatch.setattr(factory, "default_outbox", lambda: outbox)
    return outbox


@pytest.fixture
def gateway_state(monkeypatch):
    """Lets a test register channels and pick the default one; the factory's
    registries and the Outbox's bound loop are restored afterwards."""
    from gateway import factory
    from gateway import outbox

    monkeypatch.setattr(factory, "_registry", dict(factory._registry))
    monkeypatch.setattr(factory, "_confirmation_stores", dict(factory._confirmation_stores))
    monkeypatch.setattr(factory, "_default_channel_name", factory._default_channel_name)
    monkeypatch.setattr(outbox, "_loop", outbox._loop)
    return factory
