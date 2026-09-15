"""Routes de scan Sirene / SerpAPI / nettoyage."""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from jobtomail.constants import DEFAULT_NAF_CODES, OLLAMA_MODEL
from jobtomail.db import env_or_config
from jobtomail.services import jobs
from jobtomail.services.cleaner import clean_entreprises
from jobtomail.services.contacts import run_contacts_scan
from jobtomail.services.dirigeants import run_dirigeants_scan
from jobtomail.services.frenchtech import run_frenchtech_scan
from jobtomail.services.it_scope import mark_hors_champs_entreprises
from jobtomail.services.naf_search import search_naf
from jobtomail.services.ollama import ollama_available
from jobtomail.services.prune import run_prune
from jobtomail.services.serpapi import run_serpapi_scan
from jobtomail.services.sirene import run_sirene_scan

logger = logging.getLogger(__name__)

bp = Blueprint("scans", __name__)


@bp.route("/api/naf/search", methods=["GET"])
def naf_search_route():
    """Recherche de codes NAF par mot-clé (métier/secteur) — pour les
    utilisateurs qui ne connaissent pas la nomenclature INSEE par cœur."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"ok": True, "results": []})
    return jsonify({"ok": True, "results": search_naf(q)})


@bp.route("/api/scan/sirene", methods=["POST"])
def scan_sirene():
    data = request.get_json(force=True) or {}
    national = bool(data.get("national", False))
    point_ref = data.get("point_ref") or "La Crau"
    rayon_km = float(data.get("rayon_km") or 20)
    depts = [d.strip() for d in str(data.get("departements") or "83, 13").split(",") if d.strip()]
    nafs = data.get("nafs") or DEFAULT_NAF_CODES
    if isinstance(nafs, list):
        nafs = {code: DEFAULT_NAF_CODES.get(code, code) for code in nafs}

    include_mairies = data.get("include_mairies")
    if include_mairies is None:
        include_mairies = True
    else:
        include_mairies = bool(include_mairies)

    include_associations = data.get("include_associations")
    if include_associations is None:
        include_associations = True
    else:
        include_associations = bool(include_associations)

    insee_token = data.get("INSEE_TOKEN") or env_or_config("INSEE_TOKEN")
    if not insee_token:
        logger.error("Scan Sirene refusé : INSEE_TOKEN manquant")
        return jsonify({"error": "Clé API INSEE (INSEE_TOKEN) manquante"}), 400

    logger.info(
        "POST /api/scan/sirene — national=%s %s / %skm / %s NAF / depts=%s mairies=%s associations=%s",
        national,
        point_ref,
        rayon_km,
        len(nafs),
        len(depts) if national else depts,
        include_mairies,
        include_associations,
    )

    job_id = jobs.create_job(
        "sirene",
        params={"point_ref": point_ref, "rayon_km": rayon_km, "departements": depts, "national": national},
    )
    jobs.submit_job(
        job_id,
        lambda: run_sirene_scan(
            point_ref=point_ref,
            rayon_km=rayon_km,
            departements=depts,
            nafs=nafs,
            insee_token=insee_token,
            include_mairies=include_mairies,
            include_associations=include_associations,
            national=national,
        ),
        kind="sirene",
    )
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/scan/serpapi", methods=["POST"])
def scan_serpapi():
    data = request.get_json(force=True) or {}
    sirets = data.get("sirets") or []
    # Raccourci : une seule entreprise
    if data.get("siret"):
        sirets = [str(data["siret"])]
    serpapi_key = (
        data.get("SERPAPI_KEY")
        or data.get("SERPAPI_TOKEN")
        or env_or_config("SERPAPI_KEY", "SERPAPI_TOKEN")
    )
    if not serpapi_key:
        logger.error("Scan SerpAPI refusé : clé manquante")
        return jsonify({"error": "Clé SerpAPI (SERPAPI_KEY / SERPAPI_TOKEN) manquante"}), 400

    use_ollama = data.get("use_ollama", True)
    logger.info(
        "POST /api/scan/serpapi — sirets=%s ollama=%s (dispo=%s modèle=%s)",
        sirets if sirets else "tous non scannés",
        use_ollama,
        ollama_available(),
        OLLAMA_MODEL,
    )
    job_id = jobs.create_job("serpapi", params={"sirets": sirets})
    jobs.submit_job(
        job_id,
        lambda: {
            "ollama": ollama_available(),
            **run_serpapi_scan(
                serpapi_key=serpapi_key,
                sirets=sirets or None,
                use_ollama=bool(use_ollama),
            ),
        },
        kind="serpapi",
    )
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/scan/serpapi/<siret>", methods=["POST"])
def scan_serpapi_one(siret: str):
    """Scan SerpAPI forcé sur une seule entreprise (même si déjà scannée)."""
    data = request.get_json(force=True) or {}
    serpapi_key = (
        data.get("SERPAPI_KEY")
        or data.get("SERPAPI_TOKEN")
        or env_or_config("SERPAPI_KEY", "SERPAPI_TOKEN")
    )
    if not serpapi_key:
        return jsonify({"error": "Clé SerpAPI (SERPAPI_KEY / SERPAPI_TOKEN) manquante"}), 400

    use_ollama = data.get("use_ollama", True)
    logger.info("POST /api/scan/serpapi/%s — scan unitaire", siret)
    try:
        result = run_serpapi_scan(
            serpapi_key=serpapi_key,
            sirets=[siret],
            use_ollama=bool(use_ollama),
        )
    except Exception as e:
        logger.exception("Scan SerpAPI unitaire échoué (%s)", siret)
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "ollama": ollama_available(), **result})


@bp.route("/api/scan/dirigeants", methods=["POST"])
def scan_dirigeants():
    """Remplit contact_prenom / nom / poste via l'API Recherche d'Entreprises."""
    data = request.get_json(force=True) or {}
    sirets = data.get("sirets") or []
    if data.get("siret"):
        sirets = [str(data["siret"])]
    force = bool(data.get("force", False))
    limit = data.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None

    logger.info(
        "POST /api/scan/dirigeants — sirets=%s force=%s limit=%s",
        sirets if sirets else "tous non scannés",
        force,
        limit,
    )
    job_id = jobs.create_job("dirigeants", params={"sirets": sirets, "force": force, "limit": limit})
    jobs.submit_job(
        job_id,
        lambda: run_dirigeants_scan(sirets=sirets or None, force=force, limit=limit),
        kind="dirigeants",
    )
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/scan/dirigeants/<siret>", methods=["POST"])
def scan_dirigeants_one(siret: str):
    """Récupère le dirigeant pour une seule entreprise."""
    data = request.get_json(force=True) or {}
    force = bool(data.get("force", True))
    logger.info("POST /api/scan/dirigeants/%s — force=%s", siret, force)
    try:
        result = run_dirigeants_scan(sirets=[siret], force=force)
    except Exception as e:
        logger.exception("Scan dirigeants unitaire échoué (%s)", siret)
        return jsonify({"error": str(e)}), 500

    detail = (result.get("details") or [{}])[0]
    return jsonify({"ok": True, **result, **detail})


@bp.route("/api/scan/contacts", methods=["POST"])
def scan_contacts():
    """Cherche contacts RH / tech via SerpAPI (LinkedIn people)."""
    data = request.get_json(force=True) or {}
    sirets = data.get("sirets") or []
    if data.get("siret"):
        sirets = [str(data["siret"])]
    force = bool(data.get("force", False))
    limit = data.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = None
    serpapi_key = (
        data.get("SERPAPI_KEY")
        or data.get("SERPAPI_TOKEN")
        or env_or_config("SERPAPI_KEY", "SERPAPI_TOKEN")
    )
    if not serpapi_key:
        return jsonify({"error": "Clé SerpAPI (SERPAPI_KEY / SERPAPI_TOKEN) manquante"}), 400

    use_ollama = data.get("use_ollama", False)
    logger.info(
        "POST /api/scan/contacts — sirets=%s force=%s limit=%s",
        sirets if sirets else "éligibles",
        force,
        limit,
    )
    job_id = jobs.create_job("contacts", params={"sirets": sirets, "force": force, "limit": limit})
    jobs.submit_job(
        job_id,
        lambda: run_contacts_scan(
            serpapi_key=serpapi_key,
            sirets=sirets or None,
            force=force,
            limit=limit,
            use_ollama=False,
        ),
        kind="contacts",
    )
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/scan/contacts/<siret>", methods=["POST"])
def scan_contacts_one(siret: str):
    data = request.get_json(force=True) or {}
    force = bool(data.get("force", True))
    serpapi_key = (
        data.get("SERPAPI_KEY")
        or data.get("SERPAPI_TOKEN")
        or env_or_config("SERPAPI_KEY", "SERPAPI_TOKEN")
    )
    if not serpapi_key:
        return jsonify({"error": "Clé SerpAPI manquante"}), 400
    try:
        result = run_contacts_scan(
            serpapi_key=serpapi_key,
            sirets=[siret],
            force=force,
            use_ollama=False,
        )
    except Exception as e:
        logger.exception("Scan contact unitaire échoué (%s)", siret)
        return jsonify({"error": str(e)}), 500
    detail = (result.get("details") or [{}])[0]
    return jsonify({"ok": True, **result, **detail})


@bp.route("/api/scan/frenchtech", methods=["POST"])
def scan_frenchtech():
    """Importe l'annuaire LA French Tech Toulon (scraping + résolution SIRET)."""
    data = request.get_json(force=True) or {}
    tech_only = bool(data.get("tech_only", False))
    resolve_siret = data.get("resolve_siret")
    if resolve_siret is None:
        resolve_siret = True
    resolve_emails = data.get("resolve_emails")
    if resolve_emails is None:
        resolve_emails = True
    geocode = data.get("geocode")
    if geocode is None:
        geocode = True

    logger.info(
        "POST /api/scan/frenchtech — tech_only=%s resolve_siret=%s resolve_emails=%s",
        tech_only,
        resolve_siret,
        resolve_emails,
    )
    job_id = jobs.create_job("frenchtech", params={"tech_only": tech_only})
    jobs.submit_job(
        job_id,
        lambda: run_frenchtech_scan(
            tech_only=tech_only,
            resolve_siret=bool(resolve_siret),
            resolve_emails=bool(resolve_emails),
            geocode=bool(geocode),
        ),
        kind="frenchtech",
    )
    return jsonify({"job_id": job_id}), 202


