"""CRUD entreprises."""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid

from flask import Blueprint, jsonify, request

from jobtomail import db
from jobtomail.constants import TRANCHE_EFFECTIFS
from jobtomail.services.geo import geocode_adresse, get_route_info, normalize_location_label
from jobtomail.services.linkedin import build_linkedin_people_url
from jobtomail.services.relances import relance_status

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


def _current_travel_origin(payload: dict | None = None) -> str:
    payload = payload or {}
    return (payload.get("origin") or db.get_config_value("point_ref", "La Crau") or "La Crau").strip()


@bp.route("/api/entreprises", methods=["GET"])
def get_entreprises():
    rows = db.list_entreprises()
    logger.info("GET /api/entreprises — %d résultat(s)", len(rows))
    entreprises = []
    for r in rows:
        item = dict(r)
        item["linkedin_people_url"] = build_linkedin_people_url(
            item.get("denomination") or "",
            item.get("commune") or "",
        )
        item["relance_info"] = relance_status(item)
        entreprises.append(item)
    return jsonify({"entreprises": entreprises})


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
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Une entreprise avec le SIRET {siret} existe déjà"}), 409

    created = db.get_entreprise(siret)
    item = dict(created) if created else row
    item["linkedin_people_url"] = build_linkedin_people_url(
        item.get("denomination") or "",
        item.get("commune") or "",
    )
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


@bp.route("/api/entreprises/<siret>", methods=["PUT", "DELETE"])
def handle_entreprise(siret: str):
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