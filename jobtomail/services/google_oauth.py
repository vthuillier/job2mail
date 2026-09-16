"""Connexion Google OAuth : identité (openid/email) + autorisation gmail.send
en un seul flux de consentement, pour éviter un second aller-retour OAuth
quand l'utilisateur connecte Gmail plus tard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import google_auth_oauthlib.flow as google_flow

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"


@dataclass
class GoogleIdentity:
    email: str
    sub: str
    refresh_token: str | None
    access_token: str


def build_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token on every login, not just first
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


def _flow() -> google_flow.Flow:
    client_config = {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": _AUTH_ENDPOINT,
            "token_uri": _TOKEN_ENDPOINT,
        }
    }
    flow = google_flow.Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = os.environ["GOOGLE_REDIRECT_URI"]
    return flow


def exchange_code(code: str) -> GoogleIdentity:
    """Échange le code d'autorisation contre les tokens, et vérifie l'identité
    via le id_token renvoyé (appel réseau — non testé unitairement, voir
    test_google_oauth.py)."""
    flow = _flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials

    import google.auth.transport.requests
    import google.oauth2.id_token

    request = google.auth.transport.requests.Request()
    id_info = google.oauth2.id_token.verify_oauth2_token(
        credentials.id_token, request, os.environ["GOOGLE_CLIENT_ID"]
    )
    return GoogleIdentity(
        email=id_info["email"],
        sub=id_info["sub"],
        refresh_token=credentials.refresh_token,
        access_token=credentials.token,
    )
