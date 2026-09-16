# jobtomail/tests/test_quotas.py
from __future__ import annotations

from jobtomail.services import quotas


def test_quota_blocks_after_limit(temp_db, monkeypatch):
    monkeypatch.setattr(quotas, "DEFAULT_SCAN_LIMIT", 2)

    r1 = quotas.check_and_increment(1, "scan")
    r2 = quotas.check_and_increment(1, "scan")
    r3 = quotas.check_and_increment(1, "scan")

    assert (r1.allowed, r2.allowed, r3.allowed) == (True, True, False)
    assert r3.used == 2
    assert r3.limit == 2


def test_quota_is_per_user(temp_db, monkeypatch):
    monkeypatch.setattr(quotas, "DEFAULT_SCAN_LIMIT", 1)

    assert quotas.check_and_increment(1, "scan").allowed is True
    assert quotas.check_and_increment(2, "scan").allowed is True  # different user, own quota


def test_app_config_override_changes_limit(temp_db):
    from jobtomail import db

    db.set_config_values({"quota_scan_limit": "1"})

    assert quotas.check_and_increment(1, "scan").allowed is True
    assert quotas.check_and_increment(1, "scan").allowed is False


def test_scan_sirene_route_returns_429_on_quota_exceeded(client, monkeypatch):
    from jobtomail.routes import scans as scans_module

    monkeypatch.setenv("INSEE_TOKEN", "dummy-token")
    monkeypatch.setattr(
        scans_module.quotas,
        "check_and_increment",
        lambda user_id, kind: quotas.QuotaResult(allowed=False, used=30, limit=30),
    )

    res = client.post("/api/scan/sirene", json={})
    assert res.status_code == 429
    data = res.get_json()
    assert data["error"] == "quota_exceeded"
    assert "message" in data


def test_send_email_route_returns_429_on_quota_exceeded(client, monkeypatch):
    from jobtomail.routes import email_routes

    monkeypatch.setattr(
        email_routes.quotas,
        "check_and_increment",
        lambda user_id, kind: quotas.QuotaResult(allowed=False, used=50, limit=50),
    )

    res = client.post("/api/send-email", json={"email": "a@example.com", "nom": "Test"})
    assert res.status_code == 429
    data = res.get_json()
    assert data["error"] == "quota_exceeded"


def test_limit_for_degrades_to_default_on_malformed_override(temp_db):
    """Defense in depth: a malformed stored quota override (e.g. reaching
    the config table through some other path than the validated admin
    endpoint) must not raise and break every user's scan/email requests —
    it should fall back to the module default."""
    from jobtomail import db

    db.set_config_values({"quota_scan_limit": "not-a-number"})
    assert quotas._limit_for("scan") == quotas.DEFAULT_SCAN_LIMIT

    db.set_config_values({"quota_email_limit": ""})
    assert quotas._limit_for("email") == quotas.DEFAULT_EMAIL_LIMIT


def test_missing_insee_token_does_not_consume_scan_quota(client):
    from jobtomail import db

    with client.session_transaction() as sess:
        user_id = sess["user_id"]

    res = client.post("/api/scan/sirene", json={})
    assert res.status_code == 400  # clé INSEE manquante, rejeté avant le quota

    used = db.get_usage_count(user_id, quotas._current_period(), "scans_count")
    assert used == 0
