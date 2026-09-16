"""Résolution centralisée des clés API tierces.

- `INSEE_TOKEN` : variable d'environnement globale côté serveur (fournie par
  l'exploitant), jamais stockée par utilisateur.
- `SERPAPI_KEY` / `TOKEN_HUNTER_IO` : optionnelles, fournies par utilisateur
  et stockées chiffrées au repos (voir `jobtomail.services.crypto`) dans la
  table `user_config`. Un override d'environnement (clé opérateur) reste
  prioritaire, comme pour le reste de la configuration par utilisateur.
"""

from __future__ import annotations

from jobtomail import db
from jobtomail.services.crypto import decrypt


def insee_token() -> str:
    """Clé API INSEE : uniquement côté serveur (jamais par utilisateur)."""
    return db.env_or_config("INSEE_TOKEN")


def _decrypted_user_config_value(user_id: int, key: str) -> str:
    raw = db.get_user_config_value(user_id, key)
    if not raw:
        return ""
    try:
        return decrypt(raw)
    except Exception:
        # Valeur déjà en clair (ancienne donnée écrite avant le passage au
        # chiffrement) — on la renvoie telle quelle plutôt que d'échouer.
        return raw


def serpapi_key_for(user_id: int) -> str | None:
    import os

    for name in ("SERPAPI_KEY", "SERPAPI_TOKEN"):
        val = os.getenv(name)
        if val:
            return val.strip()
    return _decrypted_user_config_value(user_id, "SERPAPI_KEY") or None


def hunter_key_for(user_id: int) -> str | None:
    import os

    val = os.getenv("TOKEN_HUNTER_IO")
    if val:
        return val.strip()
    return _decrypted_user_config_value(user_id, "TOKEN_HUNTER_IO") or None
