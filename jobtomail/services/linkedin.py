"""Helpers LinkedIn (URLs prébuild, pas de scraping)."""

from __future__ import annotations

from urllib.parse import quote_plus

from jobtomail.constants import MOTS_CLES_POSTE


def build_linkedin_people_url(denomination: str, commune: str = "") -> str:
    mots = " OR ".join(f'"{m}"' for m in MOTS_CLES_POSTE)
    zone = commune or "Var"
    keywords = f'"{denomination}" {zone} ({mots})'
    return (
        "https://www.linkedin.com/search/results/people/"
        f"?keywords={quote_plus(keywords)}&origin=GLOBAL_SEARCH_HEADER"
    )
