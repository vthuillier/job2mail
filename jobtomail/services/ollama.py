"""Client Ollama (qwen3) pour choisir site / LinkedIn parmi des résultats Google."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests
from requests.exceptions import ConnectTimeout, ConnectionError, ReadTimeout, Timeout

from jobtomail.constants import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

# Circuit breaker : après N timeouts d'affilée, on saute Ollama pour le reste du process
_consecutive_failures = 0
_skip_until = 0.0  # epoch
_MAX_FAILURES = 2
_COOLDOWN_SEC = 120


def _base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or OLLAMA_BASE_URL).rstrip("/")


def _model() -> str:
    return os.getenv("OLLAMA_MODEL") or OLLAMA_MODEL


def _timeout() -> tuple[float, float]:
    """(connect, read) — le read peut être long au 1er chargement du modèle."""
    connect = float(os.getenv("OLLAMA_CONNECT_TIMEOUT", "5"))
    read = float(os.getenv("OLLAMA_TIMEOUT", "180"))
    return (connect, read)


def ollama_available() -> bool:
    global _consecutive_failures, _skip_until
    if time.time() < _skip_until:
        logger.debug("Ollama en cooldown jusqu'à %s", _skip_until)
        return False
    try:
        res = requests.get(f"{_base_url()}/api/tags", timeout=3)
        if res.status_code != 200:
            return False
        names = {m.get("name") for m in (res.json().get("models") or [])}
        model = _model()
        ok = model in names or any(
            (n or "").startswith(model.split(":")[0]) for n in names
        )
        logger.debug("Ollama dispo=%s modèle=%s", ok, model)
        return ok
    except Exception:
        logger.warning("Ollama injoignable sur %s", _base_url())
        return False


def _mark_success() -> None:
    global _consecutive_failures, _skip_until
    _consecutive_failures = 0
    _skip_until = 0.0


def _mark_failure(reason: str) -> None:
    global _consecutive_failures, _skip_until
    _consecutive_failures += 1
    logger.warning(
        "Ollama échec (%s) — streak=%d/%d",
        reason,
        _consecutive_failures,
        _MAX_FAILURES,
    )
    if _consecutive_failures >= _MAX_FAILURES:
        _skip_until = time.time() + _COOLDOWN_SEC
        logger.warning(
            "Ollama mis en pause %ds (trop de timeouts) — fallback heuristique",
            _COOLDOWN_SEC,
        )


def _extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _to_index(value: Any, n: int) -> int | None:
    if value is None or value == "" or value is False:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        idx = int(value)
        return idx if 1 <= idx <= n else None
    if isinstance(value, str):
        value = value.strip()
        if value.lower() in ("null", "none", "n/a", "0", "-"):
            return None
        m = re.search(r"\d+", value)
        if not m:
            return None
        idx = int(m.group(0))
        return idx if 1 <= idx <= n else None
    return None


def _call_ollama(payload: dict, retries: int = 2) -> dict[str, Any] | None:
    url = f"{_base_url()}/api/chat"
    timeout = _timeout()
    last_err: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            logger.debug("Ollama POST attempt=%d timeout=%s", attempt, timeout)
            res = requests.post(url, json=payload, timeout=timeout)
            res.raise_for_status()
            _mark_success()
            raw = (res.json().get("message") or {}).get("content") or ""
            return _extract_json(raw.strip()) or {}
        except (ReadTimeout, ConnectTimeout, Timeout) as e:
            last_err = e
            logger.warning(
                "Ollama timeout (tentative %d/%d, read=%ss) : %s",
                attempt,
                retries,
                timeout[1],
                e,
            )
            # 2e tentative : timeout encore plus large
            timeout = (timeout[0], min(timeout[1] * 1.5, 300))
            time.sleep(1)
        except (ConnectionError, OSError) as e:
            last_err = e
            logger.warning("Ollama connexion refusée (tentative %d/%d) : %s", attempt, retries, e)
            time.sleep(1)
        except Exception as e:
            last_err = e
            logger.warning("Ollama erreur inattendue : %s", e)
            break

    _mark_failure(str(last_err) if last_err else "unknown")
    return None


def pick_company_links(
    *,
    denomination: str,
    commune: str,
    results: list[dict[str, str]],
) -> dict[str, str | None]:
    """
    Demande à qwen3 de choisir par numéro parmi les résultats Google.
    En cas de timeout / erreur : retourne nulls (le caller bascule en heuristique).
    """
    if not results:
        return {"site_web": None, "linkedin_company": None}

    if time.time() < _skip_until:
        logger.info("Ollama en cooldown — skip pour %s", denomination)
        return {"site_web": None, "linkedin_company": None}

    # Limite le prompt (modèle petit + réponse rapide)
    results = results[:8]
    lines = []
    for i, r in enumerate(results, 1):
        lines.append(
            f"[{i}] {(r.get('title') or '')[:70]} | {(r.get('link') or '')[:100]}"
        )
    listing = "\n".join(lines)
    n = len(results)

    payload = {
        "model": _model(),
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "num_predict": 32,
            # Force une réponse courte même si thinking est supporté
            "num_ctx": 2048,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    'Reply JSON only: {"linkedin":N,"site":N}. Use 0 if missing. '
                    'Example: {"linkedin":1,"site":2}'
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Company: {denomination} ({commune or 'France'})\n"
                    f"{listing}\n"
                    "linkedin = linkedin.com/company number; site = official website number."
                ),
            },
        ],
    }

    logger.info(
        "Ollama (%s) — extraction pour %s (%d résultats, timeout=%ss)",
        _model(),
        denomination,
        n,
        _timeout()[1],
    )

    data = _call_ollama(payload, retries=2)
    if data is None:
        return {"site_web": None, "linkedin_company": None}

    li_idx = _to_index(
        data.get("linkedin", data.get("linkedin_company", data.get("linkedin_index"))),
        n,
    )
    site_idx = _to_index(
        data.get("site", data.get("site_web", data.get("website"))),
        n,
    )

    linkedin = None
    site = None
    if li_idx:
        link = (results[li_idx - 1].get("link") or "").split("?")[0]
        if "linkedin.com/company" in link.lower():
            linkedin = link
        else:
            logger.warning("Ollama linkedin idx=%s mais URL non company: %s", li_idx, link)
    if site_idx:
        link = (results[site_idx - 1].get("link") or "").split("?")[0]
        blocked = (
            "linkedin.com",
            "societe.com",
            "pagesjaunes",
            "pappers.fr",
            "infogreffe",
            "facebook.com",
            "verif.com",
            "manageo",
            "rubypayeur",
        )
        if not any(b in link.lower() for b in blocked):
            site = link
        else:
            logger.warning("Ollama site idx=%s rejeté (annuaire): %s", site_idx, link)

    logger.info(
        "Ollama choix %s — site[%s]=%s linkedin[%s]=%s",
        denomination,
        site_idx or 0,
        site or "—",
        li_idx or 0,
        linkedin or "—",
    )
    return {"site_web": site, "linkedin_company": linkedin}


_REPLY_CLASSES = frozenset({"offre", "entretien", "refus", "autre"})


def classify_reply(
    *,
    subject: str,
    body: str,
    from_name: str = "",
) -> tuple[str, str] | None:
    """
    Classe une réponse mail de candidature.
    Retourne (class, reason) avec class ∈ offre|entretien|refus|autre,
    ou None si échec.
    """
    if time.time() < _skip_until:
        return None

    text = (body or "")[:1500]
    sender_hint = f"\nExpéditeur: {from_name}" if from_name else ""
    payload = {
        "model": _model(),
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "num_predict": 80,
            "num_ctx": 2048,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu classes des réponses à une candidature spontanée. "
                    'Réponds JSON uniquement: {"class":"...","reason":"..."} où '
                    "class vaut offre (proposition d'embauche), entretien "
                    "(invitation RDV/appel), refus (candidature rejetée), ou autre "
                    "(accusé réception, question, spam…). "
                    'reason = une phrase courte en français expliquant pourquoi '
                    '(ex: "M. Dupont refuse car sur-effectif", '
                    '"Invitation entretien téléphonique jeudi 14h", '
                    '"Accusé de réception automatique").'
                ),
            },
            {
                "role": "user",
                "content": f"Sujet: {subject or '(sans)'}{sender_hint}\n\nCorps:\n{text}",
            },
        ],
    }

    logger.info("Ollama — classification réponse mail (sujet=%s)", (subject or "")[:60])
    data = _call_ollama(payload, retries=1)
    if not data:
        return None

    raw = (
        data.get("class")
        or data.get("classification")
        or data.get("label")
        or data.get("status")
        or ""
    )
    label = str(raw).strip().lower()
    # Normalise variantes
    aliases = {
        "offer": "offre",
        "job_offer": "offre",
        "interview": "entretien",
        "rdv": "entretien",
        "rejection": "refus",
        "refuse": "refus",
        "rejected": "refus",
        "other": "autre",
        "ack": "autre",
    }
    label = aliases.get(label, label)
    if label in _REPLY_CLASSES:
        reason = str(
            data.get("reason")
            or data.get("why")
            or data.get("explanation")
            or data.get("note")
            or ""
        ).strip()
        logger.info("Ollama classification → %s (%s)", label, reason[:80] if reason else "—")
        return label, reason
    logger.warning("Ollama classification invalide : %r", raw)
    return None


def generate_accroche(
    *,
    denomination: str,
    commune: str = "",
    naf_libelle: str = "",
    site_web: str = "",
    poste: str = "",
) -> str | None:
    """
    Génère 1–2 phrases de personnalisation pour le mail de candidature.
    Retourne None si Ollama indisponible / échec.
    """
    if time.time() < _skip_until:
        return None
    if not ollama_available():
        return None

    hints = []
    if commune:
        hints.append(f"Ville: {commune}")
    if naf_libelle:
        hints.append(f"Activité: {naf_libelle}")
    if site_web:
        hints.append(f"Site: {site_web}")
    if poste:
        hints.append(f"Destinataire: {poste}")
    hint_block = "\n".join(hints) if hints else "Pas d'infos supplémentaires."

    payload = {
        "model": _model(),
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {
            "temperature": 0.4,
            "num_predict": 120,
            "num_ctx": 2048,
        },
        "messages": [
            {
                "role": "system",
                "content": (
                    "Tu rédiges une accroche courte pour une candidature spontanée "
                    "en développement informatique (Toulon / Var). "
                    'Réponds JSON uniquement: {"accroche":"..."} '
                    "L'accroche fait 1 ou 2 phrases max, tutoiement interdit, "
                    "pas de flatterie creuse, pas d'emoji, pas de guillemets inutiles. "
                    "Mentionne un détail concret sur l'entreprise si possible "
                    "(produit, métier, zone), sinon reste sobre et local."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Entreprise: {denomination}\n{hint_block}\n"
                    "Rédige l'accroche à insérer juste après 'Bonjour …'."
                ),
            },
        ],
    }

    logger.info("Ollama — accroche pour %s", denomination)
    data = _call_ollama(payload, retries=1)
    if not data:
        return None
    text = str(
        data.get("accroche") or data.get("hook") or data.get("text") or ""
    ).strip()
    text = re.sub(r'^["«]|["»]$', "", text).strip()
    if len(text) < 20 or len(text) > 400:
        logger.warning("Accroche Ollama rejetée (longueur=%d)", len(text))
        return None
    return text


def pick_best_contact(
    *,
    denomination: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Demande à Ollama de choisir le meilleur contact RH/tech parmi des candidats."""
    if not candidates:
        return None
    if time.time() < _skip_until:
        return candidates[0]

    lines = []
    for i, c in enumerate(candidates[:6], 1):
        lines.append(
            f"[{i}] {c.get('contact_prenom', '')} {c.get('contact_nom', '')} "
            f"— {c.get('contact_poste', '')} — {c.get('contact_linkedin', '')}"
        )
    n = min(len(candidates), 6)
    payload = {
        "model": _model(),
        "stream": False,
        "think": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 24, "num_ctx": 2048},
        "messages": [
            {
                "role": "system",
                "content": (
                    'Reply JSON only: {"pick":N}. Prefer CTO/tech lead/RH/recruteur '
                    "over generic directors. Use 0 if none fit."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Company: {denomination}\n" + "\n".join(lines)
                    + "\nBest contact for a spontaneous software-dev application?"
                ),
            },
        ],
    }
    data = _call_ollama(payload, retries=1)
    if not data:
        return candidates[0]
    idx = _to_index(data.get("pick", data.get("index", data.get("n"))), n)
    if not idx:
        return candidates[0]
    return candidates[idx - 1]
