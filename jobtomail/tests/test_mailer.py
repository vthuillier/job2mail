from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet

from jobtomail.services import mailer


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_gmail_send_calls_api_with_base64_message(temp_db, monkeypatch):
    _set_key(monkeypatch)
    from jobtomail import db

    user_id = db.create_user("sender@example.com")
    db.save_google_refresh_token(user_id, "fake-refresh-token")

    msg = EmailMessage()
    msg["To"] = "dest@example.com"
    msg["Subject"] = "Test"
    msg.set_content("Bonjour")

    fake_service = MagicMock()
    with patch("jobtomail.services.mailer._build_gmail_service", return_value=fake_service):
        mailer._gmail_send(user_id, msg)

    fake_service.users.return_value.messages.return_value.send.assert_called_once()
    _, kwargs = fake_service.users.return_value.messages.return_value.send.call_args
    assert kwargs["userId"] == "me"
    assert "raw" in kwargs["body"]


def test_build_gmail_service_raises_clear_error_without_refresh_token(temp_db, monkeypatch):
    _set_key(monkeypatch)
    from jobtomail import db

    user_id = db.create_user("no-token@example.com")

    with pytest.raises(RuntimeError, match="Réglages"):
        mailer._build_gmail_service(user_id)


def test_build_gmail_service_builds_credentials_from_refresh_token(temp_db, monkeypatch):
    _set_key(monkeypatch)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret")
    from jobtomail import db

    user_id = db.create_user("connected@example.com")
    db.save_google_refresh_token(user_id, "fake-refresh-token")

    fake_service = object()
    with patch("jobtomail.services.mailer.build_google_service", return_value=fake_service) as mock_build:
        service = mailer._build_gmail_service(user_id)

    assert service is fake_service
    mock_build.assert_called_once()
    _, kwargs = mock_build.call_args
    assert kwargs["credentials"].refresh_token == "fake-refresh-token"
