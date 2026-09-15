"""Factory Flask."""

from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, url_for
from flask_compress import Compress

from jobtomail.constants import TEMPLATES_DIR
from jobtomail import db
from jobtomail.db import init_db
from jobtomail.logging_setup import setup_logging
from jobtomail.routes.auth import auth_enabled, bp as auth_bp, is_authenticated
from jobtomail.routes.config_routes import bp as config_bp
from jobtomail.routes.email_routes import bp as email_bp
from jobtomail.routes.entreprises import bp as entreprises_bp
from jobtomail.routes.jobs_routes import bp as jobs_bp
from jobtomail.routes.scans import bp as scans_bp

logger = logging.getLogger(__name__)

_PUBLIC_ENDPOINTS = frozenset({"auth.login", "static"})


def _resolve_secret_key() -> str:
    """SECRET_KEY .env sinon clé persistée en base (partagée entre workers gunicorn)."""
    env_key = os.getenv("SECRET_KEY")
    if env_key:
        return env_key
    stored = db.get_config_value("_secret_key")
    if stored:
        return stored
    generated = secrets.token_hex(32)
    db.set_config_values({"_secret_key": generated})
    logger.warning(
        "SECRET_KEY absent de .env — clé générée et persistée en base. "
        "Définissez SECRET_KEY en prod pour un contrôle explicite."
    )
    return generated


def create_app() -> Flask:
    load_dotenv()
    setup_logging()
    init_db()

    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    app.secret_key = _resolve_secret_key()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    Compress(app)

    @app.after_request
    def _security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    app.register_blueprint(auth_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(entreprises_bp)
    app.register_blueprint(scans_bp)
    app.register_blueprint(email_bp)
    app.register_blueprint(jobs_bp)

    @app.before_request
    def require_auth():
        if not auth_enabled():
            return None
        if request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        if is_authenticated():
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "unauthorized"}), 401
        return redirect(url_for("auth.login", next=request.path))

    if auth_enabled():
        logger.info("Protection par mot de passe activée")
    else:
        logger.warning("APP_PASSWORD non défini — l'app est ouverte sans auth")

    logger.info("Application JobToMail créée")
    return app
