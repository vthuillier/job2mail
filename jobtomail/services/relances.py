"""Cadence et éligibilité des relances intelligentes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from jobtomail import db
from jobtomail.constants import (
    MAX_RELANCES,
    RELANCE_1_DELAY_DAYS,
    RELANCE_2_DELAY_DAYS,
)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip().replace("Z", "+00:00")
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(raw[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _days_since(value: str | None) -> float | None:
    dt = _parse_ts(value)
    if not dt:
        return None
    if dt.tzinfo is not None:
        now = datetime.now(timezone.utc)
        dt = dt.astimezone(timezone.utc)
    else:
        now = datetime.now()
    return (now - dt).total_seconds() / 86400.0


def relance_status(ent: dict[str, Any] | Any) -> dict[str, Any]:
    """
    Calcule si une relance est due / bloquée pour une entreprise.
    angle: 1 (1ère relance) ou 2 (dernière, angle différent).
    """
    if hasattr(ent, "keys"):
        e = dict(ent)
    else:
        e = ent

    status = e.get("status") or ""
    count = int(e.get("relance_count") or 0)
    reply = (e.get("reply_class") or "").strip()

    base = {
        "siret": e.get("siret"),
        "denomination": e.get("denomination"),
        "relance_count": count,
        "max_relances": MAX_RELANCES,
        "angle": min(count + 1, MAX_RELANCES),
        "due": False,
        "blocked": False,
        "reason": "",
        "days_since_contact": None,
        "delay_days": RELANCE_1_DELAY_DAYS if count == 0 else RELANCE_2_DELAY_DAYS,
    }

    if (e.get("nature") or "").strip() == "mairie":
        base["blocked"] = True
        base["reason"] = "Relance désactivée pour les mairies"
        return base

    if status not in ("postule", "relance"):
        base["blocked"] = True
        base["reason"] = f"statut {status or '—'} — pas de relance"
        return base

    if reply and reply != "autre":
        base["blocked"] = True
        base["reason"] = f"réponse déjà classée ({reply})"
        return base

    if count >= MAX_RELANCES:
        base["blocked"] = True
        base["reason"] = f"max {MAX_RELANCES} relances atteint — stop"
        return base

    if not (e.get("contact_email") or "").strip():
        base["blocked"] = True
        base["reason"] = "pas d'email"
        return base

    if count == 0:
        ref = e.get("email_sent_at")
        delay = RELANCE_1_DELAY_DAYS
        angle = 1
    else:
        ref = e.get("last_relance_at") or e.get("email_sent_at")
        delay = RELANCE_2_DELAY_DAYS
        angle = 2

    days = _days_since(ref)
    base["days_since_contact"] = round(days, 1) if days is not None else None
    base["delay_days"] = delay
    base["angle"] = angle

    if days is None:
        base["reason"] = "date d'envoi inconnue"
        base["due"] = True  # on autorise quand même
        return base

    if days >= delay:
        base["due"] = True
        base["reason"] = f"relance n°{angle} due (J+{delay})"
    else:
        remaining = max(0, delay - days)
        base["reason"] = f"trop tôt — encore ~{remaining:.0f}j (J+{delay})"
    return base


def list_relances_dues() -> list[dict[str, Any]]:
    dues: list[dict[str, Any]] = []
    for ent in db.list_candidatures_en_attente():
        info = relance_status(ent)
        if info.get("due") and not info.get("blocked"):
            dues.append({**ent, **info})
    dues.sort(key=lambda x: (-(x.get("days_since_contact") or 0), x.get("denomination") or ""))
    return dues
