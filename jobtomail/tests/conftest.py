from __future__ import annotations

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """DB SQLite temporaire, isolée : DB_PATH et db_config.json redirigés vers tmp_path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("jobtomail.db_config.DB_PATH", db_path)
    monkeypatch.setattr("jobtomail.db_config.DB_CONFIG_PATH", tmp_path / "db_config.json")
    monkeypatch.delenv("DB_BACKEND", raising=False)
    from jobtomail import db

    db.reset_engine()
    db.init_db()
    yield db_path
    db.reset_engine()


@pytest.fixture
def app(temp_db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    # Même raison : un .env local (dépôt parent du worktree) peut contenir un vrai
    # INSEE_TOKEN, faisant passer à tort le contrôle "clé manquante" dans les tests.
    monkeypatch.setenv("INSEE_TOKEN", "")
    from jobtomail import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    """Client de test authentifié par défaut.

    Auth est désormais obligatoire (magic link / Google OAuth) — la plupart
    des tests ciblent des routes protégées et n'ont pas vocation à exercer le
    flux de login lui-même, donc on seed directement `session["user_id"]`
    avec un vrai utilisateur, plutôt que de rejouer un login à chaque test
    (cf. tests dédiés à l'auth dans test_auth_security.py).
    """
    from jobtomail import db

    test_client = app.test_client()
    with app.app_context():
        user_id = db.create_user("test-user@example.com")
    with test_client.session_transaction() as sess:
        sess["user_id"] = user_id
    return test_client
