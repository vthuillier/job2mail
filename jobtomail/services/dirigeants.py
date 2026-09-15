"""Récupération des dirigeants via l'API Recherche d'Entreprises (data.gouv)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from jobtomail import db
from jobtomail.constants import RECHERCHE_ENTREPRISES_URL

logger = logging.getLogger(__name__)

# Qualités prioritaires (score décroissant) pour choisir le bon contact
_QUALITE_SCORES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"pr[eé]sident.*directeur\s+g[eé]n[eé]ral|pdg", re.I), 100),
    (re.compile(r"pr[eé]sident", re.I), 90),
    (re.compile(r"directeur\s+g[eé]n[eé]ral|^dg\b", re.I), 80),
    (re.compile(r"g[eé]rant", re.I), 75),
    (re.compile(r"fondateur|co-?fondateur", re.I), 70),
    (re.compile(r"directeur", re.I), 50),
    (re.compile(r"associ[eé]", re.I), 30),
    (re.compile(r"administrateur", re.I), 10),
]

_SKIP_QUALITES = re.compile(
    r"commissaire\s+aux\s+comptes|liquidateur|mandataire\s+judiciaire",
    re.I,
)


def _clean_name_token(value: str) -> str:
    """Retire les parenthèses type 'DALSASS (DALSASS)' → 'DALSASS'."""
    value = re.sub(r"\([^)]*\)", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def _title_name(value: str) -> str:
    """JEAN-PIERRE → Jean-Pierre ; LE BIHAN → Le Bihan."""
    value = _clean_name_token(value)
    if not value:
        return ""
    return " ".join(
        "-".join(part.capitalize() for part in word.split("-"))
        for word in value.lower().split()
    )


def _first_prenom(prenoms: str) -> str:
    raw = _clean_name_token(prenoms)
    if not raw:
        return ""
    return _title_name(raw.split()[0])


def _score_qualite(qualite: str) -> int:
    q = qualite or ""
    if _SKIP_QUALITES.search(q):
        return -1
    for pattern, score in _QUALITE_SCORES:
        if pattern.search(q):
            return score
    return 5


def pick_best_dirigeant(dirigeants: list[dict]) -> dict[str, str] | None:
    """Choisit la meilleure personne physique parmi les dirigeants."""
    candidates: list[tuple[int, dict[str, str]]] = []
    for d in dirigeants:
        if (d.get("type_dirigeant") or "").lower() != "personne physique":
            continue
        nom = _title_name(d.get("nom") or "")
        prenom = _first_prenom(d.get("prenoms") or "")
        if not nom or not prenom:
            continue
        qualite = (d.get("qualite") or "").strip()
        score = _score_qualite(qualite)
        if score < 0:
            continue
        candidates.append(
            (
                score,
                {
                    "contact_prenom": prenom,
                    "contact_nom": nom,
                    "contact_poste": qualite,
                },
            )
        )

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def fetch_dirigeants(siren: str) -> list[dict]:
    """Appelle l'API Recherche d'Entreprises pour un SIREN."""
    siren = re.sub(r"\D", "", siren or "")
    if len(siren) != 9:
        raise ValueError(f"SIREN invalide : {siren!r}")

    response = requests.get(
        RECHERCHE_ENTREPRISES_URL,
        params={"q": siren, "per_page": 1},
        timeout=20,
    )
    if response.status_code == 429:
        logger.warning("Rate limit Recherche Entreprises (429) — pause 2s")
        time.sleep(2)
        response = requests.get(
            RECHERCHE_ENTREPRISES_URL,
            params={"q": siren, "per_page": 1},
            timeout=20,
        )
    if response.status_code != 200:
        logger.error(
            "Recherche Entreprises HTTP %s pour SIREN=%s : %s",
            response.status_code,
            siren,
            response.text[:200],
        )
        raise RuntimeError(f"Erreur API Recherche Entreprises ({response.status_code})")

    results = response.json().get("results") or []
    if not results:
        return []
    # Sécurité : matcher exactement le SIREN demandé
    for row in results:
        if row.get("siren") == siren:
            return list(row.get("dirigeants") or [])
    return list(results[0].get("dirigeants") or [])


def enrich_entreprise_dirigeant(
    user_id: int,
    *,
    siret: str,
    siren: str,
    force: bool = False,
) -> dict[str, Any]:
    """Remplit contact_prenom / nom / poste pour une entreprise."""
    existing = db.get_entreprise(user_id, siret)
    if not existing:
        return {"siret": siret, "ok": False, "error": "introuvable"}

    if not force:
        if existing["contact_prenom"] or existing["contact_nom"]:
            if not existing["dirigeants_scanned"]:
                db.mark_dirigeants_scanned(user_id, siret)
            return {
                "siret": siret,
                "ok": True,
                "skipped": True,
                "reason": "déjà renseigné",
                "contact_prenom": existing["contact_prenom"],
                "contact_nom": existing["contact_nom"],
                "contact_poste": existing["contact_poste"],
            }
        if existing["dirigeants_scanned"]:
            return {
                "siret": siret,
                "ok": True,
                "skipped": True,
                "reason": "déjà scanné (aucun dirigeant)",
            }

    try:
        dirigeants = fetch_dirigeants(siren)
    except Exception as e:
        logger.exception("Échec fetch dirigeants SIREN=%s", siren)
        db.mark_dirigeants_scanned(user_id, siret)
        return {"siret": siret, "ok": False, "error": str(e)}

    picked = pick_best_dirigeant(dirigeants)
    if not picked:
        logger.info(
            "Aucun dirigeant personne physique pour %s (SIREN=%s, %d entrée(s))",
            existing["denomination"],
            siren,
            len(dirigeants),
        )
        db.mark_dirigeants_scanned(user_id, siret)
        return {
            "siret": siret,
            "ok": True,
            "found": False,
            "dirigeants_count": len(dirigeants),
        }

    db.apply_dirigeant(user_id, siret, picked)
    logger.info(
        "Dirigeant %s %s (%s) → %s",
        picked["contact_prenom"],
        picked["contact_nom"],
        picked["contact_poste"],
        existing["denomination"],
    )
    return {
        "siret": siret,
        "ok": True,
        "found": True,
        **picked,
    }


def run_dirigeants_scan(
    user_id: int,
    *,
    sirets: list[str] | None = None,
    force: bool = False,
    limit: int | None = None,
) -> dict[str, Any]:
    """Enrichit en lot les entreprises sans contact (ou une liste de SIRET)."""
    rows = db.entreprises_to_dirigeants(user_id, sirets=sirets, force=force)
    if limit is not None:
        rows = rows[: max(0, limit)]

    logger.info(
        "Scan dirigeants — %d entreprise(s) (force=%s)",
        len(rows),
        force,
    )

    filled = 0
    skipped = 0
    not_found = 0
    errors = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        siren = (row["siren"] or "")[:9]
        if len(siren) != 9:
            # Fallback : SIREN = 9 premiers chiffres du SIRET
            siret_digits = re.sub(r"\D", "", row["siret"] or "")
            siren = siret_digits[:9]
        if len(siren) != 9:
            errors += 1
            details.append({"siret": row["siret"], "ok": False, "error": "SIREN manquant"})
            continue

        result = enrich_entreprise_dirigeant(
            user_id,
            siret=row["siret"],
            siren=siren,
            force=force,
        )
        details.append(result)
        if result.get("skipped"):
            skipped += 1
        elif result.get("found"):
            filled += 1
        elif result.get("ok") and result.get("found") is False:
            not_found += 1
        elif not result.get("ok"):
            errors += 1
        time.sleep(0.2)

    logger.info(
        "Scan dirigeants terminé — filled=%d not_found=%d skipped=%d errors=%d",
        filled,
        not_found,
        skipped,
        errors,
    )
    return {
        "processed": len(rows),
        "filled": filled,
        "not_found": not_found,
        "skipped": skipped,
        "errors": errors,
        "details": details,
    }
