from __future__ import annotations


def _admin_client(app):
    """Client de test authentifié comme un utilisateur admin (is_admin=True)."""
    from jobtomail import db

    test_client = app.test_client()
    with app.app_context():
        user_id = db.create_user("admin@example.com", is_admin=True)
    with test_client.session_transaction() as sess:
        sess["user_id"] = user_id
    return test_client


def test_unauthenticated_redirects_to_login(app):
    test_client = app.test_client()
    response = test_client.get("/admin")
    assert response.status_code in (302, 303)
    assert "/login" in response.headers["Location"]


def test_non_admin_gets_403(client):
    # `client` (conftest) is authenticated as a regular, non-admin user.
    response = client.get("/admin")
    assert response.status_code == 403


def test_non_admin_gets_403_on_api_users(client):
    response = client.get("/admin/api/users")
    assert response.status_code == 403


def test_admin_sees_dashboard(app):
    test_client = _admin_client(app)
    response = test_client.get("/admin")
    assert response.status_code == 200


def test_admin_sees_user_list(app):
    test_client = _admin_client(app)
    response = test_client.get("/admin/api/users")
    assert response.status_code == 200
    emails = [u["email"] for u in response.get_json()]
    assert "admin@example.com" in emails


def test_admin_user_list_includes_usage_counts(app):
    from jobtomail import db

    test_client = _admin_client(app)
    with app.app_context():
        db.increment_usage_count(1, "2026-09", "scans_count")

    response = test_client.get("/admin/api/users")
    assert response.status_code == 200
    users = {u["id"]: u for u in response.get_json()}
    assert "scans_count" in users[1]
    assert "emails_count" in users[1]


def test_admin_can_set_quotas(app):
    from jobtomail import db
    from jobtomail.services import quotas

    test_client = _admin_client(app)
    response = test_client.post(
        "/admin/api/quotas", json={"scan_limit": 5, "email_limit": 7}
    )
    assert response.status_code == 200

    with app.app_context():
        assert db.get_app_config_value("quota_scan_limit") == "5"
        assert db.get_app_config_value("quota_email_limit") == "7"
        assert quotas._limit_for("scan") == 5
        assert quotas._limit_for("email") == 7


def test_non_admin_cannot_set_quotas(client):
    response = client.post(
        "/admin/api/quotas", json={"scan_limit": 5, "email_limit": 7}
    )
    assert response.status_code == 403
