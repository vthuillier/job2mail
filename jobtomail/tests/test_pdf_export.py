from __future__ import annotations

from pypdf import PdfReader
import io

from jobtomail.services.pdf_export import build_entreprise_pdf, build_entreprises_pdf


def _entreprise(siret="12345678900012", **overrides):
    base = {
        "siret": siret,
        "siren": siret[:9],
        "denomination": "Acme SAS",
        "adresse": "1 rue Test",
        "commune": "Toulon",
        "effectif_code": "12",
        "effectif_libelle": "20 à 49 salariés",
        "naf_code": "62.01Z",
        "naf_libelle": "Programmation informatique",
        "est_siege": 1,
        "categorie_entreprise": "PME",
        "score_pertinence": 87.4,
        "site_web": "https://acme.example",
        "linkedin_company": "https://linkedin.com/company/acme",
        "contact_prenom": "Jean",
        "contact_nom": "Dupont",
        "contact_poste": "CTO",
        "contact_email": "jean@acme.example",
        "contact_source": "LinkedIn",
        "status": "entretien",
        "notes": "RAS",
        "accroche": "Bonjour...",
        "entretien_date": "2026-09-20",
        "entretien_next_step": "Entretien technique",
        "relance_count": 1,
        "travel_origin": "La Crau",
        "travel_duration_min": 22.3,
        "travel_distance_km": 15.4,
    }
    base.update(overrides)
    return base


def test_build_entreprise_pdf_returns_valid_single_page_pdf():
    pdf_bytes = build_entreprise_pdf(_entreprise())
    assert pdf_bytes[:4] == b"%PDF"
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 1


def test_build_entreprise_pdf_handles_missing_optional_fields():
    minimal = {"siret": "12345678900012", "denomination": "Sans infos"}
    pdf_bytes = build_entreprise_pdf(minimal)
    assert pdf_bytes[:4] == b"%PDF"


def test_build_entreprises_pdf_merges_summary_plus_one_page_per_entreprise():
    e1 = _entreprise(siret="11111111100011")
    e2 = _entreprise(siret="22222222200022", denomination="Beta SARL", status="a_postuler")
    pdf_bytes = build_entreprises_pdf([e1, e2])
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) == 3  # sommaire + 2 fiches


def test_export_entreprise_pdf_route(client):
    client.post(
        "/api/entreprises",
        json={"denomination": "Acme SAS", "siret": "12345678900012", "status": "a_postuler"},
    )
    res = client.get("/api/entreprises/12345678900012/export.pdf")
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    assert res.data[:4] == b"%PDF"


def test_export_entreprise_pdf_route_404_when_missing(client):
    res = client.get("/api/entreprises/00000000000000/export.pdf")
    assert res.status_code == 404


def test_export_entreprises_pdf_route_grouped(client):
    client.post("/api/entreprises", json={"denomination": "Acme SAS", "siret": "12345678900012"})
    client.post("/api/entreprises", json={"denomination": "Beta SARL", "siret": "22222222200022"})
    res = client.post(
        "/api/entreprises/export.pdf",
        json={"sirets": ["12345678900012", "22222222200022"]},
    )
    assert res.status_code == 200
    assert res.mimetype == "application/pdf"
    reader = PdfReader(io.BytesIO(res.data))
    assert len(reader.pages) == 3


def test_export_entreprises_pdf_route_requires_sirets(client):
    res = client.post("/api/entreprises/export.pdf", json={"sirets": []})
    assert res.status_code == 400
