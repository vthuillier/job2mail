"""Routes config & page d'accueil."""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, render_template, request

from jobtomail.constants import (
    DEFAULT_MAIL_BODY,
    DEFAULT_NAF_CODES,
    DEFAULT_RELANCE2_BODY,
    DEFAULT_RELANCE_BODY,
    MAIL_SUBJECT,
    MOTS_CLES_POSTE,
    OLLAMA_MODEL,
)
from jobtomail import db
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
        logger.info("POST /api/config — clés reçues : %s", list(data.keys()))
        db.set_config_values(data)
        return jsonify({"ok": True})

    cfg = db.get_all_config()
    nafs = db.load_nafs(cfg)
    logger.debug("GET /api/config — %d clé(s) SQLite, %d NAF", len(cfg), len(nafs))
    return jsonify(
        {
            "INSEE_TOKEN": cfg.get("INSEE_TOKEN") or os.getenv("INSEE_TOKEN", ""),
            "SERPAPI_KEY": cfg.get("SERPAPI_KEY")
            or os.getenv("SERPAPI_KEY")
            or os.getenv("SERPAPI_TOKEN", ""),
            "TOKEN_HUNTER_IO": cfg.get("TOKEN_HUNTER_IO") or os.getenv("TOKEN_HUNTER_IO", ""),
            "candidate_name": cfg.get("candidate_name", ""),
            "EMAIL_ADDRESS": cfg.get("EMAIL_ADDRESS") or os.getenv("EMAIL_ADDRESS", ""),
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
        }
    )


@bp.route("/api/db/reset", methods=["POST"])
def reset_db():
    logger.warning("POST /api/db/reset — vidage entreprises demandé")
    count = db.reset_entreprises()
    return jsonify({"ok": True, "deleted": count})
