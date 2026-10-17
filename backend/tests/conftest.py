"""Test-wide safety: no test may reach the real Firestore (or real Gemini).

Tests that exercise persistence patch the specific store functions they need; anything
else that tries to open a Firestore client gets an error (which the streaming writer
and quota cache log and swallow, as they would in production).
"""

import pytest

from app.agents import model_chain
from app.store import firestore as store


class _NoFirestore:
    def __getattr__(self, name):
        raise RuntimeError("tests must not touch the real Firestore")


@pytest.fixture(autouse=True)
def _no_real_backends(monkeypatch):
    monkeypatch.setattr(store, "_client", lambda: _NoFirestore())

    def no_real_gemini(name):
        raise AssertionError(f"real Gemini model {name} must not be used in tests")

    monkeypatch.setattr(model_chain, "_default_factory", no_real_gemini)
    yield
