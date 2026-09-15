"""CRUD entreprises."""

from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import date
from math import ceil

from flask import Blueprint, Response, jsonify, request

from jobtomail import db
from jobtomail.constants import TRANCHE_EFFECTIFS
from jobtomail.services.geo import geocode_adresse, get_route_info, normalize_location_label
from jobtomail.services.pdf_export import build_entreprise_pdf, build_entreprises_pdf
from jobtomail.services.relances import list_relances_dues, relance_status

logger = logging.getLogger(__name__)

bp = Blueprint("entreprises", __name__)

UPDATE_FIELDS = [
    "site_web",
    "linkedin_company",
    "contact_prenom",
    "contact_nom",
    "contact_genre",
    "contact_poste",
    "contact_email",
    "contact_linkedin",
    "contact_source",
    "status",
    "notes",
    "accroche",
    "entretien_date",
    "entretien_next_step",
    "entretien_rappel_at",
    "email_hunter_score",
    "email_quality",
    "email_quality_note",
]

_SIRET_RE = re.compile(r"^\d{14}$")


def _normalize_siret(raw: str | None) -> str | None:
    if raw is None:
        return None
    digits = re.sub(r"\D", "", str(raw).strip())
    return digits or None


def _make_manual_siret() -> str:
    """Identifiant synthétique (préfixe M) pour les ajouts sans SIRET réel."""
    return "M" + uuid.uuid4().hex[:13].upper()


def _pdf_slug(name: str | None) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "entreprise").strip()).strip("-").lower()
    return slug[:60] or "entreprise"


def _current_travel_origin(payload: dict | None = None) -> str:
    payload = payload or {}
    return (payload.get("origin") or db.get_config_value("point_ref", "La Crau") or "La Crau").strip()


def _to_float(raw: str | None) -> float | None:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_filters_from_query() -> dict:
    args = request.args
    return {
        "status": args.get("status") or "",
        "search": args.get("search") or "",
        "naf": args.getlist("naf"),
        "effectif": args.getlist("effectif"),
        "commune": args.get("commune") or "",
        "categorie": args.get("categorie") or "",
        "nature": args.get("nature") or "",
        "score_min": _to_float(args.get("score_min")),
        "travel_max_min": _to_float(args.get("travel_max_min")),
        "siege_only": args.get("siege_only") == "1",
        "has_contact": args.get("has_contact") == "1",
        "has_email": args.get("has_email") == "1",
        "serpapi_scanned": args.get("serpapi_scanned") == "1",
    }


@bp.route("/api/entreprises", methods=["GET"])
def get_entreprises():
    """Liste paginée + filtrée (recherche/filtres portés côté SQL — dataset trop gros pour tout charger)."""
    filters = _parse_filters_from_query()
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1
    try:
        per_page = int(request.args.get("per_page") or 100)
    except ValueError:
        per_page = 100
    per_page = max(1, min(per_page, 500))

    origin = _current_travel_origin() if filters["travel_max_min"] is not None else None
    rows, total = db.list_entreprises_page(filters, origin, page, per_page)

    entreprises = []
    for r in rows:
        item = dict(r)
        item["relance_info"] = relance_status(item)
        entreprises.append(item)

    pages = ceil(total / per_page) if total else 1
    logger.info(
        "GET /api/entreprises — page %d/%d, %d résultat(s) sur %d filtré(s)",
        page,
        pages,
        len(entreprises),
        total,
    )
    return jsonify(
        {"entreprises": entreprises, "total": total, "page": page, "per_page": per_page, "pages": pages}
    )


@bp.route("/api/entreprises/stats", methods=["GET"])
def get_entreprises_stats():
    """Compteurs globaux (cartes stats + badges chips) — indépendants des filtres actifs."""
    counts = db.entreprises_status_counts()
    dues = len(list_relances_dues())
    return jsonify({"counts": counts, "relances_dues": dues})


@bp.route("/api/entreprises/lite", methods=["GET"])
def get_entreprises_lite():
    """Liste allégée non paginée (carte + calcul candidats trajet) — respecte les mêmes filtres."""
    filters = _parse_filters_from_query()
    origin = _current_travel_origin()
    rows = db.list_entreprises_lite(filters, origin)
    return jsonify({"entreprises": [dict(r) for r in rows]})


@bp.route("/api/entreprises/naf-codes-used", methods=["GET"])
def get_naf_codes_used():
    """Codes NAF distincts en base — alimente la checklist du filtre NAF."""
    return jsonify({"codes": db.naf_codes_used()})


