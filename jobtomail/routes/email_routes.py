"""Routes Hunter.io + envoi email + vérification des réponses + relances."""

from __future__ import annotations

import imaplib
import logging

from flask import Blueprint, jsonify, request

from jobtomail import db
from jobtomail.db import env_or_config, get_entreprise
from jobtomail.routes.auth import current_user_id
from jobtomail.services.email_quality import assess_email
from jobtomail.services.hunter import find_email as hunter_find_email
from jobtomail.services.email_finder import find_email as find_email_smtp, FoundEmail
from jobtomail.services.inbox import check_replies
from jobtomail.services.mailer import mail_body, send_candidature_email, send_relance_email
from jobtomail.services.ollama import generate_accroche, ollama_available
from jobtomail.services.relances import list_relances_dues, relance_status
from jobtomail.services.serpapi import extract_domain

logger = logging.getLogger(__name__)

bp = Blueprint("email", __name__)


def _smtp_credentials(data: dict) -> tuple[str, str]:
    email_address = data.get("EMAIL_ADDRESS") or env_or_config("EMAIL_ADDRESS")
    email_password = data.get("EMAIL_PASSWORD") or env_or_config("EMAIL_PASSWORD")
    return email_address, email_password


@bp.route("/api/manual/find-email", methods=["POST"])
def find_email_manual():
    data = request.get_json(force=True) or {}
    domain = extract_domain(data.get("domain") or "")
    prenom = (data.get("prenom") or "").strip()
    nom = (data.get("nom") or "").strip()
    siret = (data.get("siret") or "").strip()
    
    if not domain or not prenom or not nom:
        logger.warning("Recherche email manuelle : paramètres incomplets")
        return jsonify({"error": "Domaine, prénom et nom requis"}), 400

    logger.info("POST /api/manual/find-email — domain=%s prenom=%s nom=%s", domain, prenom, nom)
    email_data: FoundEmail = find_email_smtp(
        domain=domain,
        prenom=prenom,
        nom=nom
    )
    logger.info("POST /api/manual/find-email — résultat=%s", email_data)

    if email_data is None:
        return jsonify({"ok": True, "data": None, "quality": None})
    
    quality = assess_email(email_data.email, company_domain=domain, hunter_score=email_data.score)
    if siret and email_data.email:
        db.apply_email_quality(
            current_user_id(),
            siret,
            email=email_data.email,
            hunter_score=email_data.score,
            quality=quality["quality"],
            note=quality["note"],
        )
        
    return jsonify({"ok": True, "data": {
        "email": email_data.email,
        "status": email_data.status.name,
        "score": email_data.score,
    }, "quality": quality})


@bp.route("/api/hunter/find-email", methods=["POST"])
def find_email_hunter():
    data = request.get_json(force=True) or {}
    domain = extract_domain(data.get("domain") or "")
    prenom = (data.get("prenom") or "").strip()
    nom = (data.get("nom") or "").strip()
    siret = (data.get("siret") or "").strip()
    hunter_key = data.get("TOKEN_HUNTER_IO") or env_or_config("TOKEN_HUNTER_IO")

    if not hunter_key:
        logger.error("Hunter.io refusé : TOKEN manquant")
        return jsonify({"error": "TOKEN_HUNTER_IO manquant"}), 400
    if not domain or not prenom or not nom:
        logger.warning("Hunter.io paramètres incomplets")
        return jsonify({"error": "Domaine, prénom et nom requis"}), 400

    try:
        email_data = hunter_find_email(
            domain=domain,
            prenom=prenom,
            nom=nom,
            hunter_key=hunter_key,
        )
    except Exception as e:
        logger.exception("Hunter.io échoué")
        return jsonify({"error": str(e)}), 500

    found = email_data.get("email") or ""
    score = email_data.get("score")
    try:
        score_int = int(score) if score is not None else None
    except (TypeError, ValueError):
        score_int = None

    quality = assess_email(found, company_domain=domain, hunter_score=score_int)
    if siret and found:
        db.apply_email_quality(
            current_user_id(),
            siret,
            email=found,
            hunter_score=score_int,
            quality=quality["quality"],
            note=quality["note"],
        )

    return jsonify({"ok": True, "data": email_data, "quality": quality})


@bp.route("/api/email/assess", methods=["POST"])
def assess_email_route():
    data = request.get_json(force=True) or {}
    email = (data.get("email") or "").strip()
    domain = data.get("domain") or ""
    score = data.get("hunter_score")
    try:
        score_int = int(score) if score is not None and score != "" else None
    except (TypeError, ValueError):
        score_int = None
    quality = assess_email(email, company_domain=domain, hunter_score=score_int)
    siret = (data.get("siret") or "").strip()
    if siret and email:
        db.apply_email_quality(
            current_user_id(),
            siret,
            email=email,
            hunter_score=score_int,
            quality=quality["quality"],
            note=quality["note"],
        )
    return jsonify({"ok": True, "quality": quality})


