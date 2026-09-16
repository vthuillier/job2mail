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
import smtplib
from email.message import EmailMessage

from flask import current_app

logger = logging.getLogger(__name__)


def _send_smtp(to_email: str, subject: str, body: str) -> None:
    host = os.environ["TRANSACTIONAL_SMTP_HOST"]
    port = int(os.environ.get("TRANSACTIONAL_SMTP_PORT", "587"))
    user = os.environ["TRANSACTIONAL_SMTP_USER"]
    password = os.environ["TRANSACTIONAL_SMTP_PASSWORD"]
    from_addr = os.environ.get("TRANSACTIONAL_SMTP_FROM", user)
    use_ssl = os.environ.get("TRANSACTIONAL_SMTP_SSL", "0") == "1"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_email
    msg.set_content(body)

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port) as smtp:
        if not use_ssl:
            smtp.starttls()
        smtp.login(user, password)
        smtp.send_message(msg)


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
    if provider == "smtp":
        _send_smtp(
            to_email,
            "Votre lien de connexion JobToMail",
            f"Cliquez sur ce lien pour vous connecter (valable 15 minutes) :\n\n{link}",
        )
        logger.info("Lien magique envoyé par SMTP à %s", to_email)
        return
    raise NotImplementedError(
        f"Fournisseur d'email transactionnel '{provider}' non implémenté — "
        "câbler ici (ex: appel API Postmark/SES) sans changer la signature "
        "de send_magic_link_email."
    )
