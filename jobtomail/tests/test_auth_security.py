from __future__ import annotations

import logging

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


def test_login_ignores_open_redirect_next_param(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    res = client.post("/login?next=//evil.com", data={"password": "s3cret"})
    assert res.status_code in (302, 303)
    assert res.headers["Location"] == "/"


def test_consume_magic_link_ignores_open_redirect_next_param(client, app):
    with app.app_context():
        token = auth_module.magic_link.generate_token("someone@example.com")
    res = client.get(f"/auth/magic/{token}?next=//evil.com")
    assert res.status_code in (302, 303)
    assert res.headers["Location"] == "/"


# --- Critical 2: magic-link token must not leak into logs outside debug -------


def test_send_magic_link_email_does_not_log_raw_link_outside_debug(app, caplog):
    app.debug = False
    with app.app_context(), caplog.at_level(logging.INFO, logger=auth_module.logger.name):
        auth_module.send_magic_link_email(
            "someone@example.com", "https://example.test/auth/magic/super-secret-token"
        )
    assert "super-secret-token" not in caplog.text
    assert "someone@example.com" in caplog.text


def test_send_magic_link_email_logs_raw_link_in_debug(app, caplog):
    app.debug = True
    try:
        with app.app_context(), caplog.at_level(logging.INFO, logger=auth_module.logger.name):
            auth_module.send_magic_link_email(
                "someone@example.com", "https://example.test/auth/magic/super-secret-token"
            )
        assert "super-secret-token" in caplog.text
    finally:
        app.debug = False


# --- Important: password and magic-link rate limiting must be independent -----


def test_magic_link_requests_do_not_trip_password_lockout(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    for _ in range(auth_module._MAX_ATTEMPTS):
        client.post("/auth/magic", data={"email": "someone@example.com"})

    # The magic-link bucket should now be locked out...
    res = client.post("/auth/magic", data={"email": "someone@example.com"})
    assert "Trop de tentatives" in res.get_data(as_text=True)

    # ...but the password bucket for the same IP must be unaffected.
    res = client.post("/login", data={"password": "s3cret"})
    assert res.status_code in (302, 303)


def test_password_failures_do_not_trip_magic_link_lockout(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    for _ in range(auth_module._MAX_ATTEMPTS):
        client.post("/login", data={"password": "wrong"})

    # Password bucket is locked...
    res = client.post("/login", data={"password": "s3cret"})
    assert "Trop de tentatives" in res.get_data(as_text=True)

    # ...but a magic-link request from the same IP must still go through.
    res = client.post("/auth/magic", data={"email": "someone@example.com"})
    assert "Trop de tentatives" not in res.get_data(as_text=True)


def test_magic_link_success_does_not_clear_password_lockout(client, monkeypatch, app):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    for _ in range(auth_module._MAX_ATTEMPTS):
        client.post("/login", data={"password": "wrong"})
    assert auth_module._is_locked("127.0.0.1", bucket="password")

    with app.app_context():
        token = auth_module.magic_link.generate_token("someone@example.com")
    client.get(f"/auth/magic/{token}")

    # A successful magic-link login must not silently clear the unrelated
    # password brute-force lockout for the same IP.
    assert auth_module._is_locked("127.0.0.1", bucket="password")
