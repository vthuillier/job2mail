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
    # setenv (pas delenv) : load_dotenv() dans create_app() re-remplirait une clé absente
    # depuis le .env local, ré-activant l'auth malgré l'isolation voulue par le test.
    monkeypatch.setenv("APP_PASSWORD", "")
    # Même raison : un .env local (dépôt parent du worktree) peut contenir un vrai
    # INSEE_TOKEN, faisant passer à tort le contrôle "clé manquante" dans les tests.
    monkeypatch.setenv("INSEE_TOKEN", "")
    from jobtomail import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
