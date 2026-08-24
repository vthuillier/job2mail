from __future__ import annotations

from datetime import date

from jobtomail.services.cleaner import clean_entreprises, score_pertinence


def _row(**overrides):
    row = {
        "nature": "entreprise",
        "effectif_code": "22",
        "date_creation": "2015-01-01",
        "est_siege": True,
        "categorie_entreprise": "PME",
    }
    row.update(overrides)
    return row


def test_score_pertinence_entreprise_bounds():
    score = score_pertinence(_row())
    assert 0.0 <= score <= 100.0


def test_score_pertinence_missing_effectif_and_date():
    score = score_pertinence(_row(effectif_code=None, date_creation=None))
    assert 0.0 <= score <= 100.0


def test_score_pertinence_mairie():
    siege = score_pertinence(_row(nature="mairie", est_siege=True))
    non_siege = score_pertinence(_row(nature="mairie", est_siege=False))
    assert siege > non_siege


def test_score_pertinence_association_zero_effectif_still_scores():
    score = score_pertinence(_row(nature="association", effectif_code="00"))
    assert 0.0 <= score <= 100.0


def test_score_pertinence_invalid_date_falls_back():
    score = score_pertinence(_row(date_creation="not-a-date"))
    assert 0.0 <= score <= 100.0


def test_score_pertinence_no_cv_keywords_matches_default_none():
    row = _row(naf_libelle="Programmation informatique", denomination="Acme Corp")
    assert score_pertinence(row) == score_pertinence(row, cv_keywords=None)
    assert score_pertinence(row, cv_keywords=[]) == score_pertinence(row, cv_keywords=None)


def test_score_pertinence_keyword_match_increases_score():
    row = _row(naf_libelle="Programmation informatique", denomination="Acme Python Corp")
    without = score_pertinence(row, cv_keywords=None)
    with_match = score_pertinence(row, cv_keywords=["python", "flask", "docker"])
    assert with_match > without


def test_score_pertinence_keyword_no_match_decreases_score():
    row = _row(naf_libelle="Boulangerie artisanale", denomination="Le Bon Pain")
    without = score_pertinence(row, cv_keywords=None)
    with_no_match = score_pertinence(row, cv_keywords=["python", "flask", "docker"])
    assert with_no_match < without
    assert 0.0 <= with_no_match <= 100.0


def test_clean_entreprises_dedup_and_filter(temp_db):
    from jobtomail import db

    db.insert_entreprise_ignore(
        {
            "siret": "11111111100001",
            "siren": "111111111",
            "denomination": "Petite Structure",
            "effectif_code": "00",
            "nature": "entreprise",
            "date_creation": "2020-01-01",
        }
    )
    db.insert_entreprise_ignore(
        {
            "siret": "22222222200001",
            "siren": "222222222",
            "denomination": "Siege Social",
            "effectif_code": "22",
            "est_siege": True,
            "nature": "entreprise",
            "date_creation": "2010-01-01",
        }
    )
    db.insert_entreprise_ignore(
        {
            "siret": "22222222200002",
            "siren": "222222222",
            "denomination": "Etablissement Secondaire",
            "effectif_code": "22",
            "est_siege": False,
            "nature": "entreprise",
            "date_creation": str(date.today()),
        }
    )

    result = clean_entreprises(apply_effectif_filter=True)

    assert result["excluded_effectif"] == 1
    assert result["duplicates_removed"] == 1
    remaining = db.list_entreprises()
    assert len(remaining) == 1
    assert remaining[0]["siret"] == "22222222200001"