@bp.route("/api/mail/accroche", methods=["POST"])
def generate_accroche_route():
    data = request.get_json(force=True) or {}
    siret = (data.get("siret") or "").strip()
    denomination = (data.get("denomination") or "").strip()
    commune = (data.get("commune") or "").strip()
    naf_libelle = (data.get("naf_libelle") or "").strip()
    site_web = (data.get("site_web") or "").strip()
    poste = (data.get("poste") or "").strip()

    if siret:
        ent = get_entreprise(current_user_id(), siret)
        if ent:
            denomination = denomination or (ent["denomination"] or "")
            commune = commune or (ent["commune"] or "")
            naf_libelle = naf_libelle or (ent["naf_libelle"] or "")
            site_web = site_web or (ent["site_web"] or "")
            poste = poste or (ent["contact_poste"] or "")

    if not denomination:
        return jsonify({"error": "denomination requise"}), 400
    if not ollama_available():
        return jsonify({"error": "Ollama indisponible — lance le modèle local", "ollama": False}), 503

    text = generate_accroche(
        denomination=denomination,
        commune=commune,
        naf_libelle=naf_libelle,
        site_web=site_web,
        poste=poste,
    )
    if not text:
        return jsonify({"error": "Impossible de générer l'accroche", "ollama": True}), 500

    if siret:
        db.update_entreprise(current_user_id(), siret, {"accroche": text})

    return jsonify({"ok": True, "accroche": text})


@bp.route("/api/mail/preview", methods=["POST"])
def preview_mail_route():
    data = request.get_json(force=True) or {}
    nom = (data.get("nom") or "").strip()
    prenom = (data.get("prenom") or "").strip()
    genre = (data.get("genre") or "m").strip()
    denomination = (data.get("denomination") or "").strip()
    poste = (data.get("poste") or "").strip()
    accroche = (data.get("accroche") or "").strip()
    body = mail_body(
        nom,
        genre,
        prenom=prenom,
        denomination=denomination,
        poste=poste,
        accroche=accroche,
    )
    return jsonify({"ok": True, "body": body})


@bp.route("/api/send-email", methods=["POST"])
def send_email_route():
    user_id = current_user_id()
    data = request.get_json(force=True) or {}
    to_email = (data.get("email") or "").strip()
    nom = (data.get("nom") or "").strip()
    genre = (data.get("genre") or "m").strip()
    siret = data.get("siret")
    prenom = (data.get("prenom") or "").strip()
    denomination = (data.get("denomination") or "").strip()
    poste = (data.get("poste") or "").strip()
    accroche = (data.get("accroche") or "").strip()
    force = bool(data.get("force", False))
    auto_accroche = bool(data.get("auto_accroche", True))

    email_address, email_password = _smtp_credentials(data)

    if not email_address or not email_password:
        logger.error("Envoi mail refusé : identifiants SMTP manquants")
        return jsonify({"error": "EMAIL_ADDRESS / EMAIL_PASSWORD manquants"}), 400
    if not to_email:
        logger.warning("Envoi mail refusé : destinataire manquant")
        return jsonify({"error": "Adresse email du destinataire manquante"}), 400

    company_domain = ""
    hunter_score = None
    if siret:
        ent = get_entreprise(user_id, siret)
        if ent:
            company_domain = ent["site_web"] or ""
            hunter_score = ent["email_hunter_score"] if "email_hunter_score" in ent.keys() else None
            if not accroche:
                accroche = (ent["accroche"] or "") if "accroche" in ent.keys() else ""
            if not denomination:
                denomination = ent["denomination"] or ""

    quality = assess_email(
        to_email,
        company_domain=company_domain,
        hunter_score=int(hunter_score) if hunter_score is not None else None,
    )
    if not quality["can_send"] and not force:
        return jsonify({
            "error": f"Email de qualité insuffisante : {quality['note']}",
            "quality": quality,
            "needs_force": True,
        }), 400
    if quality["quality"] == "warn" and not force and not data.get("accept_warn"):
        return jsonify({
            "error": f"Email douteux : {quality['note']}",
            "quality": quality,
            "needs_confirm": True,
        }), 409

    if auto_accroche and not accroche and ollama_available() and siret:
        ent = get_entreprise(user_id, siret)
        if ent:
            accroche = generate_accroche(
                denomination=ent["denomination"] or denomination,
                commune=ent["commune"] or "",
                naf_libelle=ent["naf_libelle"] or "",
                site_web=ent["site_web"] or "",
                poste=poste or (ent["contact_poste"] or ""),
            ) or ""

    try:
        result = send_candidature_email(
            user_id,
            to_email=to_email,
            nom=nom,
            genre=genre,
            email_address=email_address,
            email_password=email_password,
            siret=siret,
            prenom=prenom,
            denomination=denomination,
            poste=poste,
            accroche=accroche,
        )
    except Exception as e:
        logger.exception("Envoi SMTP échoué vers %s", to_email)
        return jsonify({"error": f"Erreur SMTP : {e}"}), 500

    if siret:
        db.apply_email_quality(
            user_id,
            siret,
            email=to_email,
            hunter_score=quality.get("hunter_score"),
            quality=quality["quality"],
            note=quality["note"],
        )

    return jsonify({"ok": True, "quality": quality, "accroche": accroche, **result})


