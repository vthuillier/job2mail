from __future__ import annotations

from jobtomail.routes import auth as auth_module


def test_magic_link_login_promotes_admin_email(client, app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com")

    with app.app_context():
        token = auth_module.magic_link.generate_token("boss@example.com")
    client.get(f"/auth/magic/{token}")

    from jobtomail import db

    with app.app_context():
        user = db.get_user_by_email("boss@example.com")
    assert user["is_admin"] == 1


def test_magic_link_login_does_not_promote_other_emails(client, app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "boss@example.com")

    with app.app_context():
        token = auth_module.magic_link.generate_token("nobody@example.com")
    client.get(f"/auth/magic/{token}")

    from jobtomail import db

    with app.app_context():
        user = db.get_user_by_email("nobody@example.com")
    assert user["is_admin"] == 0


def test_admin_email_match_is_case_insensitive(client, app, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAIL", "Boss@Example.com")

    with app.app_context():
        token = auth_module.magic_link.generate_token("boss@example.com")
    client.get(f"/auth/magic/{token}")

    from jobtomail import db

    with app.app_context():
        user = db.get_user_by_email("boss@example.com")
    assert user["is_admin"] == 1


def test_no_admin_email_set_promotes_nobody(client, app):
    with app.app_context():
        token = auth_module.magic_link.generate_token("anyone@example.com")
    client.get(f"/auth/magic/{token}")

    from jobtomail import db

    with app.app_context():
        user = db.get_user_by_email("anyone@example.com")
    assert user["is_admin"] == 0


def test_bootstrap_promotes_existing_account_on_later_login(app, monkeypatch):
    from jobtomail import db

    with app.app_context():
        db.create_user("late@example.com")
        token = auth_module.magic_link.generate_token("late@example.com")

    monkeypatch.setenv("ADMIN_EMAIL", "late@example.com")
    test_client = app.test_client()
    test_client.get(f"/auth/magic/{token}")

    with app.app_context():
        user = db.get_user_by_email("late@example.com")
    assert user["is_admin"] == 1
