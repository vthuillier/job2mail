from __future__ import annotations

import logging

import pytest

from jobtomail.services import mailer_transactional


def test_send_magic_link_email_logs_link_in_debug(app, caplog):
    app.debug = True
    try:
        with app.app_context(), caplog.at_level(
            logging.INFO, logger=mailer_transactional.logger.name
        ):
            mailer_transactional.send_magic_link_email(
                "user@example.com", "https://app/auth/magic/super-secret-token"
            )
        assert "https://app/auth/magic/super-secret-token" in caplog.text
    finally:
        app.debug = False


def test_send_magic_link_email_does_not_log_link_outside_debug(app, caplog):
    app.debug = False
    with app.app_context(), caplog.at_level(
        logging.INFO, logger=mailer_transactional.logger.name
    ):
        mailer_transactional.send_magic_link_email(
            "user@example.com", "https://app/auth/magic/super-secret-token"
        )
    assert "https://app/auth/magic/super-secret-token" not in caplog.text
    assert "user@example.com" in caplog.text


def test_send_magic_link_email_unknown_provider_raises(app, monkeypatch):
    monkeypatch.setenv("TRANSACTIONAL_EMAIL_PROVIDER", "postmark")
    with app.app_context(), pytest.raises(NotImplementedError):
        mailer_transactional.send_magic_link_email("user@example.com", "https://app/x")
