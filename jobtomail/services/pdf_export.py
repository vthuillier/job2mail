"""Génération de fiches PDF A4 (entreprise seule ou export groupé)."""

from __future__ import annotations

import io
from datetime import datetime, date

from pypdf import PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from jobtomail.constants import STATUS_COLORS, STATUS_LABELS, TRANCHE_EFFECTIFS

_INK = colors.HexColor("#1f2937")
_FAINT = colors.HexColor("#6b7280")
_LINE = colors.HexColor("#e5e7eb")
_DEFAULT_STATUS_COLOR = "#8b9aab"

_styles = getSampleStyleSheet()
_STYLE_TITLE = ParagraphStyle(
    "Fiche_Title", parent=_styles["Title"], fontSize=18, leading=22, textColor=_INK, spaceAfter=2
)
_STYLE_SUB = ParagraphStyle(
    "Fiche_Sub", parent=_styles["Normal"], fontSize=10, textColor=_FAINT, spaceAfter=0
)
_STYLE_H2 = ParagraphStyle(
    "Fiche_H2",
    parent=_styles["Heading2"],
    fontSize=11.5,
    leading=14,
    textColor=_INK,
    spaceBefore=10,
    spaceAfter=4,
)
_STYLE_BODY = ParagraphStyle("Fiche_Body", parent=_styles["Normal"], fontSize=9.5, leading=13.5, textColor=_INK)
_STYLE_LABEL = ParagraphStyle("Fiche_Label", parent=_styles["Normal"], fontSize=8.5, textColor=_FAINT)


def _text(value) -> str:
    return str(value).strip() if value not in (None, "") else "—"


def _status_color(status: str) -> colors.Color:
    return colors.HexColor(STATUS_COLORS.get(status or "", _DEFAULT_STATUS_COLOR))


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status or "", status or "—")


def _format_date(raw) -> str:
    if not raw:
        return "—"
    text = str(raw).strip()
    cleaned = text.replace(" ", "T", 1) if " " in text and "T" not in text else text
    try:
        return datetime.fromisoformat(cleaned).strftime("%d/%m/%Y")
    except ValueError:
        return text


def _field_table(rows: list[tuple[str, str]]) -> Table:
    data = []
    for label, value in rows:
        data.append([Paragraph(label, _STYLE_LABEL), Paragraph(_text(value), _STYLE_BODY)])
    table = Table(data, colWidths=[42 * mm, 118 * mm])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _entreprise_flowables(e: dict) -> list:
    denomination = _text(e.get("denomination"))
    status = e.get("status") or "a_postuler"
    score = e.get("score_pertinence")
    score_txt = f"Score {round(float(score))}" if score not in (None, "") else None

    header_cells = [
        [
            Paragraph(denomination, _STYLE_TITLE),
            Paragraph(
                f'<font color="{STATUS_COLORS.get(status, _DEFAULT_STATUS_COLOR)}">●</font> '
                f"{_status_label(status)}" + (f" · {score_txt}" if score_txt else ""),
                ParagraphStyle("badge", parent=_STYLE_SUB, alignment=TA_RIGHT, fontSize=11),
            ),
        ]
    ]
    header = Table(header_cells, colWidths=[110 * mm, 50 * mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))

    flow: list = [
        header,
        Paragraph(_text(e.get("naf_libelle")) + (f" ({e.get('naf_code')})" if e.get("naf_code") else ""), _STYLE_SUB),
        Spacer(1, 4),
        HRFlowable(width="100%", thickness=1, color=_status_color(status), spaceAfter=6),
        Paragraph("Identité", _STYLE_H2),
        _field_table(
            [
                ("SIRET", e.get("siret")),
                ("SIREN", e.get("siren")),
                ("Adresse", e.get("adresse")),
                ("Commune", e.get("commune")),
                ("Effectif", e.get("effectif_libelle") or TRANCHE_EFFECTIFS.get(e.get("effectif_code"), "—")),
                ("Catégorie", e.get("categorie_entreprise")),
                ("Siège social", "Oui" if e.get("est_siege") else "Non"),
            ]
        ),
        Paragraph("Contact", _STYLE_H2),
        _field_table(
            [
                ("Nom", " ".join(filter(None, [e.get("contact_prenom"), e.get("contact_nom")])) or None),
                ("Poste", e.get("contact_poste")),
                ("Email", e.get("contact_email")),
                ("LinkedIn", e.get("contact_linkedin")),
                ("Source", e.get("contact_source")),
            ]
        ),
        Paragraph("Suivi candidature", _STYLE_H2),
        _field_table(
            [
                ("Statut", _status_label(status)),
                ("Accroche", e.get("accroche")),
                ("Notes", e.get("notes")),
                ("Date entretien", _format_date(e.get("entretien_date"))),
                ("Prochaine étape", e.get("entretien_next_step")),
                ("Relances envoyées", e.get("relance_count") or 0),
            ]
        ),
    ]

    travel_duration = e.get("travel_duration_min")
    travel_distance = e.get("travel_distance_km")
    if travel_duration is not None and travel_distance is not None:
        flow.append(Paragraph("Trajet", _STYLE_H2))
        flow.append(
            _field_table(
                [
                    ("Depuis", e.get("travel_origin")),
                    ("Durée", f"{round(float(travel_duration))} min"),
                    ("Distance", f"{float(travel_distance):.1f} km"),
                ]
            )
        )

    if e.get("site_web") or e.get("linkedin_company"):
        flow.append(Paragraph("Liens", _STYLE_H2))
        flow.append(
            _field_table(
                [
                    ("Site web", e.get("site_web")),
                    ("LinkedIn entreprise", e.get("linkedin_company")),
                ]
            )
        )

    return flow