@bp.route("/api/entreprises", methods=["POST"])
def create_entreprise():
    """Ajoute manuellement une entreprise (hors scan Sirene)."""
    data = request.get_json(force=True) or {}
    denomination = (data.get("denomination") or "").strip()
    if not denomination:
        return jsonify({"error": "La dénomination est obligatoire"}), 400

    siret = _normalize_siret(data.get("siret"))
    if siret:
        if not _SIRET_RE.match(siret):
            return jsonify({"error": "SIRET invalide (14 chiffres attendus)"}), 400
        if db.get_entreprise(siret):
            return jsonify({"error": f"Une entreprise avec le SIRET {siret} existe déjà"}), 409
    else:
        # Garantir l'unicité d'un identifiant synthétique
        for _ in range(5):
            candidate = _make_manual_siret()
            if not db.get_entreprise(candidate):
                siret = candidate
                break
        if not siret:
            return jsonify({"error": "Impossible de générer un identifiant unique"}), 500

    siren = (data.get("siren") or "").strip() or (siret[:9] if siret.isdigit() and len(siret) == 14 else None)
    adresse = (data.get("adresse") or "").strip() or None
    commune = (data.get("commune") or "").strip() or None
    effectif_code = (data.get("effectif_code") or "NN").strip() or "NN"
    if effectif_code not in TRANCHE_EFFECTIFS:
        effectif_code = "NN"
    effectif_libelle = TRANCHE_EFFECTIFS.get(effectif_code, "Non renseigné")
    naf_code = (data.get("naf_code") or "").strip() or None
    naf_libelle = (data.get("naf_libelle") or "").strip() or None
    categorie = (data.get("categorie_entreprise") or "").strip() or None
    if categorie and categorie not in ("PME", "ETI", "GE"):
        categorie = None

    lat = lon = None
    if adresse or commune:
        coords = geocode_adresse(adresse, commune)
        if coords:
            lat, lon = coords

    row = {
        "siret": siret,
        "siren": siren,
        "denomination": denomination,
        "adresse": adresse,
        "commune": commune,
        "effectif_code": effectif_code,
        "effectif_libelle": effectif_libelle,
        "naf_code": naf_code,
        "naf_libelle": naf_libelle,
        "date_creation": None,
        "est_siege": bool(data.get("est_siege")),
        "categorie_entreprise": categorie,
        "score_pertinence": float(data.get("score_pertinence") or 0),
        "latitude": lat,
        "longitude": lon,
        "site_web": (data.get("site_web") or "").strip() or None,
        "linkedin_company": (data.get("linkedin_company") or "").strip() or None,
        "notes": (data.get("notes") or "").strip() or None,
        "status": (data.get("status") or "a_postuler").strip() or "a_postuler",
    }

    try:
        db.insert_entreprise(row)
    except db.DuplicateSiretError:
        return jsonify({"error": f"Une entreprise avec le SIRET {siret} existe déjà"}), 409

    created = db.get_entreprise(siret)
    item = dict(created) if created else row
    item["relance_info"] = relance_status(item)
    logger.info("POST /api/entreprises — %s (%s)", denomination, siret)
    return jsonify({"ok": True, "entreprise": item}), 201


@bp.route("/api/entretiens", methods=["GET"])
def get_entretiens():
    rows = db.list_entretiens_a_suivre()
    return jsonify({"ok": True, "count": len(rows), "entretiens": rows})


@bp.route("/api/entreprises/geocode", methods=["POST"])
def geocode_entreprises():
    """Géocode par lots les entreprises sans coordonnées (BAN + repli commune)."""
    data = request.get_json(force=True) or {}
    limit = min(int(data.get("limit") or 40), 80)
    rows = db.entreprises_sans_coords(limit)
    geocoded = 0
    for row in rows:
        coords = geocode_adresse(row["adresse"], row["commune"])
        if coords:
            db.update_entreprise_coords(row["siret"], coords[0], coords[1])
            geocoded += 1
        else:
            db.mark_geocode_failed(row["siret"])
        time.sleep(0.12)
    remaining = db.count_entreprises_sans_coords()
    logger.info("Géocodage — %d/%d OK, %d restant(s)", geocoded, len(rows), remaining)
    return jsonify({"ok": True, "geocoded": geocoded, "processed": len(rows), "remaining": remaining})


