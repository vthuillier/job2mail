"""Recherche de contacts RH / tech via SerpAPI (LinkedIn people)."""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

from jobtomail import db
from jobtomail.constants import MOTS_CLES_POSTE, SERPAPI_URL

logger = logging.getLogger(__name__)

# Priorité décroissante pour le poste détecté
_POSTE_SCORES: list[tuple[re.Pattern[str], int, str]] = [
    (re.compile(r"\bcto\b|chief\s+technology", re.I), 100, "CTO"),
    (re.compile(r"\bmaire\b", re.I), 92, "Maire"),
    (re.compile(r"directeur\s+technique|vp\s+engineering|head\s+of\s+engineering", re.I), 95, "Directeur technique"),
    (re.compile(r"tech\s*lead|lead\s+developer|responsable\s+technique", re.I), 85, "Tech lead"),
    (re.compile(r"talent\s+acquisition|responsable\s+recrutement", re.I), 80, "Talent Acquisition"),
    (re.compile(r"\brh\b|ressources?\s+humaines|people\s+(manager|ops|partner)", re.I), 75, "RH"),
    (re.compile(r"recrut", re.I), 70, "Recruteur"),
    (re.compile(r"co-?fondateur|fondateur|founder", re.I), 65, "Fondateur"),
    (re.compile(r"\bpdg\b|ceo\b|pr[eé]sident", re.I), 55, "Dirigeant"),
    (re.compile(r"directeur|director", re.I), 40, "Directeur"),
]

_TITLE_SPLIT = re.compile(r"\s+[–—\-|•]\s+")
_LINKEDIN_IN = re.compile(r"linkedin\.com/in/", re.I)
_FORMES = re.compile(r"\b(SASU?|SARL|EURL|SA|SCI|SCOP)\b", re.I)
_MAYOR_NAME_RE = re.compile(
    r"\b(?:maire(?:\s+de)?|monsieur\s+le\s+maire|madame\s+la\s+maire)\b[^\wÀ-ÿ]+([A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'\-]+){1,3})",
    re.I,
)


def _company_name(denomination: str) -> str:
    return re.sub(r"\s+", " ", _FORMES.sub("", denomination or "").strip())


def _build_people_query(denomination: str, commune: str = "", nature: str = "") -> str:
    # Query plus courte = meilleurs résultats Google / moins de timeout ressenti
    if (nature or "").strip().lower() == "mairie":
        mairie_hint = commune or _company_name(denomination)
        return f'"{mairie_hint}" ("maire" OR "mairie")'
    roles = " OR ".join(
        f'"{m}"' for m in ("CTO", "directeur technique", "tech lead", "RH", "recrutement", "fondateur")
    )
    name = _company_name(denomination)
    parts = [f'"{name}"', f"({roles})", "site:linkedin.com/in"]
    if commune:
        parts.insert(1, commune)
    return " ".join(parts)


def _score_poste(text: str) -> tuple[int, str]:
    best_score = 0
    best_label = ""
    for pattern, score, label in _POSTE_SCORES:
        if pattern.search(text or ""):
            if score > best_score:
                best_score = score
                best_label = label
    return best_score, best_label


def _looks_like_person_name(name: str) -> bool:
    if not name or len(name) > 60:
        return False
    parts = name.split()
    if len(parts) < 2 or len(parts) > 5:
        return False
    if re.search(r"\b(jobs?|emploi|company|entreprise|page|linkedin|profil)\b", name, re.I):
        return False
    # Au moins une majuscule / lettre
    if not re.search(r"[A-Za-zÀ-ÿ]", name):
        return False
    return True


def _parse_people_title(title: str, denomination: str, snippet: str = "") -> dict[str, str] | None:
    """
    Ex: 'Jean Dupont - CTO at Acme SAS | LinkedIn'
        'Marie Martin – Responsable RH – Acme | LinkedIn'
    """
    raw = (title or "").strip()
    if not raw:
        return None
    raw = re.sub(r"\s*\|\s*LinkedIn\s*$", "", raw, flags=re.I).strip()
    parts = [p.strip() for p in _TITLE_SPLIT.split(raw) if p.strip()]
    if not parts:
        return None

    name = parts[0]
    if not _looks_like_person_name(name):
        return None

    rest = " — ".join(parts[1:]) if len(parts) > 1 else raw
    blob = f"{rest} {snippet or ''}"
    score, poste = _score_poste(blob)
    if score <= 0:
        score, poste = _score_poste(raw)
    if score <= 0:
        # Accepte si le nom de la boîte apparaît
        slug = re.sub(r"[^a-z0-9]", "", _company_name(denomination).lower())[:6]
        hay = re.sub(r"[^a-z0-9]", "", f"{raw} {snippet}".lower())
        if slug and slug not in hay:
            return None
        poste = parts[1] if len(parts) > 1 else ""
        score = 15

    name_parts = name.split()
    prenom = name_parts[0].title()
    nom = " ".join(p.title() for p in name_parts[1:])
    return {
        "contact_prenom": prenom,
        "contact_nom": nom,
        "contact_poste": poste or (parts[1] if len(parts) > 1 else ""),
        "score": str(score),
        "title_raw": title,
    }