def _footer(canvas, doc, label: str) -> None:
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(_FAINT)
    canvas.drawString(18 * mm, 12 * mm, f"{label} — exporté le {date.today().strftime('%d/%m/%Y')}")
    canvas.drawRightString(A4[0] - 18 * mm, 12 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_entreprise_pdf(entreprise: dict) -> bytes:
    """Fiche A4 complète pour une entreprise."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title=f"Fiche {entreprise.get('denomination', '')}",
    )
    label = "Job2Mail"
    doc.build(
        _entreprise_flowables(entreprise),
        onFirstPage=lambda c, d: _footer(c, d, label),
        onLaterPages=lambda c, d: _footer(c, d, label),
    )
    return buf.getvalue()


def _summary_pdf(entreprises: list[dict]) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=18 * mm,
        title="Export entreprises — sommaire",
    )
    rows = [["Entreprise", "Statut", "Score", "Commune"]]
    for e in entreprises:
        rows.append(
            [
                Paragraph(_text(e.get("denomination")), _STYLE_BODY),
                Paragraph(_status_label(e.get("status")), _STYLE_BODY),
                _text(round(float(e["score_pertinence"])) if e.get("score_pertinence") not in (None, "") else None),
                Paragraph(_text(e.get("commune")), _STYLE_BODY),
            ]
        )
    table = Table(rows, colWidths=[78 * mm, 32 * mm, 18 * mm, 42 * mm], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, 0), _INK),
                ("LINEBELOW", (0, 0), (-1, 0), 0.75, _LINE),
                ("LINEBELOW", (0, 1), (-1, -1), 0.5, _LINE),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    flow = [
        Paragraph("Export entreprises", _STYLE_TITLE),
        Paragraph(f"{len(entreprises)} entreprise(s) sélectionnée(s)", _STYLE_SUB),
        Spacer(1, 10),
        table,
    ]
    label = "Job2Mail"
    doc.build(flow, onFirstPage=lambda c, d: _footer(c, d, label), onLaterPages=lambda c, d: _footer(c, d, label))
    return buf.getvalue()


def build_entreprises_pdf(entreprises: list[dict]) -> bytes:
    """Export groupé : page sommaire + une fiche complète par entreprise."""
    writer = PdfWriter()
    for pdf_bytes in [_summary_pdf(entreprises)] + [build_entreprise_pdf(e) for e in entreprises]:
        writer.append(io.BytesIO(pdf_bytes))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
