"""Lecture IMAP des réponses (sans marquer lus) + classification."""

from __future__ import annotations

import email
import imaplib
import logging
import re
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parseaddr
from typing import Any

from jobtomail import db
from jobtomail.services.ollama import classify_reply, ollama_available

logger = logging.getLogger(__name__)

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Statuts finaux qu'on applique automatiquement
CLASSIFIABLE = frozenset({"offre", "entretien", "refus", "autre"})

# Mots-clés heuristiques (FR) — fallback si Ollama indisponible
_REFUS_KW = (
    "ne donnerons pas suite",
    "n'a pas été retenue",
    "n'a pas ete retenue",
    "candidature n'a pas",
    "autre profil",
    "profils plus",
    "ne correspond pas",
    "malheureusement",
    "regret",
    "sans suite",
    "pas retenu",
    "pas retenue",
    "refus",
    "négatif",
    "negatif",
    "closing this application",
    "not moving forward",
    "other candidates",
)
_ENTRETIEN_KW = (
    "entretien",
    "rendez-vous",
    "rendez vous",
    "échange téléphonique",
    "echange telephonique",
    "appel téléphonique",
    "appel telephonique",
    "planifier",
    "disponibilités",
    "disponibilites",
    "créneau",
    "creneau",
    "calendly",
    "visio",
    "visioconférence",
    "visioconference",
    "teams",
    "meet.google",
    "zoom.us",
    "interview",
    "phone screen",
)
_OFFRE_KW = (
    "offre d'emploi",
    "offre de poste",
    "proposition d'embauche",
    "proposition d emploi",
    "contrat de travail",
    "nous souhaitons vous embaucher",
    "rejoindre notre équipe",
    "rejoindre notre equipe",
    "package salarial",
    "rémunération proposée",
    "remuneration proposee",
    "job offer",
    "offer letter",
    "we would like to offer",
)


@dataclass
class InboxReply:
    uid: str
    message_id: str
    in_reply_to: str
    references: str
    from_email: str
    from_name: str
    subject: str
    body: str
    date: str


def _decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _normalize_msgid(value: str | None) -> str:
    if not value:
        return ""
    value = value.strip()
    # Peut contenir plusieurs IDs (References)
    return value


def _msgid_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    return {m.lower() for m in re.findall(r"<[^>]+>", value)}


def _extract_body(msg: Message) -> str:
    texts: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            disp = str(part.get("Content-Disposition") or "").lower()
            if "attachment" in disp:
                continue
            if ctype == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    texts.append(payload.decode(charset, errors="replace"))
                except Exception:
                    texts.append(payload.decode("utf-8", errors="replace"))
            elif ctype == "text/html" and not texts:
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    html = payload.decode(charset, errors="replace")
                except Exception:
                    html = payload.decode("utf-8", errors="replace")
                texts.append(re.sub(r"<[^>]+>", " ", html))
    else:
        payload = msg.get_payload(decode=True) or b""
        charset = msg.get_content_charset() or "utf-8"
        try:
            texts.append(payload.decode(charset, errors="replace"))
        except Exception:
            texts.append(payload.decode("utf-8", errors="replace"))

    body = "\n".join(texts)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:4000]


def _parse_message(raw: bytes, uid: str) -> InboxReply | None:
    try:
        msg = email.message_from_bytes(raw)
    except Exception:
        logger.warning("Impossible de parser le mail UID=%s", uid)
        return None

    from_name, from_email = parseaddr(_decode_mime(msg.get("From")))
    return InboxReply(
        uid=uid,
        message_id=_normalize_msgid(msg.get("Message-ID") or ""),
        in_reply_to=_normalize_msgid(msg.get("In-Reply-To") or ""),
        references=_normalize_msgid(msg.get("References") or ""),
        from_email=(from_email or "").lower().strip(),
        from_name=from_name or "",
        subject=_decode_mime(msg.get("Subject")),
        body=_extract_body(msg),
        date=_decode_mime(msg.get("Date")),
    )


def fetch_inbox_replies(
    email_address: str,
    email_password: str,
    *,
    limit: int = 80,
) -> list[InboxReply]:
    """
    Lit la boîte IMAP en readonly + BODY.PEEK[] :
    les mails restent non lus côté Gmail.
    """
    logger.info("Connexion IMAP readonly → %s", email_address)
    mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=30)
    try:
        mail.login(email_address, email_password)
        # readonly=True : aucun flag (\Seen) ne peut être modifié
        typ, _ = mail.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError("Impossible d'ouvrir INBOX en lecture seule")

        # Les N plus récents (ALL) — BODY.PEEK + readonly gardent le non-lu.
        # On ne se limite pas à UNSEEN : d'autres mails non lus dilueraient le scan.
        typ, data = mail.search(None, "ALL")
        ids = (data[0] or b"").split() if typ == "OK" and data and data[0] else []

        # Les plus récents d'abord
        ids = ids[-limit:]
        ids.reverse()

        replies: list[InboxReply] = []
        for num in ids:
            uid = num.decode() if isinstance(num, bytes) else str(num)
            # BODY.PEEK[] : ne pose PAS le flag \Seen (même hors readonly)
            typ, data = mail.fetch(num, "(BODY.PEEK[])")
            if typ != "OK" or not data:
                continue
            raw = None
            for item in data:
                if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
                    raw = bytes(item[1])
                    break
            if not raw:
                continue
            parsed = _parse_message(raw, uid)
            if parsed:
                replies.append(parsed)

        logger.info("IMAP : %d message(s) lus (non marqués lus)", len(replies))
        return replies
    finally:
        try:
            mail.close()
        except Exception:
            pass
        try:
            mail.logout()
        except Exception:
            pass


