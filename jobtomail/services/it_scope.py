"""Périmètre IT : détection NAF / thème et statut hors_champs."""

from __future__ import annotations

import logging
import unicodedata
from typing import Any

from jobtomail import db
from jobtomail.constants import FRENCHTECH_TECH_THEMES

logger = logging.getLogger(__name__)


def _strip_accents(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def is_it_naf(naf_code: str | None) -> bool:
    """True si le code NAF fait partie des NAF configurés par l'utilisateur
    (state.nafs — n'importe quel métier, pas seulement l'informatique)."""
    code = (naf_code or "").strip().upper().replace(" ", "")
    if not code:
        return False
    return code in db.load_nafs()


def is_tech_theme(theme: str | None) -> bool:
    """Thème French Tech considéré comme tech (vide = inconnu, pas tech)."""
    if not (theme or "").strip():
        return False
    t = _strip_accents(theme).lower()
    return any(key in t for key in FRENCHTECH_TECH_THEMES)


def is_in_it_scope(row: dict[str, Any]) -> bool:
    """
    True si l'entreprise est dans le périmètre candidature IT.
    Mairies et associations sont conservées hors auto-marquage.
    """
    nature = (row.get("nature") or "entreprise").strip() or "entreprise"
    if nature != "entreprise":
        return True

    naf = (row.get("naf_code") or "").strip()
    if naf:
        return is_it_naf(naf)

    # Thème French Tech stocké parfois dans naf_libelle
    theme = (row.get("naf_libelle") or "").strip()
    if theme and not is_tech_theme(theme):
        return False
    if theme and is_tech_theme(theme):
        return True

    return True


def should_mark_hors_champs(row: dict[str, Any]) -> bool:
    """True si une entreprise « à postuler » devrait passer en hors_champs."""
    status = (row.get("status") or "a_postuler").strip()
    if status != "a_postuler":
        return False
    return not is_in_it_scope(row)


def mark_hors_champs_entreprises(user_id: int, *, sirets: list[str] | None = None) -> dict[str, Any]:
    """
    Passe en hors_champs les entreprises hors périmètre IT (NAF ou thème).
    Ne modifie que le statut « à postuler ».
    """
    rows = [dict(r) for r in db.list_entreprises(user_id)]
    if sirets:
        wanted = set(sirets)
        rows = [r for r in rows if r["siret"] in wanted]

    marked = 0
    skipped = 0
    for row in rows:
        if not should_mark_hors_champs(row):
            skipped += 1
            continue
        db.update_entreprise(user_id, row["siret"], {"status": "hors_champs"})
        marked += 1
        logger.info(
            "Hors champs — %s (NAF=%s, thème=%s)",
            row.get("denomination"),
            row.get("naf_code") or "—",
            row.get("naf_libelle") or "—",
        )

    result = {
        "ok": True,
        "marked": marked,
        "skipped": skipped,
        "scanned": len(rows),
    }
    logger.info("Marquage hors_champs — %d/%d", marked, len(rows))
    return result
