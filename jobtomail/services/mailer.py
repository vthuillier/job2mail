"""Envoi du mail de candidature et des relances (SMTP Gmail)."""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from jobtomail.constants import (
    CV_PATH,
    DEFAULT_MAIL_BODY,
    DEFAULT_RELANCE2_BODY,
    DEFAULT_RELANCE_BODY,
    MAIL_SUBJECT,
    MAX_RELANCES,
)
from jobtomail import db
from jobtomail.services.relances import relance_status

logger = logging.getLogger(__name__)


def _salutation(genre: str) -> str:
    return "Monsieur" if (genre or "m").lower().startswith("m") else "Madame"


def _render_template(template: str, **kwargs: str) -> str:
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", value or "")
    # Nettoie les lignes d'accroche vides
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip() + "\n"


def _cv_attachment_filename() -> str:
    name = (db.get_all_config().get("candidate_name") or "").strip()
    return f"CV - {name}.pdf" if name else "CV.pdf"


def get_mail_templates() -> dict[str, str]:
    cfg = db.get_all_config()
    return {
        "subject": cfg.get("mail_subject") or MAIL_SUBJECT,
        "body": cfg.get("mail_body") or DEFAULT_MAIL_BODY,
        "relance_body": cfg.get("relance_body") or DEFAULT_RELANCE_BODY,
        "relance2_body": cfg.get("relance2_body") or DEFAULT_RELANCE2_BODY,
    }


def mail_body(
    nom: str,
    genre: str,
    *,
    prenom: str = "",
    denomination: str = "",
    poste: str = "",
    accroche: str = "",
    body_template: str | None = None,
) -> str:
    template = body_template or get_mail_templates()["body"]
    accroche_txt = (accroche or "").strip()
    if accroche_txt and "{accroche}" not in template:
        # Anciens templates sans placeholder : insert après la 1ère ligne
        lines = template.split("\n", 1)
        if len(lines) == 2:
            template = f"{lines[0]}\n\n{accroche_txt}\n{lines[1]}"
        else:
            template = f"{template}\n\n{accroche_txt}\n"
    return _render_template(
        template,
        nom=nom,
        prenom=prenom,
        salutation=_salutation(genre),
        denomination=denomination,
        poste=poste,
        accroche=accroche_txt,
    )


def relance_body(
    nom: str,
    genre: str,
    *,
    prenom: str = "",
    denomination: str = "",
    poste: str = "",
    angle: int = 1,
    body_template: str | None = None,
) -> str:
    templates = get_mail_templates()
    if body_template:
        template = body_template
    elif angle >= 2:
        template = templates["relance2_body"]
    else:
        template = templates["relance_body"]
    return _render_template(
        template,
        nom=nom,
        prenom=prenom,
        salutation=_salutation(genre),
        denomination=denomination,
        poste=poste,
        accroche="",
    )


def _reply_subject(original_subject: str) -> str:
    subject = (original_subject or MAIL_SUBJECT).strip()
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}"


def _smtp_send(msg: EmailMessage, email_address: str, email_password: str) -> None:
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as smtp:
        smtp.login(email_address, email_password)
        smtp.send_message(msg)


def send_candidature_email(
    user_id: int,
    *,
    to_email: str,
    nom: str,
    genre: str,
    email_address: str,
    email_password: str,
    siret: str | None = None,
    prenom: str = "",
    denomination: str = "",
    poste: str = "",
    accroche: str = "",
) -> dict[str, str]:
    logger.info("Envoi mail candidature → %s (contact=%s)", to_email, nom)

    templates = get_mail_templates()
    subject = templates["subject"]
    body = mail_body(
        nom,
        genre,
        prenom=prenom,
        denomination=denomination,
        poste=poste,
        accroche=accroche,
        body_template=templates["body"],
    )
    domain = email_address.split("@")[-1] if "@" in email_address else "gmail.com"
    message_id = make_msgid(domain=domain)

    msg = EmailMessage()
    msg["From"] = email_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id
    msg.set_content(body)

    if CV_PATH.exists():
        with open(CV_PATH, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=_cv_attachment_filename(),
            )
        logger.debug("CV joint : %s", CV_PATH)
    else:
        logger.warning("CV introuvable : %s", CV_PATH)

    _smtp_send(msg, email_address, email_password)
    logger.info("Mail envoyé avec succès à %s (Message-ID=%s)", to_email, message_id)

    if siret:
        db.mark_email_sent(
            user_id,
            siret,
            to_email,
            message_id=message_id,
            subject=subject,
            body=body,
        )
        if accroche:
            db.update_entreprise(user_id, siret, {"accroche": accroche})

    return {"message_id": message_id, "subject": subject, "body_preview": body[:280]}


def send_relance_email(
    user_id: int,
    *,
    to_email: str,
    nom: str,
    genre: str,
    email_address: str,
    email_password: str,
    siret: str,
    prenom: str = "",
    denomination: str = "",
    poste: str = "",
    in_reply_to: str | None = None,
    original_subject: str | None = None,
    force: bool = False,
) -> dict[str, str]:
    ent = db.get_entreprise(user_id, siret)
    if not ent:
        raise ValueError("Entreprise introuvable")

    info = relance_status(ent)
    if info.get("blocked") and not force:
        raise ValueError(info.get("reason") or "Relance bloquée")
    if not info.get("due") and not force:
        raise ValueError(info.get("reason") or "Relance trop tôt")

    angle = int(info.get("angle") or 1)
    count = int(ent["relance_count"] or 0)
    if count >= MAX_RELANCES and not force:
        raise ValueError(f"Maximum {MAX_RELANCES} relances atteint")

    logger.info(
        "Envoi relance n°%d (angle=%d) → %s (siret=%s)",
        count + 1,
        angle,
        to_email,
        siret,
    )

    templates = get_mail_templates()
    subject = _reply_subject(original_subject or templates["subject"])
    rel_body = relance_body(
        nom,
        genre,
        prenom=prenom,
        denomination=denomination,
        poste=poste,
        angle=angle,
    )

    previous_body = (ent["email_body"] or "").strip() if "email_body" in ent.keys() else ""
    if previous_body:
        quoted_previous = "\n".join(f"> {line}" for line in previous_body.split("\n"))
        sent_date = ent.get("last_relance_at") or ent.get("email_sent_at") or ""
        header = f"Le {sent_date}, {email_address} a écrit :" if sent_date else "Message précédent :"
        body = f"{rel_body}\n\n{header}\n{quoted_previous}"
    else:
        body = rel_body

    domain = email_address.split("@")[-1] if "@" in email_address else "gmail.com"
    message_id = make_msgid(domain=domain)

    msg = EmailMessage()
    msg["From"] = email_address
    msg["To"] = to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = message_id
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    if CV_PATH.exists():
        with open(CV_PATH, "rb") as f:
            msg.add_attachment(
                f.read(),
                maintype="application",
                subtype="pdf",
                filename=_cv_attachment_filename(),
            )
        logger.debug("CV joint à la relance : %s", CV_PATH)
    else:
        logger.warning("CV introuvable pour la relance : %s", CV_PATH)

    _smtp_send(msg, email_address, email_password)
    logger.info(
        "Relance envoyée à %s (Message-ID=%s, In-Reply-To=%s, angle=%d)",
        to_email,
        message_id,
        in_reply_to,
        angle,
    )

    db.mark_relance_sent(user_id, siret, message_id=message_id, body=body)

    return {
        "message_id": message_id,
        "subject": subject,
        "angle": str(angle),
        "relance_number": str(count + 1),
    }
