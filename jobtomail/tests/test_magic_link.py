from __future__ import annotations

import time

from flask import Flask

from jobtomail.services import magic_link


def _app() -> Flask:
    app = Flask(__name__)
    app.secret_key = "test-secret"
    return app


def test_token_roundtrip():
    app = _app()
    with app.app_context():
        token = magic_link.generate_token("user@example.com")
        assert magic_link.verify_token(token) == "user@example.com"


def test_token_rejects_tampering():
    app = _app()
    with app.app_context():
        token = magic_link.generate_token("user@example.com")
        assert magic_link.verify_token(token + "x") is None


def test_token_expires(monkeypatch):
    # `magic_link.time` isn't consulted by itsdangerous internally — the
    # timestamp is embedded by `itsdangerous.timed`, which does its own
    # `import time` and calls `time.time()` at signing/verification time.
    # Patching the real `time.time` (which that module reads through) lets
    # us simulate 16 minutes passing without waiting for real expiry.
    app = _app()
    with app.app_context():
        token = magic_link.generate_token("user@example.com")
        real_time = time.time
        monkeypatch.setattr(time, "time", lambda: real_time() + 16 * 60)
        assert magic_link.verify_token(token) is None