@bp.route("/api/send-relance", methods=["POST"])
def send_relance_route():
    user_id = current_user_id()
    data = request.get_json(force=True) or {}
    to_email = (data.get("email") or "").strip()
    nom = (data.get("nom") or "").strip()
    genre = (data.get("genre") or "m").strip()
    siret = (data.get("siret") or "").strip()
    prenom = (data.get("prenom") or "").strip()
    denomination = (data.get("denomination") or "").strip()
    poste = (data.get("poste") or "").strip()
    force = bool(data.get("force", False))

    email_address, email_password = _smtp_credentials(data)

    if not email_address or not email_password:
        logger.error("Relance refusée : identifiants SMTP manquants")
        return jsonify({"error": "EMAIL_ADDRESS / EMAIL_PASSWORD manquants"}), 400
    if not to_email:
        return jsonify({"error": "Adresse email du destinataire manquante"}), 400
    if not siret:
        return jsonify({"error": "SIRET requis pour enregistrer la relance"}), 400

    ent = get_entreprise(user_id, siret)
    if not ent:
        return jsonify({"error": "Entreprise introuvable"}), 404

    info = relance_status(ent)
    if info.get("blocked") and not force:
        return jsonify({"error": info.get("reason"), "relance": info, "needs_force": False}), 400
    if not info.get("due") and not force:
        return jsonify({
            "error": info.get("reason"),
            "relance": info,
            "needs_force": True,
        }), 409

    in_reply_to = ent["email_message_id"] if ent["email_message_id"] else None
    original_subject = ent["email_subject"] if ent["email_subject"] else None
    if not in_reply_to:
        logger.warning("Relance sans Message-ID d'origine pour %s — envoi sans fil de discussion", siret)

    try:
        result = send_relance_email(
            user_id,
            to_email=to_email,
            nom=nom,
            genre=genre,
            email_address=email_address,
            email_password=email_password,
            siret=siret,
            prenom=prenom,
            denomination=denomination or (ent["denomination"] or ""),
            poste=poste,
            in_reply_to=in_reply_to,
            original_subject=original_subject,
            force=force,
        )
    except ValueError as e:
        return jsonify({"error": str(e), "relance": info}), 400
    except Exception as e:
        logger.exception("Relance SMTP échouée vers %s", to_email)
        return jsonify({"error": f"Erreur SMTP : {e}"}), 500

    return jsonify({
        "ok": True,
        "threaded": bool(in_reply_to),
        "relance": {**info, "angle": int(result.get("angle") or info.get("angle") or 1)},
        **result,
    })


@bp.route("/api/relances/dues", methods=["GET"])
def relances_dues_route():
    dues = list_relances_dues(current_user_id())
    return jsonify({"ok": True, "count": len(dues), "relances": dues})


@bp.route("/api/relances/status/<siret>", methods=["GET"])
def relance_status_route(siret: str):
    ent = get_entreprise(current_user_id(), siret)
    if not ent:
        return jsonify({"error": "Entreprise introuvable"}), 404
    return jsonify({"ok": True, "relance": relance_status(ent)})


@bp.route("/api/check-replies", methods=["POST"])
def check_replies_route():
    """
    Lit la boîte IMAP en lecture seule (mails restent non lus),
    classe les réponses (offre / entretien / refus / autre)
    et met à jour le statut des entreprises concernées.
    """
    data = request.get_json(force=True) or {}
    email_address, email_password = _smtp_credentials(data)

    if not email_address or not email_password:
        return jsonify({"error": "EMAIL_ADDRESS / EMAIL_PASSWORD manquants"}), 400

    limit = int(data.get("limit") or 80)
    limit = max(10, min(limit, 200))

    logger.info("POST /api/check-replies — limit=%d", limit)
    try:
        result = check_replies(current_user_id(), email_address, email_password, limit=limit)
    except imaplib.IMAP4.error as e:
        logger.exception("IMAP échoué")
        return jsonify({"error": f"Erreur IMAP : {e}"}), 500
    except Exception as e:
        logger.exception("Vérification des réponses échouée")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)
