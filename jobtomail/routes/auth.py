"""Authentification par mot de passe (session)."""

from __future__ import annotations

import hmac
import logging
import os
import time

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from jobtomail import db
from jobtomail.services import magic_link

logger = logging.getLogger(__name__)

DEFAULT_USER_EMAIL = "default@localhost"

bp = Blueprint("auth", __name__)

# Utilisateur par défaut tant que l'authentification par utilisateur (avec
# connexion Google / session["user_id"]) n'est pas câblée — voir le plan SaaS
# multi-tenant. L'app ne connaît aujourd'hui qu'un mot de passe partagé
# (APP_PASSWORD) ; toutes les requêtes se comportent donc comme le même
# utilisateur "1" jusqu'à ce qu'une tâche ultérieure implémente la vraie
# identité par session.
DEFAULT_USER_ID = 1


def current_user_id() -> int:
    """ID de l'utilisateur courant pour le scoping multi-tenant.

    NOTE (limite connue) : retombe sur DEFAULT_USER_ID tant que le login
    par utilisateur n'existe pas — voir le commentaire ci-dessus.
    """
    return session.get("user_id", DEFAULT_USER_ID)

# Verrouillage par IP après trop d'échecs (en mémoire — best-effort par worker).
#
# Les compteurs sont répartis par "bucket" (ex. "password", "magic_link") afin
# que le brute-force du mot de passe et le throttling des demandes de lien
# magique restent indépendants pour une même IP : redemander un lien magique
# ne doit pas déclencher le verrou du mot de passe, et une connexion réussie
# par un canal ne doit pas effacer silencieusement le compteur de l'autre.
_MAX_ATTEMPTS = 5
_LOCKOUT_SEC = 300
_failed_attempts: dict[tuple[str, str], int] = {}
_locked_until: dict[tuple[str, str], float] = {}


def _expected_password() -> str:
    return (os.getenv("APP_PASSWORD") or "").strip()


def auth_enabled() -> bool:
    return bool(_expected_password())


def is_authenticated() -> bool:
    return bool(session.get("authenticated"))


def check_password(candidate: str) -> bool:
    expected = _expected_password()
    if not expected:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _client_ip() -> str:
    return request.remote_addr or "unknown"


def _is_locked(ip: str, bucket: str = "password") -> bool:
    return time.time() < _locked_until.get((bucket, ip), 0.0)


def _register_failure(ip: str, bucket: str = "password") -> None:
    key = (bucket, ip)
    _failed_attempts[key] = _failed_attempts.get(key, 0) + 1
    if _failed_attempts[key] >= _MAX_ATTEMPTS:
        _locked_until[key] = time.time() + _LOCKOUT_SEC
        _failed_attempts[key] = 0
        logger.warning(
            "IP %s verrouillée %ds (trop d'échecs, bucket=%s)", ip, _LOCKOUT_SEC, bucket
        )


def _register_success(ip: str, bucket: str = "password") -> None:
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


def send_magic_link_email(to_email: str, link: str) -> None:
    # Placeholder until Task 6 wires transactional sending through a real
    # provider. The link embeds a signed, bearer-equivalent login token
    # valid for 15 minutes, so it must never be logged outside of local
    # debug runs — logging it unconditionally would leak a login credential
    # to anyone with log access.
    if current_app.debug:
        logger.info("[dev] Lien magique pour %s : %s", to_email, link)
    else:
        logger.info("Lien magique demandé pour %s", to_email)


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


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("main.index"))

    error = None
    ip = _client_ip()
    if request.method == "POST":
        if _is_locked(ip):
            logger.warning("Connexion refusée (IP verrouillée) depuis %s", ip)
            error = "Trop de tentatives — réessaie dans quelques minutes."
        else:
            password = request.form.get("password") or ""
            if check_password(password):
                _register_success(ip)
                session.clear()
                session["user_id"] = _ensure_default_user()
                session["authenticated"] = True
                session.permanent = True
                logger.info("Connexion réussie depuis %s", ip)
                return redirect(_safe_next_url(request.args.get("next")))
            _register_failure(ip)
            logger.warning("Tentative de connexion échouée depuis %s", ip)
            error = "Mot de passe incorrect."

    return render_template("login.html", error=error)


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


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
