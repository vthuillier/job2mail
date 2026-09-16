from __future__ import annotations

from cryptography.fernet import Fernet

from jobtomail import db


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_save_and_get_google_refresh_token_roundtrip(temp_db, monkeypatch):
    _set_key(monkeypatch)
    user_id = db.create_user("alice@example.com")

    db.save_google_refresh_token(user_id, "1//0gRefreshTokenValue")

    assert db.get_google_refresh_token(user_id) == "1//0gRefreshTokenValue"


def test_get_google_refresh_token_returns_none_when_absent(temp_db, monkeypatch):
    _set_key(monkeypatch)
    user_id = db.create_user("bob@example.com")

    assert db.get_google_refresh_token(user_id) is None


def test_save_google_refresh_token_is_encrypted_at_rest(temp_db, monkeypatch):
    _set_key(monkeypatch)
    user_id = db.create_user("carol@example.com")
    plaintext = "1//0gSuperSecretRefreshToken"

    db.save_google_refresh_token(user_id, plaintext)

    with db.get_engine().connect() as conn:
        from sqlalchemy import select

        from jobtomail.schema import user_google_tokens

        row = conn.execute(
            select(user_google_tokens.c.refresh_token_encrypted).where(
                user_google_tokens.c.user_id == user_id
            )
        ).first()
    assert row is not None
    assert plaintext not in row[0]


def test_save_google_refresh_token_upserts_on_repeated_login(temp_db, monkeypatch):
    _set_key(monkeypatch)
    user_id = db.create_user("dave@example.com")

    db.save_google_refresh_token(user_id, "first-token")
    db.save_google_refresh_token(user_id, "second-token")

    assert db.get_google_refresh_token(user_id) == "second-token"
