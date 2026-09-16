"""Envoi d'emails transactionnels (lien magique, notifications système).

Indépendant du compte Gmail de l'utilisateur — ces emails partent avant
qu'un utilisateur ait un compte/token OAuth. En l'absence de
TRANSACTIONAL_EMAIL_PROVIDER, se contente de logger un message (mode dev),
sans jamais exposer le lien/token en dehors du mode debug — voir la note
sur le fournisseur "log" ci-dessous.
"""

from __future__ import annotations

import logging
import os

from flask import current_app

logger = logging.getLogger(__name__)


def send_magic_link_email(to_email: str, link: str) -> None:
    provider = os.getenv("TRANSACTIONAL_EMAIL_PROVIDER", "log")
    if provider == "log":
        # Le lien magique embarque un token de connexion signé, équivalent à
        # un identifiant porteur, valable 15 minutes : il ne doit jamais être
        # loggé en dehors des sessions de debug local, sous peine de fuiter
        # un identifiant de connexion à quiconque a accès aux logs.
        if current_app.debug:
            logger.info("[dev] Lien magique pour %s : %s", to_email, link)
        else:
            logger.info("Lien magique demandé pour %s", to_email)
        return
    raise NotImplementedError(
        f"Fournisseur d'email transactionnel '{provider}' non implémenté — "
        "câbler ici (ex: appel API Postmark/SES) sans changer la signature "
        "de send_magic_link_email."
    )
