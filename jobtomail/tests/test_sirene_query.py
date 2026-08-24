from __future__ import annotations

from jobtomail.services.sirene import (
    _chunked,
    build_sirene_query_categorie_juridique,
    build_sirene_query_naf,
)


def test_chunked_splits_by_size():
    items = [str(i) for i in range(10)]
    chunks = _chunked(items, 3)
    assert chunks == [["0", "1", "2"], ["3", "4", "5"], ["6", "7", "8"], ["9"]]


def test_chunked_empty_list():
    assert _chunked([], 5) == []


def test_build_sirene_query_naf_or_clauses():
    query = build_sirene_query_naf(["62.01Z", "62.02A"], ["83001", "83002"])
    assert "62.01Z OR 62.02A" in query
    assert "83001 OR 83002" in query
    assert "etatAdministratifEtablissement:A" in query


def test_build_sirene_query_categorie_juridique_default():
    query = build_sirene_query_categorie_juridique(["7220"], ["83001"])
    assert "categorieJuridiqueUniteLegale:(7220)" in query
    assert "caractereEmployeurUniteLegale" not in query


def test_build_sirene_query_categorie_juridique_employeur_seulement():
    query = build_sirene_query_categorie_juridique(["7220"], ["83001"], employeur_seulement=True)
    assert "caractereEmployeurUniteLegale:O" in query
