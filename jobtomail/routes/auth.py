"""Authentification (magic link + Google OAuth, session)."""

from __future__ import annotations

import logging
import secrets as secrets_module
import time

from flask import Blueprint, redirect, render_template, request, session, url_for

from jobtomail import db
from jobtomail.services import google_oauth, magic_link
from jobtomail.services.mailer_transactional import send_magic_link_email

logger = logging.getLogger(__name__)

DEFAULT_USER_EMAIL = "default@localhost"

bp = Blueprint("auth", __name__)

# Utilisateur par défaut historique (Phase 1, mot de passe partagé) — conservé
# pour compatibilité tant que `_ensure_default_user` existe, mais n'est plus
# utilisé par le flux de login courant (magic link / Google OAuth).
DEFAULT_USER_ID = 1


def current_user_id() -> int:
    """ID de l'utilisateur courant pour le scoping multi-tenant.

    NOTE (limite connue) : retombe sur DEFAULT_USER_ID tant que le login
    par utilisateur n'existe pas — voir le commentaire ci-dessus.
    """
    return session.get("user_id", DEFAULT_USER_ID)

# Verrouillage par IP après trop d'échecs (en mémoire — best-effort par worker).
#
# Les compteurs sont répartis par "bucket" (ex. "magic_link") afin que le
# throttling de chaque canal de connexion reste indépendant pour une même IP :
# redemander un lien magique ne doit pas déclencher le verrou d'un autre
# canal, et une connexion réussie par un canal ne doit pas effacer
# silencieusement le compteur d'un autre.
_MAX_ATTEMPTS = 5
_LOCKOUT_SEC = 300
_failed_attempts: dict[tuple[str, str], int] = {}
_locked_until: dict[tuple[str, str], float] = {}


def is_authenticated() -> bool:
    return "user_id" in session


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _is_locked(ip: str, bucket: str = "magic_link") -> bool:
    return time.time() < _locked_until.get((bucket, ip), 0.0)


def _register_failure(ip: str, bucket: str = "magic_link") -> None:
    key = (bucket, ip)
    _failed_attempts[key] = _failed_attempts.get(key, 0) + 1
    if _failed_attempts[key] >= _MAX_ATTEMPTS:
        _locked_until[key] = time.time() + _LOCKOUT_SEC
        _failed_attempts[key] = 0
        logger.warning(
            "IP %s verrouillée %ds (trop d'échecs, bucket=%s)", ip, _LOCKOUT_SEC, bucket
        )


def _register_success(ip: str, bucket: str = "magic_link") -> None:
    key = (bucket, ip)
    _failed_attempts.pop(key, None)
    _locked_until.pop(key, None)


def _safe_next_url(raw: str | None) -> str:
    """Renvoie `raw` s'il s'agit d'un chemin local sûr, sinon la page d'accueil.

    Refuse tout ce qui n'est pas un chemin absolu commençant par un seul `/`
    (les navigateurs traitent `//evil.com` et `/\\evil.com` comme des URLs
    absolues vers un autre hôte, donc un simple `startswith("/")` ne suffit
    pas à empêcher une redirection ouverte).
    """
    if raw and raw.startswith("/") and not raw.startswith("//") and not raw.startswith("/\\"):
        return raw
    return url_for("main.index")


def _ensure_default_user() -> int:
    """Garantit qu'une vraie ligne `users` existe pour l'utilisateur par défaut
    (mot de passe partagé, Phase 1) et renvoie son id.

    Temporaire : tant qu'il n'y a qu'un mot de passe partagé, tout le monde qui
    se connecte se voit attribuer ce même utilisateur. Remplacé en Phase 2 par
    une vraie identité par utilisateur (magic link / Google OAuth).
    """
    row = db.get_user_by_email(DEFAULT_USER_EMAIL)
    if row:
        return row["id"]
    return db.create_user(DEFAULT_USER_EMAIL)


@bp.route("/login")
def login():
    if is_authenticated():
        return redirect(url_for("main.index"))
    return render_template("login.html")


@bp.route("/auth/magic", methods=["POST"])
def request_magic_link():
    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return render_template("login.html", error="Adresse email invalide.")
    ip = _client_ip()
    if _is_locked(ip, bucket="magic_link"):
        return render_template("login.html", error="Trop de tentatives, réessaie dans 5 minutes.")
    token = magic_link.generate_token(email)
    link = url_for("auth.consume_magic_link", token=token, _external=True)
    send_magic_link_email(email, link)
    # Rate-limit magic-link requests per IP using their own bucket, kept
    # separate from the password-login lockout counters so the two flows
    # can't interfere with each other (see module-level comment above
    # `_failed_attempts`).
    _register_failure(ip, bucket="magic_link")
    return render_template("login.html", sent=True)


@bp.route("/auth/magic/<token>")
def consume_magic_link(token: str):
    email = magic_link.verify_token(token)
    if not email:
        return render_template("login.html", error="Lien invalide ou expiré, redemande-en un.")
    user = db.get_user_by_email(email)
    user_id = user["id"] if user else db.create_user(email)
    session.clear()
    session["user_id"] = user_id
    session["authenticated"] = True
    session.permanent = True
    _register_success(_client_ip(), bucket="magic_link")
    return redirect(_safe_next_url(request.args.get("next")))


@bp.route("/auth/google/start")
def google_login_start():
    state = secrets_module.token_urlsafe(24)
    session["_oauth_state"] = state
    session["_oauth_next"] = _safe_next_url(request.args.get("next"))
    return redirect(google_oauth.build_auth_url(state))


@bp.route("/auth/google/callback")
def google_login_callback():
    expected_state = session.pop("_oauth_state", None)
    next_url = _safe_next_url(session.pop("_oauth_next", None))
    if not expected_state or request.args.get("state") != expected_state:
        logger.warning("Échec de connexion Google (state invalide) depuis %s", _client_ip())
        return render_template("login.html", error="Échec de connexion Google, réessaie.")

    code = request.args.get("code")
    if not code:
        logger.warning("Échec de connexion Google (code manquant) depuis %s", _client_ip())
        return render_template("login.html", error="Échec de connexion Google, réessaie.")

    identity = google_oauth.exchange_code(code)
    user = db.get_user_by_email(identity.email)
    user_id = user["id"] if user else db.create_user(identity.email, google_sub=identity.sub)
    if identity.refresh_token:
        db.save_google_refresh_token(user_id, identity.refresh_token)
    session.clear()
    session["user_id"] = user_id
    session["authenticated"] = True
    session.permanent = True
    return redirect(next_url)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
