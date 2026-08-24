from __future__ import annotations

import pytest
from requests.exceptions import ReadTimeout

from jobtomail.services import ollama


@pytest.fixture(autouse=True)
def _reset_circuit_breaker(monkeypatch):
    ollama._consecutive_failures = 0
    ollama._skip_until = 0.0
    monkeypatch.setattr(ollama.time, "sleep", lambda *_: None)
    yield
    ollama._consecutive_failures = 0
    ollama._skip_until = 0.0


def test_call_ollama_retries_then_fails(monkeypatch):
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        raise ReadTimeout("timeout")

    monkeypatch.setattr(ollama.requests, "post", fake_post)

    result = ollama._call_ollama({"model": "x"}, retries=2)

    assert result is None
    assert calls["n"] == 2


def test_call_ollama_circuit_breaker_opens_after_max_failures(monkeypatch):
    def fake_post(*args, **kwargs):
        raise ReadTimeout("timeout")

    monkeypatch.setattr(ollama.requests, "post", fake_post)

    for _ in range(ollama._MAX_FAILURES):
        ollama._call_ollama({"model": "x"}, retries=1)

    assert ollama._skip_until > 0.0


def test_ollama_available_false_during_cooldown(monkeypatch):
    ollama._skip_until = ollama.time.time() + 60

    def fail_if_called(*args, **kwargs):
        raise AssertionError("requests.get should not be called during cooldown")

    monkeypatch.setattr(ollama.requests, "get", fail_if_called)

    assert ollama.ollama_available() is False


def test_ollama_available_success(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {"models": [{"name": "qwen3:0.6b"}]}

    monkeypatch.setattr(ollama.requests, "get", lambda *a, **k: FakeResponse())
    monkeypatch.setenv("OLLAMA_MODEL", "qwen3:0.6b")

    assert ollama.ollama_available() is True
