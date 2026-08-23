"""Recherche d'email via Hunter.io."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote_plus

import requests

from jobtomail.constants import HUNTER_EMAIL_FINDER_URL
from jobtomail.services.serpapi import extract_domain

logger = logging.getLogger(__name__)


def find_email(
    *,
    domain: str,
    prenom: str,
    nom: str,
    hunter_key: str,
) -> dict[str, Any]:
    clean_domain = extract_domain(domain)
    logger.info("Hunter.io → %s %s @ %s", prenom, nom, clean_domain)

    url = (
        f"{HUNTER_EMAIL_FINDER_URL}"
        f"?domain={quote_plus(clean_domain)}"
        f"&first_name={quote_plus(prenom)}"
        f"&last_name={quote_plus(nom)}"
        f"&api_key={hunter_key}"
    )
    res = requests.get(url, timeout=20)
    if res.status_code != 200:
        logger.error("Hunter.io HTTP %s : %s", res.status_code, res.text[:200])
        raise RuntimeError(f"Erreur Hunter.io ({res.status_code})")

    email_data = res.json().get("data") or {}
    found = email_data.get("email")
    score = email_data.get("score")
    if found:
        logger.info("Hunter.io trouvé : %s (score=%s)", found, score)
    else:
        logger.warning("Hunter.io : aucun email pour %s %s @ %s", prenom, nom, clean_domain)
    return email_data
