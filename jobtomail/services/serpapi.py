"""Enrichissement site web / LinkedIn via SerpAPI (1 req Google / entreprise)."""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urlparse

import requests

from jobtomail.constants import DOMAINES_BLOQUES, DOMAINES_EXCLUS_RECHERCHE, SERPAPI_URL
from jobtomail import db
from jobtomail.services.ollama import ollama_available, pick_company_links

logger = logging.getLogger(__name__)

_FORMES_JURIDIQUES = re.compile(
    r"\b(SASU?|SARL|EURL|SA|SCI|SCOP|SCA|SCS|GIE|EARL|SNC|SELARL|SELAS)\b",
    re.IGNORECASE,
)


def extract_domain(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    host = urlparse(raw).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_site_url(url: str) -> str:
    """Retourne https://domaine.tld (racine du site)."""
    domain = extract_domain(url)
    if not domain:
        return ""
    return f"https://{domain}"


def _strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def company_slug(denomination: str) -> str:
    """Ex: 'EGERIE SAS' -> 'egerie'."""
    s = _strip_accents(denomination.upper())
    s = _FORMES_JURIDIQUES.sub(" ", s)
    s = re.sub(r"[^A-Z0-9]+", " ", s).strip()
    tokens = [t for t in s.split() if len(t) >= 3]
    if not tokens:
        return re.sub(r"[^a-z0-9]", "", s.lower())
    # Le nom principal est souvent le 1er token significatif
    return tokens[0].lower()


def is_blocked_domain(url: str) -> bool:
    host = extract_domain(url)
    if not host:
        return True
    return any(blocked in host for blocked in DOMAINES_BLOQUES)


def is_probable_company_site(link: str) -> bool:
    return not is_blocked_domain(link)


def _build_google_query(denom: str, commune: str) -> str:
    """
    Recherche type Google manuel : Nom + ville, en excluant les annuaires
    qui polluent les résultats API.
    """
    name = _FORMES_JURIDIQUES.sub("", denom).strip()
    name = re.sub(r"\s+", " ", name)
    parts = [name]
    if commune:
        parts.append(commune)
    exclusions = " ".join(f"-site:{d}" for d in DOMAINES_EXCLUS_RECHERCHE)
    return f"{' '.join(parts)} {exclusions}".strip()


def _parse_organic(payload: dict, denom: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for i, r in enumerate(payload.get("organic_results") or [], 1):
        link = (r.get("link") or "").strip()
        if not link:
            continue
        rows.append(
            {
                "position": str(i),
                "title": (r.get("title") or "").strip(),
                "link": link,
                "displayed_link": (r.get("displayed_link") or "").strip(),
                "snippet": (r.get("snippet") or "").strip(),
            }
        )

    kg = payload.get("knowledge_graph") or {}
    kg_site = (kg.get("website") or "").strip()
    if kg_site:
        rows.insert(
            0,
            {
                "position": "0",
                "title": f"Knowledge Graph — {denom}",
                "link": kg_site,
                "displayed_link": extract_domain(kg_site),
                "snippet": "site officiel",
            },
        )

    for profile in kg.get("profiles") or []:
        link = (profile.get("link") or "").strip()
        if link:
            rows.append(
                {
                    "position": "kg",
                    "title": profile.get("name") or "LinkedIn",
                    "link": link,
                    "displayed_link": extract_domain(link),
                    "snippet": "knowledge graph profile",
                }
            )
    return rows


def _score_site_candidate(url: str, *, slug: str, position: int) -> float:
    """Plus le score est haut, plus c'est probablement le vrai site."""
    if is_blocked_domain(url):
        return -1.0

    domain = extract_domain(url)
    path = urlparse(url if "://" in url else f"https://{url}").path or "/"
    score = 100.0 - position * 3.0  # le 1er résultat non bloqué gagne souvent

    if slug and slug in domain.replace("-", "").replace(".", ""):
        score += 40.0
    if slug and domain.startswith(f"{slug}."):
        score += 30.0

    # Racine ou page d'accueil = bon signe
    if path in ("", "/"):
        score += 10.0
    # Annuaires = chemins longs avec id numérique
    if re.search(r"/\d{6,}", path):
        score -= 25.0
    if path.count("/") > 2:
        score -= 10.0

    # TLD classiques entreprise
    if domain.endswith((".com", ".fr", ".io", ".net", ".tech", ".cloud", ".ai")):
        score += 5.0

    return score


def _pick_official_site(results: list[dict[str, str]], denom: str) -> str | None:
    """
    Choisit le domaine officiel : en pratique le 1er résultat Google
    hors annuaires, avec bonus si le domaine matche le nom (egerie.com).
    """
    slug = company_slug(denom)
    best_url = None
    best_score = -1.0

    for i, r in enumerate(results):
        link = (r.get("link") or "").strip()
        if not link or "linkedin.com/company" in link.lower():
            continue
        if not is_probable_company_site(link):
            logger.debug("Site ignoré (annuaire) pos=%s : %s", i + 1, link)
            continue

        pos = int(r.get("position") or i + 1) if str(r.get("position", "")).isdigit() else i + 1
        score = _score_site_candidate(link, slug=slug, position=pos)
        if score > best_score:
            best_score = score
            best_url = link

    if best_url:
        normalized = normalize_site_url(best_url)
        logger.info(
            "Site retenu pour %s → %s (slug=%s, score=%.1f)",
            denom,
            normalized,
            slug,
            best_score,
        )
        return normalized

    logger.warning("Aucun site officiel trouvé pour %s (slug=%s)", denom, slug)
    return None


def _pick_linkedin(results: list[dict[str, str]]) -> str | None:
    for r in results:
        link = (r.get("link") or "").strip().split("?")[0]
        if "linkedin.com/company" in link.lower():
            return link
    return None


def _heuristic_pick(results: list[dict[str, str]], denom: str) -> dict[str, str | None]:
    return {
        "site_web": _pick_official_site(results, denom),
        "linkedin_company": _pick_linkedin(results),
    }


def enrich_one(
    denom: str,
    commune: str,
    serpapi_key: str,
    use_ollama: bool = True,
) -> tuple[str | None, str | None]:
    query = _build_google_query(denom, commune)
    params = {
        "q": query,
        "api_key": serpapi_key,
        "engine": "google",
        "gl": "fr",
        "hl": "fr",
        "num": 10,
        "google_domain": "google.fr",
    }
    logger.info("SerpAPI query → %s", query)
    res = requests.get(SERPAPI_URL, params=params, timeout=25)
    if res.status_code != 200:
        logger.error("SerpAPI HTTP %s pour %s : %s", res.status_code, denom, res.text[:200])
        raise RuntimeError(f"HTTP {res.status_code}")

    payload = res.json()
    organic = _parse_organic(payload, denom)
    logger.info(
        "SerpAPI %s — %d résultat(s) : %s",
        denom,
        len(organic),
        ", ".join(extract_domain(r.get("link", "")) for r in organic[:5]),
    )

    # Heuristique d'abord : c'est ce qui colle le mieux à "1er résultat = domaine"
    picked = _heuristic_pick(organic, denom)

    # Ollama seulement pour compléter ce qui manque (surtout LinkedIn)
    if use_ollama and ollama_available() and (
        not picked.get("site_web") or not picked.get("linkedin_company")
    ):
        try:
            ai = pick_company_links(
                denomination=denom,
                commune=commune or "",
                results=organic,
            )
            if not picked.get("site_web") and ai.get("site_web"):
                picked["site_web"] = normalize_site_url(ai["site_web"])
            if not picked.get("linkedin_company") and ai.get("linkedin_company"):
                picked["linkedin_company"] = ai["linkedin_company"]
        except Exception:
            logger.exception("Ollama complément échoué pour %s — heuristique conservée", denom)

    logger.info(
        "Enrichissement %s — site=%s linkedin=%s",
        denom,
        picked.get("site_web") or "—",
        picked.get("linkedin_company") or "—",
    )
    return picked.get("site_web"), picked.get("linkedin_company")


def run_serpapi_scan(
    *,
    serpapi_key: str,
    sirets: list[str] | None = None,
    use_ollama: bool = True,
) -> dict[str, Any]:
    to_scan = db.entreprises_to_serpapi(sirets)
    logger.info(
        "Scan SerpAPI — %d entreprise(s), ollama=%s",
        len(to_scan),
        use_ollama and ollama_available(),
    )

    scanned_count = 0
    errors: list[str] = []

    for row in to_scan:
        siret = row["siret"]
        denom = row["denomination"]
        commune = row["commune"] or ""
        try:
            site_found, linkedin_found = enrich_one(
                denom, commune, serpapi_key, use_ollama=use_ollama
            )
            db.mark_serpapi_result(siret, site_found, linkedin_found, scanned=True)
            scanned_count += 1
            time.sleep(0.25)
        except Exception as e:
            logger.exception("Échec SerpAPI pour %s", denom)
            errors.append(f"{denom}: {e}")
            db.mark_serpapi_scanned_only(siret)

    logger.info("Scan SerpAPI terminé — %d OK, %d erreur(s)", scanned_count, len(errors))
    return {"scanned": scanned_count, "errors": errors[:10]}
