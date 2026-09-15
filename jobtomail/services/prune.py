"""Filtrage avancé : effectif minimum + durée de trajet réelle (GPS / IGN)."""

from __future__ import annotations

import logging
import time
from typing import Any

from jobtomail import db
from jobtomail.constants import PRUNE_PROTECTED_STATUSES
from jobtomail.services.geo import geocode_adresse, get_route_info, normalize_location_label

logger = logging.getLogger(__name__)


def _is_prune_protected(item: dict[str, Any]) -> bool:
    status = (item.get("status") or "a_postuler").strip()
    return status in PRUNE_PROTECTED_STATUSES


# Code INSEE tranche effectifs → borne basse d'employés.
MIN_EMPLOYEES_BY_CODE = {
    "00": 0,
    "01": 1,
    "02": 3,
    "03": 6,
    "11": 10,
    "12": 20,
    "21": 50,
    "22": 100,
    "31": 200,
    "32": 250,
    "41": 500,
    "42": 1000,
    "51": 2000,
    "52": 5000,
    "53": 10000,
}


def run_prune(
    user_id: int,
    *,
    min_employees: int = 3,
    max_travel_min: float = 45.0,
    origin: str | None = None,
    force_recompute: bool = False,
    sleep: float = 0.05,
    delete_unavailable_travel: bool = False,
    delete_unknown_employees: bool = False,
) -> dict[str, Any]:
    """
    1. Supprime les structures dont l'effectif connu est sous le seuil
       (les mairies sont conservées ; NN configurable).
    2. Calcule le trajet routier sans péage depuis l'origine, met en cache,
       puis supprime celles au-delà de max_travel_min.
    """
    t_start = time.monotonic()
    origin_label = (origin or db.get_user_config_value(user_id, "point_ref", "La Crau") or "La Crau").strip()
    origin_norm = normalize_location_label(origin_label)

    logger.info(
        "Prune — origin=%s min_employees=%d max_travel_min=%.1f force=%s delete_unknown=%s",
        origin_label,
        min_employees,
        max_travel_min,
        force_recompute,
        delete_unknown_employees,
    )

    origin_coords = geocode_adresse(origin_label, origin_label)
    if not origin_coords:
        raise ValueError(f"Origine introuvable au géocodage : {origin_label}")

    rows = [dict(r) for r in db.list_entreprises(user_id)]
    initial_count = len(rows)
    protected_sirets = {item["siret"] for item in rows if _is_prune_protected(item)}
    skipped_protected = len(protected_sirets)

    # Étape 1 — filtre effectif
    deleted_small = 0
    for item in rows:
        if item["siret"] in protected_sirets:
            continue
        nature = (item.get("nature") or "entreprise").strip() or "entreprise"
        if nature == "mairie":
            continue
        code = (item.get("effectif_code") or "").strip()
        if code == "NN" and delete_unknown_employees:
            db.delete_entreprise(user_id, item["siret"])
            deleted_small += 1
            continue
        min_emp = MIN_EMPLOYEES_BY_CODE.get(code)
        if min_emp is not None and min_emp < min_employees:
            db.delete_entreprise(user_id, item["siret"])
            deleted_small += 1

    kept_rows = [dict(r) for r in db.list_entreprises(user_id)]
    logger.info(
        "Prune effectif — %d supprimée(s), %d restante(s)",
        deleted_small,
        len(kept_rows),
    )

    # Étape 2 — trajets GPS
    processed = 0
    computed = 0
    deleted_travel = 0
    unavailable = 0
    cached_hits = 0

    for item in kept_rows:
        siret = item["siret"]
        nom = item.get("denomination") or siret

        if item["siret"] in protected_sirets:
            processed += 1
            continue

        cached_origin = normalize_location_label(item.get("travel_origin"))
        cached_ok = (
            not force_recompute
            and cached_origin == origin_norm
            and item.get("travel_duration_min") is not None
            and item.get("travel_distance_km") is not None
            and bool(item.get("travel_without_tolls"))
        )

        if cached_ok:
            duration = float(item["travel_duration_min"])
            cached_hits += 1
            if duration > max_travel_min:
                db.delete_entreprise(user_id, siret)
                deleted_travel += 1
            processed += 1
            continue

        lat = item.get("latitude")
        lon = item.get("longitude")
        if lat is None or lon is None:
            coords = geocode_adresse(item.get("adresse"), item.get("commune"))
            if coords:
                lat, lon = coords
                db.update_entreprise_coords(user_id, siret, lat, lon)
            else:
                logger.warning("Géocodage échoué pour %s — trajet indisponible", nom)
                unavailable += 1
                processed += 1
                if delete_unavailable_travel:
                    db.delete_entreprise(user_id, siret)
                    deleted_travel += 1
                continue

        route = get_route_info(
            origin_coords[0],
            origin_coords[1],
            float(lat),
            float(lon),
            avoid_tolls=True,
        )
        processed += 1
        if not route:
            logger.warning("Route introuvable pour %s", nom)
            unavailable += 1
            if delete_unavailable_travel:
                db.delete_entreprise(user_id, siret)
                deleted_travel += 1
            continue

        duration = float(route["duration_min"])
        db.update_entreprise_travel(
            user_id,
            siret,
            origin=origin_label,
            duration_min=duration,
            distance_km=float(route["distance_km"]),
            without_tolls=True,
        )
        computed += 1

        if duration > max_travel_min:
            db.delete_entreprise(user_id, siret)
            deleted_travel += 1

        if sleep > 0:
            time.sleep(sleep)

    final_count = len(db.list_entreprises(user_id))
    elapsed = time.monotonic() - t_start

    result = {
        "ok": True,
        "origin": origin_label,
        "initial": initial_count,
        "deleted_lt_min_employees": deleted_small,
        "after_headcount_filter": len(kept_rows),
        "travel_processed": processed,
        "travel_computed": computed,
        "travel_cached": cached_hits,
        "travel_unavailable": unavailable,
        "deleted_gt_max_travel": deleted_travel,
        "skipped_protected": skipped_protected,
        "final": final_count,
        "elapsed_s": round(elapsed, 1),
        "min_employees": min_employees,
        "max_travel_min": max_travel_min,
        "force_recompute": force_recompute,
        "delete_unknown_employees": delete_unknown_employees,
    }
    logger.info(
        "Prune terminé — initial=%d final=%d (−%d effectif, −%d trajet) en %.1fs",
        initial_count,
        final_count,
        deleted_small,
        deleted_travel,
        elapsed,
    )
    return result