@bp.route("/api/mark-hors-champs", methods=["POST"])
def mark_hors_champs():
    """Marque en hors_champs les entreprises hors périmètre IT (NAF / thème)."""
    data = request.get_json(force=True) or {}
    sirets = data.get("sirets") or []
    if data.get("siret"):
        sirets = [str(data["siret"])]

    logger.info("POST /api/mark-hors-champs — sirets=%s", sirets if sirets else "toutes à postuler")
    try:
        result = mark_hors_champs_entreprises(sirets=sirets or None)
    except Exception as e:
        logger.exception("Marquage hors_champs échoué")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


@bp.route("/api/clean", methods=["POST"])
def clean_data():
    data = request.get_json(force=True) or {}
    apply_filter = data.get("apply_effectif_filter", True)
    logger.info("POST /api/clean — filtre_effectif=%s", apply_filter)
    try:
        result = clean_entreprises(apply_effectif_filter=bool(apply_filter))
    except Exception as e:
        logger.exception("Nettoyage échoué")
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True, **result})


@bp.route("/api/prune", methods=["POST"])
def prune_entreprises():
    """
    Filtrage avancé (équivalent de prune_entreprises.py) :
    - supprime les structures trop petites (effectif)
    - calcule les trajets GPS sans péage et supprime celles trop loin
    """
    data = request.get_json(force=True) or {}
    try:
        min_employees = int(data.get("min_employees", 3))
    except (TypeError, ValueError):
        return jsonify({"error": "min_employees invalide"}), 400
    try:
        max_travel_min = float(data.get("max_travel_min", 45))
    except (TypeError, ValueError):
        return jsonify({"error": "max_travel_min invalide"}), 400

    origin = (data.get("origin") or "").strip() or None
    force_recompute = bool(data.get("force_recompute", False))
    delete_unavailable = bool(data.get("delete_unavailable", False))
    delete_unknown_employees = bool(data.get("delete_unknown_employees", False))
    try:
        sleep = float(data.get("sleep", 0.05))
    except (TypeError, ValueError):
        sleep = 0.05

    logger.info(
        "POST /api/prune — min_employees=%s max_travel_min=%s origin=%s force=%s delete_unknown=%s",
        min_employees,
        max_travel_min,
        origin or "(config)",
        force_recompute,
        delete_unknown_employees,
    )
    job_id = jobs.create_job(
        "prune", params={"min_employees": min_employees, "max_travel_min": max_travel_min}
    )
    jobs.submit_job(
        job_id,
        lambda: run_prune(
            min_employees=min_employees,
            max_travel_min=max_travel_min,
            origin=origin,
            force_recompute=force_recompute,
            sleep=max(0.0, sleep),
            delete_unavailable_travel=delete_unavailable,
            delete_unknown_employees=delete_unknown_employees,
        ),
        kind="prune",
    )
    return jsonify({"job_id": job_id}), 202
