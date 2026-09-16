"""Routes config & page d'accueil."""

from __future__ import annotations

import json
import logging
import os

from flask import Blueprint, jsonify, render_template, request, session
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
from jobtomail.routes.admin import _require_admin
from jobtomail.routes.auth import current_user_id
from jobtomail.services import api_keys
from jobtomail.services.crypto import encrypt
from jobtomail.services.cv_profile import extract_cv_profile
from jobtomail.services.ollama import ollama_available

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)

# Clés utilisateur stockées chiffrées au repos (secrets d'API tiers) —
# distinctes du reste de la config (candidate_name, templates, ...) qui n'a
# pas besoin de chiffrement.
_ENCRYPTED_USER_CONFIG_KEYS = ("SERPAPI_KEY", "TOKEN_HUNTER_IO")


@bp.route("/")
def index():
    ads_enabled = db.get_config_value("ads_enabled", "0") == "1"
    ads_network_id = db.get_config_value("ads_network_id", "")
    current_user = db.get_user_by_id(current_user_id())
    return render_template(
        "index.html",
        ads_enabled=ads_enabled,
        ads_network_id=ads_network_id,
        current_user=current_user,
    )


@bp.route("/api/account/consent", methods=["POST"])
def record_consent():
    db.set_user_consent(current_user_id())
    return jsonify({"ok": True})


@bp.route("/api/account/delete", methods=["POST"])
def delete_account():
    db.delete_user_account(current_user_id())
    session.clear()
    return jsonify({"ok": True})


@bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    if request.method == "POST":
        data = request.get_json(force=True) or {}
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        # INSEE_TOKEN reste une variable d'environnement globale côté serveur
        # (fournie par l'exploitant) — jamais stockée par utilisateur, même
        # si le client la renvoie dans le payload.
        data.pop("INSEE_TOKEN", None)
        for key in _ENCRYPTED_USER_CONFIG_KEYS:
            if key in data and data[key]:
                data[key] = encrypt(str(data[key]))
        logger.info("POST /api/config — clés reçues : %s", list(data.keys()))
        db.set_user_config_values(current_user_id(), data)
        return jsonify({"ok": True})

    cfg = db.get_all_user_config(current_user_id())
    nafs = db.load_nafs(cfg)
    logger.debug("GET /api/config — %d clé(s) SQLite, %d NAF", len(cfg), len(nafs))

    candidate_name = cfg.get("candidate_name", "")
    # INSEE_TOKEN : variable d'environnement globale côté serveur uniquement,
    # jamais lue depuis la config par utilisateur.
    insee_token = api_keys.insee_token()
    connected_email = ""
    user = db.get_user_by_id(current_user_id())
    if user and db.get_google_refresh_token(current_user_id()):
        connected_email = user.get("email") or ""
    needs_setup = not (candidate_name and connected_email and insee_token)

    cv_profile = None
    if cfg.get("cv_profile"):
        try:
            cv_profile = json.loads(cfg["cv_profile"])
        except json.JSONDecodeError:
            cv_profile = None

    return jsonify(
        {
            "insee_configured": bool(insee_token),
            "SERPAPI_KEY": api_keys.serpapi_key_for(current_user_id()) or "",
            "TOKEN_HUNTER_IO": api_keys.hunter_key_for(current_user_id()) or "",
            "candidate_name": candidate_name,
            "google_connected_email": connected_email,
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
    profile = extract_cv_profile(current_user_id(), force=force)
    if profile is None:
        return jsonify({"error": "cv.pdf introuvable — place le fichier à la racine du projet"}), 400
    return jsonify(profile)


@bp.route("/api/db/reset", methods=["POST"])
def reset_db():
    logger.warning("POST /api/db/reset — vidage entreprises demandé")
    count = db.reset_entreprises(current_user_id())
    return jsonify({"ok": True, "deleted": count})


@bp.route("/api/db/backend", methods=["GET"])
def get_db_backend():
    # Décision exploitant (ops-only) : reconfigurer le backend DB d'un
    # déploiement multi-tenant impacte TOUS les utilisateurs — jamais
    # accessible à un utilisateur non-admin.
    _require_admin()
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
    # Décision exploitant (ops-only) : reconfigurer le backend DB d'un
    # déploiement multi-tenant impacte TOUS les utilisateurs — jamais
    # accessible à un utilisateur non-admin.
    _require_admin()
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
