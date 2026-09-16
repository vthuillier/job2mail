from __future__ import annotations

import pytest

from jobtomail.routes import auth as auth_module


@pytest.fixture(autouse=True)
def _reset_rate_limit_state():
    """Les compteurs de verrouillage sont des dicts au niveau module —
    on les réinitialise avant/après chaque test pour éviter toute
    interférence entre tests (et entre buckets)."""
    auth_module._failed_attempts.clear()
    auth_module._locked_until.clear()
    yield
    auth_module._failed_attempts.clear()
    auth_module._locked_until.clear()


# --- Critical 1: open redirect -------------------------------------------------


def test_safe_next_url_rejects_protocol_relative(app):
    with app.test_request_context("/"):
        assert auth_module._safe_next_url("//evil.com") == "/"


def test_safe_next_url_rejects_backslash_variant(app):
    with app.test_request_context("/"):
        assert auth_module._safe_next_url("/\\evil.com") == "/"


def test_safe_next_url_accepts_local_path(app):
    with app.test_request_context("/"):
        assert auth_module._safe_next_url("/dashboard") == "/dashboard"


def test_safe_next_url_falls_back_on_missing(app):
    with app.test_request_context("/"):
        assert auth_module._safe_next_url(None) == "/"


def test_consume_magic_link_ignores_open_redirect_next_param(client, app):
    with app.app_context():
        token = auth_module.magic_link.generate_token("someone@example.com")
    res = client.get(f"/auth/magic/{token}?next=//evil.com")
    assert res.status_code in (302, 303)
    assert res.headers["Location"] == "/"


# --- Critical 2: magic-link token must not leak into logs outside debug -------
#
# `send_magic_link_email` was extracted into `jobtomail.services.mailer_transactional`
# (Task 6). Its debug-gating behaviour (never logging the raw link outside
# debug) is now covered directly in `test_mailer_transactional.py`, against
# that module's own logger.


# --- Important: rate-limiting buckets must stay independent -------------------
#
# The password-login path (and its "password" bucket) was removed once magic
# link + Google OAuth fully covered login. The bucket mechanism itself is
# generic and reusable by any future login channel, so we exercise its
# isolation directly at the module level rather than through a since-removed
# HTTP route.


def test_failure_in_one_bucket_does_not_lock_another_bucket():
    for _ in range(auth_module._MAX_ATTEMPTS):
        auth_module._register_failure("127.0.0.1", bucket="magic_link")

    assert auth_module._is_locked("127.0.0.1", bucket="magic_link")
    assert not auth_module._is_locked("127.0.0.1", bucket="some_other_bucket")


def test_success_in_one_bucket_does_not_clear_another_bucket(client, app):
    for _ in range(auth_module._MAX_ATTEMPTS):
        auth_module._register_failure("127.0.0.1", bucket="magic_link")
    assert auth_module._is_locked("127.0.0.1", bucket="magic_link")

    auth_module._register_success("127.0.0.1", bucket="some_other_bucket")

    # A successful login on an unrelated bucket must not silently clear the
    # magic-link brute-force lockout for the same IP.
    assert auth_module._is_locked("127.0.0.1", bucket="magic_link")


def test_magic_link_requests_lock_out_after_max_attempts(client):
    for _ in range(auth_module._MAX_ATTEMPTS):
        client.post("/auth/magic", data={"email": "someone@example.com"})

    res = client.post("/auth/magic", data={"email": "someone@example.com"})
    assert "Trop de tentatives" in res.get_data(as_text=True)


# --- Google OAuth login -----------------------------------------------------


def test_google_login_start_redirects_to_google_and_stores_state(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example.com/auth/google/callback")

    res = client.get("/auth/google/start")

    assert res.status_code in (302, 303)
    assert res.headers["Location"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    with client.session_transaction() as sess:
        assert sess["_oauth_state"]


def test_google_login_start_sanitizes_open_redirect_next_param(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example.com/auth/google/callback")

    client.get("/auth/google/start?next=//evil.com")

    with client.session_transaction() as sess:
        assert sess["_oauth_next"] == "/"


def test_google_login_callback_rejects_missing_state(client):
    res = client.get("/auth/google/callback?code=abc123")
    assert "Échec de connexion Google" in res.get_data(as_text=True)


def test_google_login_callback_rejects_state_mismatch(client):
    with client.session_transaction() as sess:
        sess["_oauth_state"] = "expected-state"

    res = client.get("/auth/google/callback?code=abc123&state=wrong-state")
    assert "Échec de connexion Google" in res.get_data(as_text=True)
    with client.session_transaction() as sess:
        assert "_oauth_state" not in sess


def test_google_login_callback_success_sets_session_and_saves_refresh_token(
    client, monkeypatch
):
    monkeypatch.setenv("APP_ENCRYPTION_KEY", _fernet_key())
    with client.session_transaction() as sess:
        sess["_oauth_state"] = "expected-state"
        sess["_oauth_next"] = "/dashboard"

    identity = auth_module.google_oauth.GoogleIdentity(
        email="alice@example.com",
        sub="google-sub-123",
        refresh_token="1//0gRefreshToken",
        access_token="ya29.access-token",
    )
    monkeypatch.setattr(auth_module.google_oauth, "exchange_code", lambda code: identity)

    res = client.get("/auth/google/callback?code=abc123&state=expected-state")

    assert res.status_code in (302, 303)
    assert res.headers["Location"] == "/dashboard"

    from jobtomail import db

    row = db.get_user_by_email("alice@example.com")
    assert row is not None
    assert row["google_sub"] == "google-sub-123"
    assert db.get_google_refresh_token(row["id"]) == "1//0gRefreshToken"

    with client.session_transaction() as sess:
        assert sess["user_id"] == row["id"]


def _fernet_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()