def _extract_mayor_from_text(text: str) -> tuple[str, str] | None:
    if not text:
        return None
    m = _MAYOR_NAME_RE.search(text)
    if not m:
        return None
    full_name = re.sub(r"\s+", " ", (m.group(1) or "").strip())
    parts = full_name.split()
    if len(parts) < 2:
        return None
    prenom = parts[0].title()
    nom = " ".join(p.title() for p in parts[1:])
    if not _looks_like_person_name(f"{prenom} {nom}"):
        return None
    return prenom, nom


def _find_mayor_candidate(
    organic: list[dict[str, Any]],
    *,
    denomination: str,
    commune: str,
) -> dict[str, Any] | None:
    for r in organic:
        blob = " ".join(
            [
                (r.get("title") or "").strip(),
                (r.get("snippet") or "").strip(),
            ]
        ).strip()
        parsed = _extract_mayor_from_text(blob)
        if not parsed:
            continue
        prenom, nom = parsed
        link = (r.get("link") or "").strip()
        return {
            "contact_prenom": prenom,
            "contact_nom": nom,
            "contact_poste": f"Maire de {commune or denomination}".strip(),
            "contact_linkedin": link if _LINKEDIN_IN.search(link) else "",
            "snippet": (r.get("snippet") or "").strip()[:200],
            "score": "92",
            "title_raw": r.get("title") or "",
        }
    return None


def _serpapi_people(query: str, serpapi_key: str) -> list[dict[str, Any]]:
    params = {
        "q": query,
        "api_key": serpapi_key,
        "engine": "google",
        "gl": "fr",
        "hl": "fr",
        "num": 10,
        "google_domain": "google.fr",
    }
    logger.info("SerpAPI people → %s", query)
    res = requests.get(SERPAPI_URL, params=params, timeout=25)
    if res.status_code != 200:
        logger.error("SerpAPI people HTTP %s : %s", res.status_code, res.text[:200])
        raise RuntimeError(f"Erreur SerpAPI ({res.status_code})")
    return list(res.json().get("organic_results") or [])


def search_people_contacts(
    *,
    denomination: str,
    commune: str = "",
    nature: str = "",
    serpapi_key: str,
) -> list[dict[str, Any]]:
    """Retourne des candidats triés par score de poste (desc)."""
    nature_norm = (nature or "").strip().lower()
    queries = [_build_people_query(denomination, commune, nature_norm)]
    # Fallback sans commune si trop restrictif
    if commune:
        queries.append(_build_people_query(denomination, "", nature_norm))

    candidates: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for qi, query in enumerate(queries):
        try:
            organic = _serpapi_people(query, serpapi_key)
        except Exception:
            if qi == 0 and len(queries) > 1:
                continue
            raise

        if nature_norm == "mairie":
            mayor = _find_mayor_candidate(
                organic,
                denomination=denomination,
                commune=commune,
            )
            if mayor:
                candidates.append(mayor)

        for r in organic:
            link = (r.get("link") or "").split("?")[0].strip()
            if not _LINKEDIN_IN.search(link):
                continue
            key = link.lower().rstrip("/")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            snippet = (r.get("snippet") or "").strip()
            parsed = _parse_people_title(r.get("title") or "", denomination, snippet)
            if not parsed:
                continue
            candidates.append(
                {
                    **parsed,
                    "contact_linkedin": link,
                    "snippet": snippet[:200],
                }
            )

        # Assez de bons candidats → pas besoin du 2e appel
        if any(int(c.get("score") or 0) >= 70 for c in candidates):
            break
        if candidates and qi == 0:
            # On a quelque chose, on tente quand même le fallback seulement si faible
            if max(int(c.get("score") or 0) for c in candidates) >= 55:
                break

    candidates.sort(key=lambda c: int(c.get("score") or 0), reverse=True)
    logger.info(
        "SerpAPI people %s — %d candidat(s) : %s",
        denomination,
        len(candidates),
        ", ".join(
            f"{c.get('contact_prenom')} {c.get('contact_nom')}({c.get('score')})"
            for c in candidates[:3]
        )
        or "—",
    )
    return candidates