def _heuristic_reason(
    label: str,
    matched_kws: list[str],
    *,
    from_name: str = "",
) -> str:
    who = from_name.strip() if from_name else "L'expéditeur"
    if label == "autre":
        return f"{who} — pas de signal clair (offre/entretien/refus)"
    kw_sample = ", ".join(matched_kws[:3])
    labels_fr = {
        "offre": "proposition d'embauche",
        "entretien": "invitation entretien",
        "refus": "refus",
    }
    kind = labels_fr.get(label, label)
    if kw_sample:
        return f"{who} — {kind} détecté (« {kw_sample} »)"
    return f"{who} — {kind} détecté"


def classify_reply_heuristic(
    subject: str,
    body: str,
    *,
    from_name: str = "",
) -> tuple[str, str]:
    text = f"{subject}\n{body}".lower()

    # Ordre : offre > entretien > refus (mots plus spécifiques d'abord)
    offre_kws = [kw for kw in _OFFRE_KW if kw in text]
    entretien_kws = [kw for kw in _ENTRETIEN_KW if kw in text]
    refus_kws = [kw for kw in _REFUS_KW if kw in text]

    scores = {
        "offre": len(offre_kws),
        "entretien": len(entretien_kws),
        "refus": len(refus_kws),
    }
    matched = {"offre": offre_kws, "entretien": entretien_kws, "refus": refus_kws}
    best = max(scores, key=scores.get)
    if scores[best] <= 0:
        return "autre", _heuristic_reason("autre", [], from_name=from_name)
    # En cas d'égalité, priorité offre > entretien > refus
    tied = [k for k, v in scores.items() if v == scores[best]]
    label = best
    for preferred in ("offre", "entretien", "refus"):
        if preferred in tied:
            label = preferred
            break
    return label, _heuristic_reason(label, matched[label], from_name=from_name)


def classify_reply_text(
    subject: str,
    body: str,
    *,
    from_name: str = "",
) -> tuple[str, str]:
    if ollama_available():
        result = classify_reply(subject=subject, body=body, from_name=from_name)
        if result and result[0] in CLASSIFIABLE:
            label, reason = result
            if not reason:
                reason = _heuristic_reason(label, [], from_name=from_name)
            return label, reason
    return classify_reply_heuristic(subject, body, from_name=from_name)


def _match_entreprise(
    reply: InboxReply,
    candidatures: list[dict[str, Any]],
) -> dict[str, Any] | None:
    reply_refs = _msgid_tokens(reply.in_reply_to) | _msgid_tokens(reply.references)

    # 1) Match par In-Reply-To / References ↔ Message-ID envoyé
    for ent in candidatures:
        our_ids = _msgid_tokens(ent.get("email_message_id"))
        if our_ids and our_ids & reply_refs:
            return ent

    # 2) Match par adresse expéditeur = contact_email
    if reply.from_email:
        matches = [
            e
            for e in candidatures
            if (e.get("contact_email") or "").lower().strip() == reply.from_email
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            # Affiner via sujet
            subj = (reply.subject or "").lower()
            for e in matches:
                orig = (e.get("email_subject") or "").lower()
                if orig and orig in subj:
                    return e
            return matches[0]

    # 3) Match par sujet contenant le sujet d'origine
    subj = (reply.subject or "").lower()
    if subj.startswith("re:"):
        for ent in candidatures:
            orig = (ent.get("email_subject") or "").lower().strip()
            if orig and orig in subj:
                return ent

    return None


def check_replies(
    email_address: str,
    email_password: str,
    *,
    limit: int = 80,
) -> dict[str, Any]:
    """
    Scanne la boîte, classe les réponses liées aux candidatures,
    met à jour le statut — sans ouvrir / marquer les mails comme lus.
    """
    candidatures = db.list_candidatures_en_attente()
    if not candidatures:
        return {
            "ok": True,
            "scanned": 0,
            "matched": 0,
            "updated": 0,
            "results": [],
            "message": "Aucune candidature en attente (postulé / relancé)",
        }

    already = db.list_processed_reply_ids()
    replies = fetch_inbox_replies(email_address, email_password, limit=limit)

    results: list[dict[str, Any]] = []
    updated = 0
    matched = 0

    for reply in replies:
        mid = (reply.message_id or "").strip()
        if mid and mid.lower() in already:
            continue

        ent = _match_entreprise(reply, candidatures)
        if not ent:
            continue

        matched += 1
        classification, reason = classify_reply_text(
            reply.subject,
            reply.body,
            from_name=reply.from_name,
        )
        excerpt = (reply.body or "")[:280]

        db.mark_reply_classified(
            ent["siret"],
            classification,
            message_id=mid or f"uid:{reply.uid}",
            subject=reply.subject,
            excerpt=excerpt,
            from_email=reply.from_email,
            reason=reason,
        )
        updated += 1
        if mid:
            already.add(mid.lower())

        results.append(
            {
                "siret": ent["siret"],
                "denomination": ent.get("denomination"),
                "from": reply.from_email,
                "subject": reply.subject,
                "classification": classification,
                "reason": reason,
                "status_applied": classification if classification != "autre" else ent.get("status"),
            }
        )
        logger.info(
            "Réponse classée %s → %s (%s) — %s",
            ent.get("denomination"),
            classification,
            reply.from_email,
            reason,
        )

    return {
        "ok": True,
        "scanned": len(replies),
        "matched": matched,
        "updated": updated,
        "results": results,
    }
