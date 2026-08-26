"""Routes config & page d'accueil."""

from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, render_template, request
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from jobtomail.constants import (
    DEFAULT_MAIL_BODY,
    DEFAULT_NAF_CODES,
    DEFAULT_RELANCE2_BODY,
    DEFAULT_RELANCE_BODY,
    MAIL_SUBJECT,
    MOTS_CLES_POSTE,
    OLLAMA_MODEL,
)
from jobtomail import db, db_config
from jobtomail.services.cv_profile import extract_cv_profile
from jobtomail.services.ollama import ollama_available

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        logger.info("POST /api/config — clés reçues : %s", list(data.keys()))
        db.set_config_values(data)
        return jsonify({"ok": True})

    cfg = db.get_all_config()
    nafs = db.load_nafs(cfg)
    logger.debug("GET /api/config — %d clé(s) SQLite, %d NAF", len(cfg), len(nafs))

    candidate_name = cfg.get("candidate_name", "")
    email_address = cfg.get("EMAIL_ADDRESS") or os.getenv("EMAIL_ADDRESS", "")
    insee_token = cfg.get("INSEE_TOKEN") or os.getenv("INSEE_TOKEN", "")
    needs_setup = not (candidate_name and email_address and insee_token)

    cv_profile = None
    if cfg.get("cv_profile"):
        try:
            cv_profile = json.loads(cfg["cv_profile"])
        except json.JSONDecodeError:
            cv_profile = None

    return jsonify(
        {
            "INSEE_TOKEN": insee_token,
            "SERPAPI_KEY": cfg.get("SERPAPI_KEY")
            or os.getenv("SERPAPI_KEY")
            or os.getenv("SERPAPI_TOKEN", ""),
            "TOKEN_HUNTER_IO": cfg.get("TOKEN_HUNTER_IO") or os.getenv("TOKEN_HUNTER_IO", ""),
            "candidate_name": candidate_name,
            "EMAIL_ADDRESS": email_address,
            "EMAIL_PASSWORD": cfg.get("EMAIL_PASSWORD") or os.getenv("EMAIL_PASSWORD", ""),
            "point_ref": cfg.get("point_ref", "La Crau"),
            "rayon_km": cfg.get("rayon_km", "20"),
            "departements": cfg.get("departements", "83, 13"),
            "nafs": nafs or DEFAULT_NAF_CODES,
            "mail_subject": cfg.get("mail_subject") or MAIL_SUBJECT,
            "mail_body": cfg.get("mail_body") or DEFAULT_MAIL_BODY,
            "relance_body": cfg.get("relance_body") or DEFAULT_RELANCE_BODY,
            "relance2_body": cfg.get("relance2_body") or DEFAULT_RELANCE2_BODY,
            "mots_cles_poste": MOTS_CLES_POSTE,
            "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL") or OLLAMA_MODEL,
            "ollama_available": ollama_available(),
            "needs_setup": needs_setup,
            "cv_profile": cv_profile,
        }
    )


@bp.route("/api/cv/analyze", methods=["POST"])
def analyze_cv():
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", True))
    profile = extract_cv_profile(force=force)
    if profile is None:
        return jsonify({"error": "cv.pdf introuvable — place le fichier à la racine du projet"}), 400
    return jsonify(profile)


@bp.route("/api/db/reset", methods=["POST"])
def reset_db():
    logger.warning("POST /api/db/reset — vidage entreprises demandé")
    count = db.reset_entreprises()
    return jsonify({"ok": True, "deleted": count})


@bp.route("/api/db/backend", methods=["GET"])
def get_db_backend():
    cfg = db_config.resolve_db_config()
    return jsonify(
        {
            "backend": cfg.backend,
            "host": cfg.host,
            "port": cfg.port,
            "user": cfg.user,
            "dbname": cfg.dbname,
            "source": cfg.source,
            "locked": cfg.source == "env",
        }
    )


@bp.route("/api/db/backend", methods=["POST"])
def set_db_backend():
    current = db_config.resolve_db_config()
    if current.source == "env":
        return jsonify(
            {"error": "Backend DB imposé par DB_BACKEND (variable d'environnement) — non modifiable ici"}
        ), 409

    data = request.get_json(force=True) or {}
    backend = str(data.get("backend", "")).strip().lower()
    if backend not in ("sqlite", "postgres", "mariadb"):
        return jsonify({"error": "backend doit être sqlite, postgres ou mariadb"}), 400

    if backend == "sqlite":
        db_config.delete_db_config_file()
    else:
        candidate = db_config.DbConfig(
            backend=backend,
            host=str(data.get("host", "")).strip(),
            port=str(data.get("port", "")).strip(),
            user=str(data.get("user", "")).strip(),
            password=str(data.get("password", "")).strip(),
            dbname=str(data.get("dbname", "")).strip(),
            source="file",
        )
        try:
            test_engine = create_engine(candidate.url())
            with test_engine.connect() as conn:
                conn.execute(sa_text("SELECT 1"))
            test_engine.dispose()
        except (SQLAlchemyError, ValueError) as exc:
            logger.warning("Connexion DB refusée pour backend=%s : %s", backend, exc)
            return jsonify({"error": f"Connexion impossible : {exc}"}), 400

        db_config.write_db_config_file(
            {
                "backend": backend,
                "host": candidate.host,
                "port": candidate.port,
                "user": candidate.user,
                "password": candidate.password,
                "dbname": candidate.dbname,
            }
        )

    db.reset_engine()
    db.init_db()
    logger.warning("Backend DB changé : %s", backend)
    return jsonify({"ok": True, "backend": backend})