def pick_best_people_contact(
    candidates: list[dict[str, Any]],
    *,
    denomination: str = "",
    use_ollama: bool = False,
) -> dict[str, Any] | None:
    """Choisit le meilleur contact par score de poste (pas d'Ollama — trop lent)."""
    if not candidates:
        return None
    # use_ollama ignoré volontairement : le ranking par poste suffit et reste instantané
    if use_ollama:
        logger.debug("Ollama contact désactivé pour %s — score heuristique", denomination)
    return candidates[0]


def enrich_entreprise_contact(
    *,
    siret: str,
    serpapi_key: str,
    force: bool = False,
    use_ollama: bool = False,
) -> dict[str, Any]:
    """Cherche un contact RH/tech et le pose si meilleur que le dirigeant légal."""
    ent = db.get_entreprise(siret)
    if not ent:
        return {"siret": siret, "ok": False, "error": "introuvable"}

    source = (ent["contact_source"] if "contact_source" in ent.keys() else None) or ""
    if not force and source == "manual":
        return {
            "siret": siret,
            "ok": True,
            "skipped": True,
            "reason": "contact manuel — utilise force=true pour écraser",
        }
    if not force and source == "linkedin_search" and (ent["contact_prenom"] or ent["contact_nom"]):
        return {
            "siret": siret,
            "ok": True,
            "skipped": True,
            "reason": "déjà enrichi (linkedin_search)",
            "contact_prenom": ent["contact_prenom"],
            "contact_nom": ent["contact_nom"],
            "contact_poste": ent["contact_poste"],
        }

    try:
        candidates = search_people_contacts(
            denomination=ent["denomination"] or "",
            commune=ent["commune"] or "",
            nature=ent["nature"] or "",
            serpapi_key=serpapi_key,
        )
    except Exception as e:
        logger.exception("Recherche contact échouée pour %s", siret)
        return {"siret": siret, "ok": False, "error": str(e)}

    best = pick_best_people_contact(
        candidates,
        denomination=ent["denomination"] or "",
        use_ollama=False,
    )
    if not best:
        return {
            "siret": siret,
            "ok": True,
            "found": False,
            "candidates": 0,
            "message": "Aucun profil LinkedIn RH/tech trouvé",
        }

    # Ne remplace un dirigeant que si le nouveau score est intéressant (≥55)
    existing_score, _ = _score_poste(ent["contact_poste"] or "")
    new_score = int(best.get("score") or 0)
    if (
        not force
        and (ent["contact_prenom"] or ent["contact_nom"])
        and source in ("dirigeant", "")
        and new_score < 55
        and existing_score >= 50
    ):
        return {
            "siret": siret,
            "ok": True,
            "skipped": True,
            "reason": "dirigeant conservé (pas de meilleur contact RH/tech)",
            "candidates_count": len(candidates),
            "candidates": candidates[:5],
        }

    fields = {
        "contact_prenom": best["contact_prenom"],
        "contact_nom": best["contact_nom"],
        "contact_poste": best["contact_poste"],
        "contact_linkedin": best.get("contact_linkedin") or "",
        "contact_source": "linkedin_search",
    }
    db.apply_contact(siret, fields)
    logger.info(
        "Contact RH/tech %s %s (%s) → %s",
        fields["contact_prenom"],
        fields["contact_nom"],
        fields["contact_poste"],
        ent["denomination"],
    )
    return {
        "siret": siret,
        "ok": True,
        "found": True,
        **fields,
        "score": new_score,
        "candidates_count": len(candidates),
        "candidates": candidates[:5],
    }


def run_contacts_scan(
    *,
    serpapi_key: str,
    sirets: list[str] | None = None,
    force: bool = False,
    limit: int | None = None,
    use_ollama: bool = False,
) -> dict[str, Any]:
    if sirets:
        rows = [db.get_entreprise(s) for s in sirets]
        rows = [r for r in rows if r]
    else:
        rows = db.list_entreprises()
        rows = [
            r
            for r in rows
            if force
            or ((r["contact_source"] or "") not in ("linkedin_search", "manual"))
        ]
    # Garde-fou SerpAPI : jamais un scan illimité
    max_limit = 50 if limit is None else max(0, min(int(limit), 50))
    rows = rows[:max_limit]

    logger.info(
        "Scan contacts — %d entreprise(s) (force=%s)",
        len(rows),
        force,
    )

    filled = 0
    skipped = 0
    not_found = 0
    errors = 0
    details: list[dict[str, Any]] = []

    for row in rows:
        result = enrich_entreprise_contact(
            siret=row["siret"],
            serpapi_key=serpapi_key,
            force=force,
            use_ollama=False,
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
        time.sleep(0.25)

    logger.info(
        "Scan contacts terminé — filled=%d not_found=%d skipped=%d errors=%d",
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
