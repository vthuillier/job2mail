"""Authentification par mot de passe (session)."""

from __future__ import annotations

import hmac
import logging
import os
import time

from flask import Blueprint, redirect, render_template, request, session, url_for

logger = logging.getLogger(__name__)

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
_MAX_ATTEMPTS = 5
_LOCKOUT_SEC = 300
_failed_attempts: dict[str, int] = {}
_locked_until: dict[str, float] = {}


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


def _is_locked(ip: str) -> bool:
    return time.time() < _locked_until.get(ip, 0.0)


def _register_failure(ip: str) -> None:
    _failed_attempts[ip] = _failed_attempts.get(ip, 0) + 1
    if _failed_attempts[ip] >= _MAX_ATTEMPTS:
        _locked_until[ip] = time.time() + _LOCKOUT_SEC
        _failed_attempts[ip] = 0
        logger.warning("IP %s verrouillée %ds (trop d'échecs de connexion)", ip, _LOCKOUT_SEC)


def _register_success(ip: str) -> None:
    _failed_attempts.pop(ip, None)
    _locked_until.pop(ip, None)


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
                session["authenticated"] = True
                session.permanent = True
                logger.info("Connexion réussie depuis %s", ip)
                next_url = request.args.get("next") or url_for("main.index")
                if not next_url.startswith("/"):
                    next_url = url_for("main.index")
                return redirect(next_url)
            _register_failure(ip)
            logger.warning("Tentative de connexion échouée depuis %s", ip)
            error = "Mot de passe incorrect."

    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
