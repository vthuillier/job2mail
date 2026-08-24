"""Nettoyage & scoring des entreprises post-Sirene."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import Any

from jobtomail import db

logger = logging.getLogger(__name__)

# Filtre : trop petit pour candidater utilement (0 à 5 salariés)
EFFECTIFS_EXCLUS = {"00", "01", "02"}

POIDS_EFFECTIF = 0.35
POIDS_ANCIENNETE = 0.25
POIDS_SIEGE = 0.15
POIDS_CATEGORIE = 0.1
POIDS_KEYWORDS = 0.15

SCORE_EFFECTIF = {
    "NN": 0.3,
    "00": 0.0,
    "01": 0.0,
    "02": 0.0,
    "03": 0.5,
    "11": 0.8,
    "12": 1.0,
    "21": 1.0,
    "22": 0.9,
    "31": 0.8,
    "32": 0.7,
    "41": 0.6,
    "42": 0.5,
    "51": 0.4,
    "52": 0.3,
    "53": 0.3,
}

SCORE_CATEGORIE = {
    "PME": 1.0,
    "ETI": 0.7,
    "GE": 0.4,
    "": 0.5,
    None: 0.5,
}


def _anciennete_score(date_creation: str | None) -> float:
    if not date_creation:
        return 0.4
    try:
        ans = date.today().year - date.fromisoformat(date_creation[:10]).year
    except ValueError:
        return 0.4
    if ans < 2:
        return 0.3
    if ans < 5:
        return 0.7
    if ans < 15:
        return 1.0
    if ans < 30:
        return 0.8
    return 0.5


def _keyword_match_score(row: dict[str, Any], keywords: list[str] | None) -> float:
    """Similarité (substring match) entre mots-clés du CV et libellé NAF / dénomination.
    Neutre (0.5) si aucun profil CV n'est disponible — ne pénalise pas en son absence.
    """
    if not keywords:
        return 0.5
    haystack = f"{row.get('naf_libelle') or ''} {row.get('denomination') or ''}".lower()
    hits = sum(1 for kw in keywords if kw and kw.lower() in haystack)
    return min(1.0, hits / max(1, len(keywords) * 0.15))


def score_pertinence(row: dict[str, Any], *, cv_keywords: list[str] | None = None) -> float:
    nature = (row.get("nature") or "entreprise").strip() or "entreprise"
    if nature == "mairie":
        # Collectivités : score stable, utile pour candidater SI / numérique
        siege = 1.0 if row.get("est_siege") else 0.6
        return round((0.7 * siege + 0.3) * 100, 1)
    if nature == "association":
        eff = SCORE_EFFECTIF.get(row.get("effectif_code") or "NN", 0.3)
        anc = _anciennete_score(row.get("date_creation"))
        siege = 1.0 if row.get("est_siege") else 0.5
        return round((0.45 * eff + 0.25 * anc + 0.3 * siege) * 100, 1)

    eff = SCORE_EFFECTIF.get(row.get("effectif_code") or "NN", 0.3)
    anc = _anciennete_score(row.get("date_creation"))
    siege = 1.0 if row.get("est_siege") else 0.4
    cat = SCORE_CATEGORIE.get(row.get("categorie_entreprise") or "", 0.5)
    kw = _keyword_match_score(row, cv_keywords)
    score = (
        POIDS_EFFECTIF * eff
        + POIDS_ANCIENNETE * anc
        + POIDS_SIEGE * siege
        + POIDS_CATEGORIE * cat
        + POIDS_KEYWORDS * kw
    )
    return round(score * 100, 1)


def _pick_best_etablissement(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Garde le siège en priorité, sinon le plus ancien établissement."""

    def key(r: dict) -> tuple:
        siege = 0 if r.get("est_siege") else 1
        date_c = r.get("date_creation") or "9999-99-99"
        return (siege, date_c)

    best = sorted(rows, key=key)[0]
    if len(rows) > 1:
        autres = [
            r.get("adresse")
            for r in rows
            if r.get("siret") != best.get("siret") and r.get("adresse")
        ]
        if autres:
            note = "Autres établissements : " + " | ".join(autres[:5])
            existing = (best.get("notes") or "").strip()
            if note not in existing:
                best["notes"] = f"{existing}\n{note}".strip() if existing else note
    return best


def clean_entreprises(*, apply_effectif_filter: bool = True) -> dict[str, Any]:
    """
    Nettoie la base :
    1. filtre les trop petites structures
    2. déduplique par SIREN (garde le meilleur établissement)
    3. calcule un score de pertinence
    """
    rows = [dict(r) for r in db.list_entreprises()]
    before = len(rows)
    logger.info("Nettoyage — %d entreprise(s) en entrée", before)

    if apply_effectif_filter:
        filtered = []
        exclus = 0
        for r in rows:
            nature = (r.get("nature") or "entreprise").strip() or "entreprise"
            code = (r.get("effectif_code") or "NN").strip()
            # Mairies : toujours conservées. Associations employeuses : on garde
            # sauf 0 salarié. Entreprises : filtre classique 0–5 salariés.
            if nature == "mairie":
                filtered.append(r)
            elif nature == "association":
                if code == "00":
                    exclus += 1
                else:
                    filtered.append(r)
            elif code in EFFECTIFS_EXCLUS:
                exclus += 1
            else:
                filtered.append(r)
        logger.info("Filtre effectif : %d exclue(s) (codes %s)", exclus, sorted(EFFECTIFS_EXCLUS))
    else:
        filtered = rows
        exclus = 0

    by_siren: dict[str, list[dict]] = defaultdict(list)
    sans_siren: list[dict] = []
    for r in filtered:
        siren = (r.get("siren") or "").strip()
        if siren:
            by_siren[siren].append(r)
        else:
            sans_siren.append(r)

    deduped: list[dict] = []
    duplicates_removed = 0
    for siren, group in by_siren.items():
        if len(group) > 1:
            duplicates_removed += len(group) - 1
            logger.debug("Dédup SIREN %s : %d → 1", siren, len(group))
        deduped.append(_pick_best_etablissement(group))
    deduped.extend(sans_siren)

    cv_keywords = None
    try:
        from jobtomail.services.cv_profile import extract_cv_profile

        profile = extract_cv_profile()
        cv_keywords = profile["keywords"] if profile else None
    except Exception:
        logger.exception("Extraction profil CV indisponible — scoring sans mots-clés")

    for r in deduped:
        r["score_pertinence"] = score_pertinence(r, cv_keywords=cv_keywords)

    deduped.sort(
        key=lambda r: (-(r.get("score_pertinence") or 0), r.get("denomination") or "")
    )

    keep_sirets = {r["siret"] for r in deduped}
    stats = db.replace_entreprises_cleaned(deduped, keep_sirets)

    logger.info(
        "Nettoyage terminé — avant=%d après=%d exclus_effectif=%d dedup=%d",
        before,
        len(deduped),
        exclus,
        duplicates_removed,
    )
    return {
        "before": before,
        "after": len(deduped),
        "excluded_effectif": exclus,
        "duplicates_removed": duplicates_removed,
        **stats,
    }
