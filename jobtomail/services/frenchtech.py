"""Import de l'annuaire LA French Tech Toulon (scraping Drupal)."""

from __future__ import annotations

import base64
import codecs
import logging
import re
import time
import unicodedata
import uuid
from typing import Any
from urllib.parse import unquote, urljoin

import requests
from bs4 import BeautifulSoup

from jobtomail import db
from jobtomail.constants import (
    FRENCHTECH_ANNUAIRE_URL,
    FRENCHTECH_BASE_URL,
    FRENCHTECH_TECH_THEMES,
    RECHERCHE_ENTREPRISES_URL,
    TRANCHE_EFFECTIFS,
)
from jobtomail.services.cleaner import score_pertinence
from jobtomail.services.geo import geocode_adresse
from jobtomail.services.it_scope import is_in_it_scope

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "fr-FR,fr;q=0.9",
    }
)

_FORMES = re.compile(
    r"\b(SASU|SAS|SARL|SA|EURL|SCI|SNC|SCOP|SELARL|EARL|EI|EIRL|GIE)\b",
    re.I,
)
_CP_RE = re.compile(r"\b(\d{5})\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _strip_accents(value: str) -> str:
    nfkd = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _norm_name(value: str) -> str:
    value = _strip_accents(value or "").lower()
    value = _FORMES.sub(" ", value)
    value = _NON_ALNUM.sub(" ", value)
    return re.sub(r"\s+", " ", value).strip()


def _parse_adresse(adresse: str | None) -> tuple[str | None, str | None, str | None]:
    """Retourne (adresse, commune, code_postal)."""
    raw = (adresse or "").strip()
    if not raw:
        return None, None, None
    m = _CP_RE.search(raw)
    if not m:
        return raw, None, None
    cp = m.group(1)
    commune = raw[m.end() :].strip(" ,")
    return raw, commune or None, cp


def _is_tech_theme(theme: str | None) -> bool:
    """True si thème tech, ou non renseigné (beaucoup de fiches FT n'ont pas de thème)."""
    if not (theme or "").strip():
        return True
    t = _strip_accents(theme).lower()
    return any(key in t for key in FRENCHTECH_TECH_THEMES)


def _decode_obfuscated_email(token: str) -> str | None:
    """Décode /get-mail-to/<base64> (base64 + ROT13, chemins at/dot)."""
    try:
        raw = base64.b64decode(unquote(token)).decode("utf-8", errors="ignore")
    except Exception:
        return None
    decoded = codecs.decode(raw, "rot_13")
    decoded = decoded.replace("/at/", "@").replace("/dot/", ".")
    decoded = decoded.replace(" at ", "@").replace(" dot ", ".")
    if "@" in decoded and "." in decoded.split("@", 1)[-1]:
        return decoded.strip().lower()
    return None


def _resolve_email(href: str | None) -> str | None:
    if not href:
        return None
    if href.startswith("mailto:"):
        return href.split(":", 1)[1].split("?", 1)[0].strip().lower() or None
    if "/get-mail-to/" not in href:
        return None
    token = href.rsplit("/get-mail-to/", 1)[-1].strip("/")
    email = _decode_obfuscated_email(token)
    if email:
        return email
    url = urljoin(FRENCHTECH_BASE_URL, href)
    try:
        response = _SESSION.get(url, timeout=15, allow_redirects=False)
    except requests.RequestException:
        return None
    loc = response.headers.get("Location") or ""
    if loc.startswith("mailto:"):
        return loc.split(":", 1)[1].split("?", 1)[0].strip().lower() or None
    return None


def _name_variants(denomination: str) -> list[str]:
    raw = (denomination or "").strip()
    if not raw:
        return []
    variants = [raw]
    # "Advease - Startup Prextiwit" → aussi Prextiwit / Advease
    for part in re.split(r"\s[-–—/|]\s", raw):
        part = part.strip()
        part = re.sub(r"(?i)^startup\s+", "", part).strip()
        if part and part not in variants:
            variants.append(part)
    return variants[:4]


def _name_similarity(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _search_entreprises(query: str) -> list[dict]:
    try:
        response = _SESSION.get(
            RECHERCHE_ENTREPRISES_URL,
            params={"q": query, "per_page": 10},
            timeout=20,
        )
    except requests.RequestException as e:
        logger.warning("Recherche Entreprises réseau : %s", e)
        return []
    if response.status_code == 429:
        time.sleep(2)
        try:
            response = _SESSION.get(
                RECHERCHE_ENTREPRISES_URL,
                params={"q": query, "per_page": 10},
                timeout=20,
            )
        except requests.RequestException:
            return []
    if response.status_code != 200:
        logger.warning("Recherche Entreprises HTTP %s pour %r", response.status_code, query)
        return []
    return list(response.json().get("results") or [])


def resolve_via_recherche(
    denomination: str,
    *,
    commune: str | None = None,
    code_postal: str | None = None,
) -> dict[str, Any] | None:
    """Trouve le meilleur établissement via l'API Recherche d'Entreprises."""
    best: tuple[float, dict[str, Any]] | None = None

    queries: list[str] = []
    for variant in _name_variants(denomination):
        queries.append(variant)
        if commune:
            queries.append(f"{variant} {commune}")

    seen_q: set[str] = set()
    for q in queries:
        key = q.lower()
        if key in seen_q:
            continue
        seen_q.add(key)
        for res in _search_entreprises(q):
            siege = res.get("siege") or {}
            siret = (siege.get("siret") or "").strip()
            if len(siret) != 14:
                continue
            nom = res.get("nom_complet") or res.get("nom_raison_sociale") or ""
            score = _name_similarity(denomination, nom) * 100
            for variant in _name_variants(denomination):
                score = max(score, _name_similarity(variant, nom) * 100)

            cp = (siege.get("code_postal") or "").strip()
            lib_com = (siege.get("libelle_commune") or "").strip()
            if code_postal and cp == code_postal:
                score += 35
            elif commune and _norm_name(commune) and _norm_name(commune) == _norm_name(lib_com):
                score += 25
            elif commune and _norm_name(commune) in _norm_name(lib_com):
                score += 15

            if score < 70:
                continue
            candidate = {
                "siret": siret,
                "siren": res.get("siren") or siret[:9],
                "denomination_api": nom,
                "adresse": siege.get("geo_adresse") or siege.get("adresse"),
                "commune": lib_com.title() if lib_com else commune,
                "code_postal": cp or code_postal,
                "naf_code": siege.get("activite_principale") or "",
                "effectif_code": res.get("tranche_effectif_salarie") or "NN",
                "categorie_entreprise": res.get("categorie_entreprise") or "",
                "date_creation": res.get("date_creation") or siege.get("date_creation"),
                "est_siege": True,
                "latitude": (siege.get("latitude") if isinstance(siege.get("latitude"), (int, float)) else None),
                "longitude": (siege.get("longitude") if isinstance(siege.get("longitude"), (int, float)) else None),
                "match_score": score,
            }
            if best is None or score > best[0]:
                best = (score, candidate)
        time.sleep(0.15)

    return best[1] if best else None


def _parse_card(article) -> dict[str, Any] | None:
    link = article.select_one("a.contact-item__title-link")
    if not link:
        return None
    name = link.get_text(" ", strip=True)
    if not name:
        return None
    theme_el = article.select_one(".contact-item__theme")
    theme = theme_el.get_text(" ", strip=True) if theme_el else ""

    adresse = None
    for item in article.select(".info-item"):
        text = item.get_text(" ", strip=True)
        if "Adresse" in text or item.select_one(".fa-map-marker-alt"):
            adresse = re.sub(r"^Adresse\s*:\s*", "", text).strip()
            break

    site_web = None
    for a in article.select("a[href]"):
        href = a.get("href") or ""
        label = a.get_text(" ", strip=True).lower()
        if href.startswith("http") and ("site" in label or "globe" in " ".join(a.get("class") or [])):
            site_web = href
            break
        if href.startswith("http") and a.select_one(".fa-globe"):
            site_web = href
            break
    if not site_web:
        for a in article.select("a[href]"):
            href = a.get("href") or ""
            if href.startswith("http") and "frenchtechtoulon" not in href and "linkedin.com/share" not in href:
                if a.get_text(" ", strip=True).lower().startswith("site"):
                    site_web = href
                    break

    email_href = None
    for a in article.select("a[href]"):
        href = a.get("href") or ""
        if href.startswith("mailto:") or "/get-mail-to/" in href:
            email_href = href
            break

    path = link.get("href") or ""
    return {
        "denomination": name,
        "theme": theme,
        "adresse": adresse,
        "site_web": site_web,
        "email_href": email_href,
        "detail_url": urljoin(FRENCHTECH_BASE_URL, path) if path else None,
    }


def scrape_annuaire(*, max_pages: int = 40, pause_s: float = 0.25) -> list[dict[str, Any]]:
    """Parcourt toutes les pages de l'annuaire French Tech Toulon."""
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for page in range(max_pages):
        url = FRENCHTECH_ANNUAIRE_URL if page == 0 else f"{FRENCHTECH_ANNUAIRE_URL}?page={page}"
        try:
            response = _SESSION.get(url, timeout=30)
        except requests.RequestException as e:
            logger.error("Échec fetch annuaire page=%s : %s", page, e)
            break
        if response.status_code != 200:
            logger.error("Annuaire HTTP %s page=%s", response.status_code, page)
            break

        soup = BeautifulSoup(response.text, "html.parser")
        articles = soup.select("article.contact-item")
        if not articles:
            if page == 0:
                logger.error("Aucune carte trouvée sur l'annuaire (sélecteur cassé ?)")
            break

        page_added = 0
        for art in articles:
            card = _parse_card(art)
            if not card:
                continue
            key = (card.get("detail_url") or card["denomination"]).lower()
            if key in seen_urls:
                continue
            seen_urls.add(key)
            results.append(card)
            page_added += 1

        logger.info("French Tech page %s — %d fiche(s) (total %d)", page, page_added, len(results))
        if page_added == 0:
            break
        time.sleep(pause_s)

    return results


def _make_manual_siret() -> str:
    return "M" + uuid.uuid4().hex[:13].upper()


def _find_existing_by_name(user_id: int, denomination: str, commune: str | None = None) -> Any | None:
    target = _norm_name(denomination)
    if not target:
        return None
    commune_n = _norm_name(commune or "")
    best = None
    best_score = 0.0
    for row in db.list_entreprises(user_id):
        score = _name_similarity(denomination, row["denomination"] or "")
        if commune_n and row["commune"]:
            if _norm_name(row["commune"]) == commune_n:
                score += 0.15
        if score > best_score:
            best_score = score
            best = row
    if best and best_score >= 0.85:
        return best
    return None


def _append_note(existing: str | None, note: str) -> str:
    existing = (existing or "").strip()
    if note in existing:
        return existing
    return f"{existing}\n{note}".strip() if existing else note


def _row_from_scraped(
    card: dict[str, Any],
    *,
    resolved: dict[str, Any] | None,
    email: str | None,
    user_id: int,
) -> dict[str, Any]:
    adresse_raw = card.get("adresse")
    adresse, commune, cp = _parse_adresse(adresse_raw)
    theme = (card.get("theme") or "").strip()

    if resolved:
        siret = resolved["siret"]
        siren = resolved.get("siren") or siret[:9]
        denomination = card["denomination"]
        adresse = resolved.get("adresse") or adresse
        commune = resolved.get("commune") or commune
        effectif_code = resolved.get("effectif_code") or "NN"
        naf_code = resolved.get("naf_code") or None
        categorie = resolved.get("categorie_entreprise") or None
        date_creation = resolved.get("date_creation")
        est_siege = bool(resolved.get("est_siege", True))
        lat = resolved.get("latitude")
        lon = resolved.get("longitude")
    else:
        siret = _make_manual_siret()
        siren = None
        denomination = card["denomination"]
        effectif_code = "NN"
        naf_code = None
        categorie = None
        date_creation = None
        est_siege = False
        lat = lon = None

    if effectif_code not in TRANCHE_EFFECTIFS:
        effectif_code = "NN"

    note = f"Source: French Tech Toulon"
    if theme:
        note += f" · {theme}"
    if card.get("detail_url"):
        note += f" · {card['detail_url']}"

    row = {
        "siret": siret,
        "siren": siren,
        "denomination": denomination,
        "adresse": adresse,
        "commune": commune,
        "effectif_code": effectif_code,
        "effectif_libelle": TRANCHE_EFFECTIFS.get(effectif_code, "Non renseigné"),
        "naf_code": naf_code,
        "naf_libelle": theme or None,
        "date_creation": date_creation,
        "est_siege": est_siege,
        "categorie_entreprise": categorie if categorie in ("PME", "ETI", "GE") else None,
        "categorie_juridique": None,
        "nature": "entreprise",
        "latitude": lat,
        "longitude": lon,
        "site_web": card.get("site_web"),
        "linkedin_company": None,
        "notes": note,
        "status": "hors_champs" if not is_in_it_scope({"nature": "entreprise", "naf_code": naf_code, "naf_libelle": theme}, user_id) else "a_postuler",
        "contact_email": email,
    }
    row["score_pertinence"] = score_pertinence(row)
    # Léger bonus pour les membres de l'écosystème FT
    row["score_pertinence"] = round(min(100.0, (row["score_pertinence"] or 0) + 8.0), 1)
    return row


def _enrich_existing(user_id: int, siret: str, card: dict[str, Any], email: str | None) -> None:
    existing = db.get_entreprise(user_id, siret)
    if not existing:
        return
    fields: dict[str, Any] = {}
    if card.get("site_web") and not existing["site_web"]:
        fields["site_web"] = card["site_web"]
    if email and not existing["contact_email"]:
        fields["contact_email"] = email
    theme = (card.get("theme") or "").strip()
    note = "Source: French Tech Toulon"
    if theme:
        note += f" · {theme}"
    if card.get("detail_url"):
        note += f" · {card['detail_url']}"
    fields["notes"] = _append_note(existing["notes"], note)
    if fields:
        db.update_entreprise(user_id, siret, fields)


def run_frenchtech_scan(
    user_id: int,
    *,
    tech_only: bool = False,
    resolve_siret: bool = True,
    resolve_emails: bool = True,
    geocode: bool = True,
) -> dict[str, Any]:
    """Scrape l'annuaire et importe les entreprises en base."""
    logger.info(
        "Scan French Tech Toulon — tech_only=%s resolve_siret=%s resolve_emails=%s",
        tech_only,
        resolve_siret,
        resolve_emails,
    )
    cards = scrape_annuaire()
    if tech_only:
        cards = [c for c in cards if _is_tech_theme(c.get("theme"))]

    added = 0
    matched = 0
    skipped = 0
    unresolved = 0
    errors: list[str] = []

    for i, card in enumerate(cards, 1):
        denom = card["denomination"]
        adresse, commune, cp = _parse_adresse(card.get("adresse"))
        logger.info("French Tech [%d/%d] %s", i, len(cards), denom)

        email = _resolve_email(card.get("email_href")) if resolve_emails else None

        resolved = None
        if resolve_siret:
            try:
                resolved = resolve_via_recherche(denom, commune=commune, code_postal=cp)
            except Exception as e:
                logger.exception("Résolution SIRET échouée pour %s", denom)
                errors.append(f"{denom}: {e}")

        if resolved:
            existing = db.get_entreprise(user_id, resolved["siret"])
            if existing:
                _enrich_existing(user_id, existing["siret"], card, email)
                matched += 1
                continue
        else:
            unresolved += 1
            existing = _find_existing_by_name(user_id, denom, commune)
            if existing:
                _enrich_existing(user_id, existing["siret"], card, email)
                matched += 1
                continue

        row = _row_from_scraped(card, resolved=resolved, email=email, user_id=user_id)
        if geocode and (row.get("latitude") is None) and (row.get("adresse") or row.get("commune")):
            coords = geocode_adresse(row.get("adresse"), row.get("commune"))
            if coords:
                row["latitude"], row["longitude"] = coords

        try:
            # insert_entreprise gère site_web / notes ; ignore si collision rare
            if db.get_entreprise(user_id, row["siret"]):
                _enrich_existing(user_id, row["siret"], card, email)
                matched += 1
                continue
            db.insert_entreprise(user_id, row)
            if email:
                db.update_entreprise(user_id, row["siret"], {"contact_email": email})
            added += 1
        except Exception as e:
            logger.exception("Insert échoué pour %s", denom)
            errors.append(f"{denom}: {e}")
            skipped += 1

        time.sleep(0.05)

    result = {
        "scraped": len(cards),
        "added": added,
        "matched_existing": matched,
        "unresolved_siret": unresolved,
        "skipped": skipped,
        "errors": errors[:20],
    }
    logger.info(
        "French Tech terminé — scraped=%d added=%d matched=%d unresolved=%d",
        result["scraped"],
        added,
        matched,
        unresolved,
    )
    return result
