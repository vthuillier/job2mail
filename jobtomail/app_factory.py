"""Factory Flask."""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, request, url_for

from jobtomail.constants import TEMPLATES_DIR
from jobtomail.db import init_db
from jobtomail.logging_setup import setup_logging
from jobtomail.routes.auth import auth_enabled, bp as auth_bp, is_authenticated
from jobtomail.routes.config_routes import bp as config_bp
from jobtomail.routes.email_routes import bp as email_bp
from jobtomail.routes.entreprises import bp as entreprises_bp
from jobtomail.routes.scans import bp as scans_bp

logger = logging.getLogger(__name__)

_PUBLIC_ENDPOINTS = frozenset({"auth.login", "static"})


def create_app() -> Flask:
    load_dotenv()
    setup_logging()

    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    app.secret_key = os.getenv("SECRET_KEY") or os.urandom(32)
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=7)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    init_db()

    app.register_blueprint(auth_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(entreprises_bp)
    app.register_blueprint(scans_bp)
    app.register_blueprint(email_bp)

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
