from __future__ import annotations

import pytest


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """DB SQLite temporaire — db.py lie DB_PATH à l'import, patcher constants.py seul ne suffit pas."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("jobtomail.db.DB_PATH", db_path)
    from jobtomail.db import init_db

    init_db()
    yield db_path


@pytest.fixture
def app(temp_db, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    from jobtomail import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
