"""Scan Sirene (INSEE)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from jobtomail.constants import (
    ASSOCIATION_CATEGORIES_JURIDIQUES,
    DEFAULT_NAF_CODES,
    MAIRIE_CATEGORIES_JURIDIQUES,
    SIRENE_BASE_URL,
    TRANCHE_EFFECTIFS,
)
from jobtomail import db
from jobtomail.services.cleaner import clean_entreprises
from jobtomail.services.geo import CommuneInfo, get_communes_dans_rayon

logger = logging.getLogger(__name__)

# Nombre d'éléments regroupés par clause OR dans une même requête Sirene.
# L'API ne documente pas de limite stricte sur le nombre de clauses / la
# longueur de la requête : ces valeurs sont volontairement prudentes, et
# search_batched_by_commune scinde automatiquement le lot en deux si l'API
# répond 400 (requête trop complexe), donc une valeur trop optimiste ici ne
# casse rien — elle coûte juste un aller-retour de plus.
SIRENE_COMMUNE_CHUNK_SIZE = 40
SIRENE_NAF_CHUNK_SIZE = 20
SIRENE_CJ_CHUNK_SIZE = 20


class SireneBadQuery(Exception):
    """Levée quand l'API Sirene rejette une requête (400) — probablement trop complexe."""


def _chunked(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def build_sirene_query_naf(naf_codes: list[str], code_communes: list[str] | None = None) -> str:
    nafs = " OR ".join(naf_codes)
    query = f"periode(activitePrincipaleEtablissement:({nafs}) AND etatAdministratifEtablissement:A)"
    if code_communes:
        query += f" AND codeCommuneEtablissement:({' OR '.join(code_communes)})"
    return query


def build_sirene_query_categorie_juridique(
    categories_juridiques: list[str],
    code_communes: list[str] | None = None,
    *,
    employeur_seulement: bool = False,
) -> str:
    parts = [
        "periode(etatAdministratifEtablissement:A)",
        f"categorieJuridiqueUniteLegale:({' OR '.join(categories_juridiques)})",
    ]
    if code_communes:
        parts.append(f"codeCommuneEtablissement:({' OR '.join(code_communes)})")
    if employeur_seulement:
        parts.append("caractereEmployeurUniteLegale:O")
    return " AND ".join(parts)


def format_adresse(etab: dict) -> str:
    adr = etab.get("adresseEtablissement", {})
    parts = [
        adr.get("numeroVoieEtablissement"),
        adr.get("indiceRepetitionEtablissement"),
        adr.get("typeVoieEtablissement"),
        adr.get("libelleVoieEtablissement"),
    ]
    voie = " ".join(p for p in parts if p)
    cp = adr.get("codePostalEtablissement", "")
    commune = adr.get("libelleCommuneEtablissement", "")
    return f"{voie}, {cp} {commune}".strip(", ")


def parse_denomination(unite: dict) -> str:
    return (
        unite.get("denominationUniteLegale")
        or unite.get("denominationUsuelle1UniteLegale")
        or f"{unite.get('prenom1UniteLegale', '')} {unite.get('nomUniteLegale', '')}".strip()
        or ""
    )


def nature_from_categorie_juridique(categorie_juridique: str | None) -> str:
    code = (categorie_juridique or "").strip()
    if code in MAIRIE_CATEGORIES_JURIDIQUES:
        return "mairie"
    if code in ASSOCIATION_CATEGORIES_JURIDIQUES:
        return "association"
    return "entreprise"


def search_sirene(
    query: str,
    headers: dict,
    *,
    page_size: int = 1000,
    label: str = "",
) -> list[dict]:
    results: list[dict] = []
    curseur = "*"

    while True:
        params = {
            "q": query,
            "nombre": page_size,
            "curseur": curseur,
        }
        response = requests.get(SIRENE_BASE_URL, headers=headers, params=params, timeout=30)

        if response.status_code == 429:
            logger.warning("Rate limit Sirene (429) — pause 5s (%s)", label or query[:80])
            time.sleep(5)
            continue

        if response.status_code == 400:
            raise SireneBadQuery(f"{label or query[:80]}: {response.text[:200]}")

        if response.status_code != 200:
            logger.error(
                "Erreur Sirene %s (%s) : %s",
                response.status_code,
                label or query[:80],
                response.text[:200],
            )
            break

        data = response.json()
        etabs = data.get("etablissements", [])
        results.extend(etabs)

        curseur_suivant = data.get("header", {}).get("curseurSuivant")
        total = data.get("header", {}).get("total", 0)
        if not curseur_suivant or curseur_suivant == curseur or len(results) >= total:
            break
        curseur = curseur_suivant
        time.sleep(0.3)

    return results


def search_batched_by_commune(
    build_query: Callable[[list[str]], str],
    code_communes: list[str],
    headers: dict,
    *,
    label: str,
    page_size: int = 1000,
) -> list[dict]:
    """Recherche groupée sur un lot de communes (une seule requête HTTP au lieu
    d'une par commune). Si l'API rejette la requête (400 — trop de clauses OR /
    requête trop longue), le lot est scindé en deux et chaque moitié est
    retentée récursivement, jusqu'à isoler la commune fautive si besoin."""
    query = build_query(code_communes)
    try:
        return search_sirene(
            query,
            headers,
            page_size=page_size,
            label=f"{label} ({len(code_communes)} commune(s))",
        )
    except SireneBadQuery as exc:
        if len(code_communes) == 1:
            logger.error("Requête Sirene rejetée (%s) : %s", label, exc)
            return []
        mid = len(code_communes) // 2
        logger.info(
            "Requête Sirene trop volumineuse (%d communes, %s) — scission en 2 lots",
            len(code_communes),
            label,
        )
        return search_batched_by_commune(
            build_query, code_communes[:mid], headers, label=label, page_size=page_size
        ) + search_batched_by_commune(
            build_query, code_communes[mid:], headers, label=label, page_size=page_size
        )


def _etab_to_row(
    etab: dict,
    *,
    nom_commune: str,
    lat_commune: Any,
    lon_commune: Any,
    naf_code: str | None = None,
    naf_libelle: str | None = None,
    naf_labels: dict[str, str] | None = None,
    nature_force: str | None = None,
) -> dict[str, Any] | None:
    siret = etab.get("siret")
    if not siret:
        return None

    periodes = etab.get("periodesEtablissement", [])
    etat = periodes[0].get("etatAdministratifEtablissement") if periodes else "?"
    if etat == "F":
        return None

    unite = etab.get("uniteLegale", {})
    denom = parse_denomination(unite)
    if not denom or denom.upper() in ("N/A", "[ND]"):
        return None

    # Une requête groupée couvre plusieurs codes NAF à la fois : on ne peut
    # plus déduire le NAF recherché depuis les paramètres de la requête, donc
    # on lit toujours le NAF courant de l'établissement lui-même.
    if not naf_code and periodes:
        naf_code = periodes[0].get("activitePrincipaleEtablissement") or ""
    naf_code = naf_code or ""

    cj = str(unite.get("categorieJuridiqueUniteLegale") or "").strip() or None
    nature = nature_force or nature_from_categorie_juridique(cj)

    if not naf_libelle:
        if nature_force in ("mairie", "association") and cj:
            # Idem pour les catégories juridiques : le lot regroupe plusieurs
            # codes (ex. les 5 catégories d'association), le libellé exact
            # s'obtient donc depuis la catégorie juridique de CET établissement.
            naf_libelle = (
                MAIRIE_CATEGORIES_JURIDIQUES.get(cj)
                or ASSOCIATION_CATEGORIES_JURIDIQUES.get(cj)
                or ""
            )
        else:
            naf_libelle = (naf_labels or {}).get(naf_code) or DEFAULT_NAF_CODES.get(naf_code, "")
    tranche = unite.get("trancheEffectifsUniteLegale", "NN") or "NN"

    # Libellé plus lisible pour les mairies
    if nature == "mairie" and not denom.upper().startswith("MAIRIE"):
        denom_affiche = f"Mairie — {denom}"
    else:
        denom_affiche = denom

    return {
        "siret": siret,
        "siren": unite.get("siren") or etab.get("siren"),
        "denomination": denom_affiche,
        "adresse": format_adresse(etab),
        "commune": nom_commune,
        "effectif_code": tranche,
        "effectif_libelle": TRANCHE_EFFECTIFS.get(tranche, "Inconnu"),
        "naf_code": naf_code,
        "naf_libelle": naf_libelle,
        "date_creation": etab.get("dateCreationEtablissement"),
        "est_siege": bool(etab.get("etablissementSiege")),
        "categorie_entreprise": unite.get("categorieEntreprise") or "",
        "categorie_juridique": cj,
        "nature": nature,
        "latitude": lat_commune,
        "longitude": lon_commune,
    }


def _insert_etabs(
    user_id: int,
    etabs: list[dict],
    *,
    communes: dict[str, CommuneInfo] | None = None,
    naf_code: str | None = None,
    naf_libelle: str | None = None,
    naf_labels: dict[str, str] | None = None,
    nature_force: str | None = None,
) -> int:
    """Insère les établissements d'un lot groupé. Comme le lot couvre plusieurs
    communes à la fois, la commune de chaque établissement est retrouvée via
    son propre code retourné par l'API (adresseEtablissement.codeCommuneEtablissement),
    au lieu d'être passée en paramètre unique.

    Sans `communes` (scan national) : le nom de commune est lu directement sur
    l'établissement (adresseEtablissement), et lat/lon restent vides — prune.py
    géocode à la volée au premier calcul de trajet."""
    added = 0
    for etab in etabs:
        adr = etab.get("adresseEtablissement", {})
        code_commune = adr.get("codeCommuneEtablissement")
        commune_info = (communes or {}).get(code_commune) or {}
        nom_commune = str(commune_info.get("nom") or adr.get("libelleCommuneEtablissement") or "")
        row = _etab_to_row(
            etab,
            nom_commune=nom_commune,
            lat_commune=commune_info.get("lat"),
            lon_commune=commune_info.get("lon"),
            naf_code=naf_code,
            naf_libelle=naf_libelle,
            naf_labels=naf_labels,
            nature_force=nature_force,
        )
        if not row:
            continue
        if db.insert_entreprise_ignore(user_id, row):
            added += 1
            logger.debug("Ajoutée : %s (%s) [%s]", row["denomination"], row["siret"], row["nature"])
    return added


def run_sirene_scan(
    user_id: int,
    *,
    point_ref: str,
    rayon_km: float,
    departements: list[str],
    nafs: dict[str, str],
    insee_token: str,
    include_mairies: bool = True,
    include_associations: bool = True,
    national: bool = False,
) -> dict[str, Any]:
    logger.info(
        "Démarrage scan Sirene — national=%s point=%s rayon=%s nafs=%d depts=%s mairies=%s associations=%s",
        national,
        point_ref,
        rayon_km,
        len(nafs),
        departements,
        include_mairies,
        include_associations,
    )

    db.set_config_values(
        {
            "point_ref": "France entière" if national else point_ref,
            "rayon_km": "0" if national else str(rayon_km),
            "departements": "France entière" if national else ", ".join(departements),
            "nafs": nafs,
            "scan_mairies": "1" if include_mairies else "0",
            "scan_associations": "1" if include_associations else "0",
        }
    )

    headers = {"X-INSEE-Api-Key-Integration": insee_token}
    added_count = 0
    added_mairies = 0
    added_associations = 0
    requests_sent = 0

    if national:
        # Pas de filtrage commune du tout : une requête par lot NAF/CJ, paginée
        # par curseur (pas de plafond de résultats côté API). Aucune énumération
        # préalable des ~35000 communes françaises n'est nécessaire.
        communes: dict[str, CommuneInfo] = {}

        if nafs:
            for naf_batch in _chunked(list(nafs.keys()), SIRENE_NAF_CHUNK_SIZE):
                requests_sent += 1
                logger.debug("Scan national NAF=%s", ",".join(naf_batch))
                try:
                    etabs = search_sirene(
                        build_sirene_query_naf(naf_batch),
                        headers,
                        label=f"NAF={','.join(naf_batch)}",
                    )
                except Exception:
                    logger.exception("Échec recherche Sirene NAF batch=%s", naf_batch)
                    continue
                logger.info("NAF %s → %d établissement(s) brut(s)", ",".join(naf_batch), len(etabs))
                added_count += _insert_etabs(user_id, etabs, naf_labels=nafs, nature_force="entreprise")
                time.sleep(0.2)

        if include_mairies:
            cj_codes = list(MAIRIE_CATEGORIES_JURIDIQUES.keys())
            requests_sent += 1
            try:
                etabs = search_sirene(
                    build_sirene_query_categorie_juridique(cj_codes),
                    headers,
                    label="mairies",
                )
            except Exception:
                logger.exception("Échec scan mairies")
                etabs = []
            logger.info("Mairies (national) → %d établissement(s)", len(etabs))
            n = _insert_etabs(user_id, etabs, nature_force="mairie")
            added_count += n
            added_mairies += n

        if include_associations:
            cj_codes = list(ASSOCIATION_CATEGORIES_JURIDIQUES.keys())
            for cj_batch in _chunked(cj_codes, SIRENE_CJ_CHUNK_SIZE):
                requests_sent += 1
                try:
                    etabs = search_sirene(
                        build_sirene_query_categorie_juridique(cj_batch, employeur_seulement=True),
                        headers,
                        label="associations",
                    )
                except Exception:
                    logger.exception("Échec scan associations CJ batch=%s", cj_batch)
                    continue
                logger.info("Associations (national) CJ=%s → %d établissement(s)", ",".join(cj_batch), len(etabs))
                n = _insert_etabs(user_id, etabs, nature_force="association")
                added_count += n
                added_associations += n
                time.sleep(0.2)

        logger.info(
            "Scan Sirene national terminé — +%d (+%d mairies, +%d associations), %d requête(s)",
            added_count,
            added_mairies,
            added_associations,
            requests_sent,
        )
    else:
        communes = get_communes_dans_rayon(point_ref, rayon_km, departements)
        if not communes:
            raise ValueError("Aucune commune trouvée dans ce rayon")

        commune_batches = _chunked(list(communes.keys()), SIRENE_COMMUNE_CHUNK_SIZE)

        # 1) Entreprises par codes NAF — un seul lot NAF x communes par requête au
        # lieu d'une requête par paire (NAF, commune) : le nombre de requêtes passe
        # de len(nafs) x len(communes) à ceil(len(nafs)/20) x ceil(len(communes)/40).
        if nafs:
            for naf_batch in _chunked(list(nafs.keys()), SIRENE_NAF_CHUNK_SIZE):
                for commune_batch in commune_batches:
                    requests_sent += 1
                    logger.debug("Scan NAF=%s / %d commune(s)", ",".join(naf_batch), len(commune_batch))
                    try:
                        etabs = search_batched_by_commune(
                            lambda ccs, nb=naf_batch: build_sirene_query_naf(nb, ccs),
                            commune_batch,
                            headers,
                            label=f"NAF={','.join(naf_batch)}",
                        )
                    except Exception:
                        logger.exception("Échec recherche Sirene NAF batch=%s", naf_batch)
                        continue

                    logger.info(
                        "NAF %s / %d commune(s) → %d établissement(s) brut(s)",
                        ",".join(naf_batch),
                        len(commune_batch),
                        len(etabs),
                    )
                    added_count += _insert_etabs(
                        user_id,
                        etabs,
                        communes=communes,
                        naf_labels=nafs,
                        nature_force="entreprise",
                    )
                    time.sleep(0.2)

        # 2) Mairies (communes — catégorie juridique 7210) : un seul code CJ, donc
        # seules les communes ont besoin d'être découpées en lots.
        if include_mairies:
            cj_codes = list(MAIRIE_CATEGORIES_JURIDIQUES.keys())
            for commune_batch in commune_batches:
                requests_sent += 1
                logger.debug("Scan mairies / %d commune(s)", len(commune_batch))
                try:
                    etabs = search_batched_by_commune(
                        lambda ccs: build_sirene_query_categorie_juridique(cj_codes, ccs),
                        commune_batch,
                        headers,
                        label="mairies",
                    )
                except Exception:
                    logger.exception("Échec scan mairies")
                    continue

                logger.info("Mairies / %d commune(s) → %d établissement(s)", len(commune_batch), len(etabs))
                n = _insert_etabs(user_id, etabs, communes=communes, nature_force="mairie")
                added_count += n
                added_mairies += n
                time.sleep(0.2)

        # 3) Associations employeuses (déclarées / RUP / droit local…) — les 5
        # catégories juridiques sont elles aussi regroupées dans la même requête.
        if include_associations:
            cj_codes = list(ASSOCIATION_CATEGORIES_JURIDIQUES.keys())
            for cj_batch in _chunked(cj_codes, SIRENE_CJ_CHUNK_SIZE):
                for commune_batch in commune_batches:
                    requests_sent += 1
                    logger.debug(
                        "Scan associations CJ=%s / %d commune(s)",
                        ",".join(cj_batch),
                        len(commune_batch),
                    )
                    try:
                        etabs = search_batched_by_commune(
                            lambda ccs, cjb=cj_batch: build_sirene_query_categorie_juridique(
                                cjb, ccs, employeur_seulement=True
                            ),
                            commune_batch,
                            headers,
                            label="associations",
                        )
                    except Exception:
                        logger.exception("Échec scan associations CJ batch=%s", cj_batch)
                        continue

                    logger.info(
                        "Associations / %d commune(s) → %d établissement(s)",
                        len(commune_batch),
                        len(etabs),
                    )
                    n = _insert_etabs(user_id, etabs, communes=communes, nature_force="association")
                    added_count += n
                    added_associations += n
                    time.sleep(0.2)

        logger.info(
            "Scan Sirene terminé — +%d (+%d mairies, +%d associations), %d requête(s), %d communes",
            added_count,
            added_mairies,
            added_associations,
            requests_sent,
            len(communes),
        )

    logger.info("Lancement du nettoyage automatique post-Sirene")
    clean_stats = clean_entreprises(user_id, apply_effectif_filter=True)

    return {
        "added": added_count,
        "added_mairies": added_mairies,
        "added_associations": added_associations,
        "communes": len(communes),
        "requests_sent": requests_sent,
        "clean": clean_stats,
    }
