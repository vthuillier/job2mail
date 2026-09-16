# jobtomail/tests/test_check_replies_credentials.py
"""Regression: /api/check-replies must never fall back to operator-set
EMAIL_ADDRESS/EMAIL_PASSWORD environment variables.

`_smtp_credentials` used to resolve those two values via
`env_or_user_config`, which checks the environment BEFORE the per-user
stored value. Since Task 8 removed the only UI that ever populated the
per-user value, the env-var branch was the only reachable path in
practice — meaning `/api/check-replies` would read the OPERATOR's own
mailbox (via env vars an operator would plausibly set, since
.env.example/README.md documented them) and attribute results to
whichever user happened to call the endpoint. That's a cross-tenant data
exposure, not just a broken feature.

The fix reads ONLY the per-user stored config value, so with no per-user
value set (the current, only reachable state) the endpoint always returns
its existing "missing credentials" error instead of silently reading the
operator's mailbox.
"""
from __future__ import annotations


def test_check_replies_ignores_operator_env_vars(client, monkeypatch):
    monkeypatch.setenv("EMAIL_ADDRESS", "operator@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "operator-app-password")

    response = client.post("/api/check-replies", json={})

    assert response.status_code == 400
    data = response.get_json()
    assert "EMAIL_ADDRESS" in data["error"]


def test_check_replies_uses_per_user_stored_credentials(client, monkeypatch):
    """Sanity check: once a per-user value exists, it IS used (this is the
    interim, deliberately-inert state until a proper per-user UI/flow is
    restored in a later task — but the resolution function itself must
    still work for a per-user value)."""
    from jobtomail import db
    from jobtomail.routes import email_routes

    monkeypatch.setenv("EMAIL_ADDRESS", "operator@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "operator-app-password")

    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    db.set_user_config_values(
        user_id,
        {"EMAIL_ADDRESS": "user@example.com", "EMAIL_PASSWORD": "user-app-password"},
    )

    captured = {}

    def fake_check_replies(uid, email_address, email_password, limit=80):
        captured["email_address"] = email_address
        captured["email_password"] = email_password
        return {"ok": True, "results": []}

    monkeypatch.setattr(email_routes, "check_replies", fake_check_replies)

    response = client.post("/api/check-replies", json={})

    assert response.status_code == 200
    assert captured["email_address"] == "user@example.com"
    assert captured["email_password"] == "user-app-password"