@bp.route("/api/entreprises/trajets", methods=["POST"])
def compute_travel_times():
    """Calcule les trajets routiers sans péage depuis le point de référence."""
    data = request.get_json(force=True) or {}
    sirets = [str(s).strip() for s in (data.get("sirets") or []) if str(s).strip()]
    force = bool(data.get("force"))
    origin_label = _current_travel_origin(data)
    origin_norm = normalize_location_label(origin_label)

    origin_coords = geocode_adresse(origin_label, origin_label)
    if not origin_coords:
        return jsonify({"error": f"Point de départ introuvable : {origin_label}"}), 400

    rows = db.list_entreprises()
    if sirets:
        wanted = set(sirets)
        rows = [row for row in rows if row["siret"] in wanted]

    results = {}
    processed = 0
    refreshed = 0
    unavailable = 0

    for row in rows:
        item = dict(row)
        siret = item["siret"]
        cached_origin = normalize_location_label(item.get("travel_origin"))
        cached_ok = (
            not force
            and cached_origin == origin_norm
            and item.get("travel_duration_min") is not None
            and item.get("travel_distance_km") is not None
            and bool(item.get("travel_without_tolls"))
        )
        if cached_ok:
            results[siret] = {
                "duration_min": item.get("travel_duration_min"),
                "distance_km": item.get("travel_distance_km"),
                "cached": True,
            }
            continue

        lat = item.get("latitude")
        lon = item.get("longitude")
        if lat is None or lon is None:
            coords = geocode_adresse(item.get("adresse"), item.get("commune"))
            if coords:
                lat, lon = coords
                db.update_entreprise_coords(siret, lat, lon)
            else:
                unavailable += 1
                results[siret] = {"error": "Adresse entreprise introuvable"}
                continue

        route = get_route_info(origin_coords[0], origin_coords[1], float(lat), float(lon), avoid_tolls=True)
        processed += 1
        if not route:
            unavailable += 1
            results[siret] = {"error": "Calcul du trajet impossible"}
            continue

        db.update_entreprise_travel(
            siret,
            origin=origin_label,
            duration_min=route["duration_min"],
            distance_km=route["distance_km"],
            without_tolls=True,
        )
        refreshed += 1
        results[siret] = {
            "duration_min": route["duration_min"],
            "distance_km": route["distance_km"],
            "cached": False,
        }
        time.sleep(0.1)

    logger.info(
        "Trajets — origin=%s, demandés=%d, calculés=%d, indisponibles=%d",
        origin_label,
        len(rows),
        refreshed,
        unavailable,
    )
    return jsonify(
        {
            "ok": True,
            "origin": origin_label,
            "processed": processed,
            "refreshed": refreshed,
            "unavailable": unavailable,
            "results": results,
        }
    )


@bp.route("/api/entreprises/<siret>", methods=["GET", "PUT", "DELETE"])
def handle_entreprise(siret: str):
    if request.method == "GET":
        entreprise = db.get_entreprise(siret)
        if not entreprise:
            return jsonify({"error": "Entreprise introuvable"}), 404
        item = dict(entreprise)
        item["relance_info"] = relance_status(item)
        return jsonify({"entreprise": item})

    if request.method == "DELETE":
        logger.info("DELETE entreprise %s", siret)
        db.delete_entreprise(siret)
        return jsonify({"ok": True})

    data = request.get_json(force=True) or {}
    existing = db.get_entreprise(siret)
    if not existing:
        logger.warning("PUT entreprise introuvable : %s", siret)
        return jsonify({"error": "Entreprise introuvable"}), 404

    fields = {}
    for f in UPDATE_FIELDS:
        if f in data:
            fields[f] = data[f]
        else:
            fields[f] = existing[f] if f in existing.keys() else None

    db.update_entreprise(siret, fields)
    return jsonify({"ok": True})


@bp.route("/api/entreprises/<siret>/export.pdf", methods=["GET"])
def export_entreprise_pdf(siret: str):
    """Fiche PDF A4 d'une entreprise."""
    entreprise = db.get_entreprise(siret)
    if not entreprise:
        return jsonify({"error": "Entreprise introuvable"}), 404
    pdf_bytes = build_entreprise_pdf(dict(entreprise))
    filename = f"fiche-{_pdf_slug(entreprise.get('denomination'))}.pdf"
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@bp.route("/api/entreprises/export.pdf", methods=["POST"])
def export_entreprises_pdf():
    """Export PDF groupé (sommaire + une fiche par entreprise sélectionnée)."""
    data = request.get_json(force=True) or {}
    sirets = [str(s).strip() for s in (data.get("sirets") or []) if str(s).strip()]
    if not sirets:
        return jsonify({"error": "Aucune entreprise sélectionnée"}), 400

    wanted = set(sirets)
    rows = [dict(r) for r in db.list_entreprises() if r["siret"] in wanted]
    if not rows:
        return jsonify({"error": "Entreprises introuvables"}), 404

    pdf_bytes = build_entreprises_pdf(rows)
    filename = f"export-entreprises-{date.today().isoformat()}.pdf"
    logger.info("Export PDF groupé — %d entreprise(s)", len(rows))
    return Response(
        pdf_bytes,
        mimetype="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )