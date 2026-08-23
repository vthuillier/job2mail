"""Évaluation de la qualité d'un email avant envoi."""

from __future__ import annotations

import re
from typing import Any

from jobtomail.constants import (
    EMAIL_DOMAINES_GENERIQUES,
    EMAIL_PREFIXES_ROLE,
    HUNTER_SCORE_MIN_OK,
    HUNTER_SCORE_MIN_WARN,
)
from jobtomail.services.serpapi import extract_domain

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def assess_email(
    email: str,
    *,
    company_domain: str = "",
    hunter_score: int | None = None,
) -> dict[str, Any]:
    """
    Retourne quality ∈ ok|warn|bad, une note FR, et can_send (False si bad).
    """
    email = (email or "").strip().lower()
    notes: list[str] = []
    quality = "ok"

    if not email or not _EMAIL_RE.match(email):
        return {
            "quality": "bad",
            "note": "Adresse email invalide",
            "can_send": False,
            "hunter_score": hunter_score,
            "email": email,
        }

    local, _, domain = email.partition("@")
    company_host = extract_domain(company_domain) if company_domain else ""

    if domain in EMAIL_DOMAINES_GENERIQUES:
        notes.append(f"domaine personnel ({domain})")
        quality = "warn"
    elif company_host and domain != company_host and not domain.endswith("." + company_host):
        notes.append(f"domaine ≠ site ({domain} vs {company_host})")
        if quality == "ok":
            quality = "warn"

    if local.split("+")[0] in EMAIL_PREFIXES_ROLE:
        notes.append(f"adresse générique ({local}@…)")
        if quality == "ok":
            quality = "warn"

    score = hunter_score
    if score is not None:
        try:
            score = int(score)
        except (TypeError, ValueError):
            score = None

    if score is not None:
        if score < HUNTER_SCORE_MIN_WARN:
            notes.append(f"score Hunter faible ({score})")
            quality = "bad"
        elif score < HUNTER_SCORE_MIN_OK:
            notes.append(f"score Hunter moyen ({score})")
            if quality == "ok":
                quality = "warn"
        else:
            notes.append(f"score Hunter {score}")

    note = " · ".join(notes) if notes else "Email OK"
    return {
        "quality": quality,
        "note": note,
        "can_send": quality != "bad",
        "hunter_score": score,
        "email": email,
    }
