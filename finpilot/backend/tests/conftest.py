"""Shared fixtures.

Two things are set up globally: every test gets its own SQLite file (so the
suite never touches the developer's database), and LLM calls are off unless a
test explicitly arranges for them. A missing mock should be a loud failure, not
a silent network call.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
SYNTHETIC_DIR = BACKEND_DIR / "tests" / "fixtures" / "synthetic"


@pytest.fixture(scope="session")
def synthetic_dir() -> Path:
    """The generated fixture set. Generated on demand if it is not there."""
    if not (SYNTHETIC_DIR / "expected_transactions.csv").exists():
        from scripts.generate_synthetic import generate

        generate(SYNTHETIC_DIR)
    return SYNTHETIC_DIR


@pytest.fixture()
def tmp_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point config + database at a throwaway directory."""
    from app.config import reload_settings
    from app.db.session import init_db, reset_engine

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    monkeypatch.setenv("LLM_ENABLED", "false")
    # the ingestion/segment/agent tests are single-user; accounts are tested
    # on their own in test_accounts.py with this switched back on
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    # no background deletion of uploads while tests run
    monkeypatch.setenv("UPLOAD_RETENTION_DAYS", "0")
    settings = reload_settings()
    settings.ensure_dirs()
    reset_engine()
    init_db()
    yield settings
    reset_engine()
    reload_settings()


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    """Failed sign-ins are counted per process; don't let one test's
    wrong-password checks lock out the next test's user."""
    from app.auth import LOGIN_THROTTLE

    LOGIN_THROTTLE.reset()
    yield
    LOGIN_THROTTLE.reset()


@pytest.fixture(autouse=True)
def _offline_vectors():
    """The document index embeds with a hashing embedder in tests: no ONNX
    model download, and vectors that are the same on every run."""
    from app.rag import store

    store.set_embedder(store.hashing_embedder())
    store.reset_clients()
    yield
    store.reset_clients()
    store.set_embedder(None)


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch: pytest.MonkeyPatch, request):
    """Fail fast if a test reaches for the network without asking."""
    if request.node.get_closest_marker("live_llm"):
        return
    monkeypatch.setenv("LLM_ENABLED", "false")
    # the key can still come from backend/.env, so switch the agent off too
    monkeypatch.setenv("AGENT_ENABLED", "false")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)


def pytest_collection_modifyitems(config, items):
    """``live_llm`` tests are opt-in via FINPILOT_LIVE_LLM=1."""
    if os.environ.get("FINPILOT_LIVE_LLM") == "1":
        return
    skip = pytest.mark.skip(reason="set FINPILOT_LIVE_LLM=1 to run live LLM tests")
    for item in items:
        if "live_llm" in item.keywords:
            item.add_marker(skip)
