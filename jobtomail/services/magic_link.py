"""Génère et vérifie les tokens de connexion par lien magique."""

from __future__ import annotations

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "jobtomail-magic-link"
_MAX_AGE_SECONDS = 15 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=_SALT)


def generate_token(email: str) -> str:
    return _serializer().dumps(email)


def verify_token(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
