"""Authentification par mot de passe (session)."""

from __future__ import annotations

import hmac
import logging
import os

from flask import Blueprint, redirect, render_template, request, session, url_for

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__)


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


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_authenticated():
        return redirect(url_for("main.index"))

    error = None
    if request.method == "POST":
        password = request.form.get("password") or ""
        if check_password(password):
            session.clear()
            session["authenticated"] = True
            session.permanent = True
            logger.info("Connexion réussie depuis %s", request.remote_addr)
            next_url = request.args.get("next") or url_for("main.index")
            if not next_url.startswith("/"):
                next_url = url_for("main.index")
            return redirect(next_url)
        logger.warning("Tentative de connexion échouée depuis %s", request.remote_addr)
        error = "Mot de passe incorrect."

    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST", "GET"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
