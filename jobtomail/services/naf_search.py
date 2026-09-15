"""Recherche par mot-clé dans la nomenclature NAF Rev.2 complète (732 codes).

Permet à un utilisateur non-développeur (chimiste, artisan, association…) de
trouver ses codes NAF sans connaître la nomenclature INSEE par cœur — il tape
un métier/secteur, on lui rend les codes correspondants avec leur libellé
officiel.

Source des données : data.gouv.fr, dataset "Codes NAF INSEE"
(https://www.data.gouv.fr/fr/datasets/codes-naf-insee/), NAF Rev.2, 732 codes.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "naf_rev2.json"

# Longueur de radical pour le matching approximatif (ex. "chimie" doit
# retrouver "chimiques" — même sujet, formes différentes). 5 caractères est
# un compromis classique pour du français (racines courtes type "art"/"vin"
# restent gérées par le match exact/préfixe au-dessus).
_STEM_LEN = 5


@lru_cache(maxsize=1)
def _load_naf() -> dict[str, str]:
    with DATA_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _normalize(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return stripped.lower()


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value)


def search_naf(query: str, *, limit: int = 30) -> list[dict[str, str]]:
    """Cherche par code ou libellé (insensible accents/casse, tolérant aux
    variations de mots via un stemming approximatif : "chimie" retrouve
    "produits chimiques"). Résultats triés par pertinence décroissante."""
    q = _normalize(query.strip())
    if not q:
        return []
    q_stem = q[:_STEM_LEN]

    naf = _load_naf()
    exact: list[dict[str, str]] = []
    starts: list[dict[str, str]] = []
    stems: list[dict[str, str]] = []
    contains: list[dict[str, str]] = []

    for code, libelle in naf.items():
        norm_code = _normalize(code)
        norm_lib = _normalize(libelle)
        entry = {"code": code, "libelle": libelle}

        if q == norm_code or q == norm_lib:
            exact.append(entry)
        elif norm_code.startswith(q) or norm_lib.startswith(q):
            starts.append(entry)
        elif len(q) >= 4 and any(tok[:_STEM_LEN] == q_stem for tok in _tokens(norm_lib) if len(tok) >= 3):
            stems.append(entry)
        elif q in norm_lib or q in norm_code:
            contains.append(entry)

    return (exact + starts + stems + contains)[:limit]
