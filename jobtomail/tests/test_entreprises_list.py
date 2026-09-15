from __future__ import annotations


def _create(client, siret, denomination, **extra):
    payload = {"siret": siret, "denomination": denomination, **extra}
    res = client.post("/api/entreprises", json=payload)
    assert res.status_code == 201, res.get_json()
    return res.get_json()["entreprise"]


def test_get_entreprises_paginates(client):
    _create(client, "11111111100011", "Acme")
    _create(client, "22222222200022", "Beta")
    _create(client, "33333333300033", "Gamma")

    res = client.get("/api/entreprises?per_page=2&page=1")
    data = res.get_json()
    assert data["total"] == 3
    assert data["pages"] == 2
    assert data["page"] == 1
    assert len(data["entreprises"]) == 2

    res2 = client.get("/api/entreprises?per_page=2&page=2")
    data2 = res2.get_json()
    assert len(data2["entreprises"]) == 1
    assert data2["page"] == 2


def test_get_entreprises_filters_by_status(client):
    _create(client, "11111111100011", "Acme")
    e2 = _create(client, "22222222200022", "Beta")
    client.put(f"/api/entreprises/{e2['siret']}", json={"status": "postule"})

    res = client.get("/api/entreprises?status=postule")
    data = res.get_json()
    assert data["total"] == 1
    assert data["entreprises"][0]["siret"] == "22222222200022"


def test_get_entreprises_filters_by_search(client):
    _create(client, "11111111100011", "Acme Informatique")
    _create(client, "22222222200022", "Beta Boulangerie")

    res = client.get("/api/entreprises?search=informatique")
    data = res.get_json()
    assert data["total"] == 1
    assert data["entreprises"][0]["denomination"] == "Acme Informatique"


def test_get_entreprises_response_omits_linkedin_people_url(client):
    _create(client, "11111111100011", "Acme")
    res = client.get("/api/entreprises")
    entreprise = res.get_json()["entreprises"][0]
    assert "linkedin_people_url" not in entreprise


def test_get_entreprises_stats_counts(client):
    e1 = _create(client, "11111111100011", "Acme")
    _create(client, "22222222200022", "Beta")
    client.put(f"/api/entreprises/{e1['siret']}", json={"status": "postule"})

    res = client.get("/api/entreprises/stats")
    data = res.get_json()
    assert data["counts"]["tous"] == 2
    assert data["counts"]["postule"] == 1
    assert data["counts"]["a_postuler"] == 1
    assert "relances_dues" in data


def test_get_entreprises_lite_unpaginated(client):
    for i in range(5):
        _create(client, f"1111111110001{i}", f"Entreprise {i}")

    res = client.get("/api/entreprises/lite")
    data = res.get_json()
    assert len(data["entreprises"]) == 5
    # champs allégés uniquement
    assert "notes" not in data["entreprises"][0]
    assert "siret" in data["entreprises"][0]


def test_get_naf_codes_used_returns_distinct_codes(client):
    _create(client, "11111111100011", "Acme", naf_code="62.01Z", naf_libelle="Programmation informatique")
    _create(client, "22222222200022", "Beta", naf_code="62.01Z", naf_libelle="Programmation informatique")
    _create(client, "33333333300033", "Gamma", naf_code="56.10A", naf_libelle="Restauration")

    res = client.get("/api/entreprises/naf-codes-used")
    codes = {c["code"] for c in res.get_json()["codes"]}
    assert codes == {"62.01Z", "56.10A"}


def test_get_single_entreprise_route(client):
    _create(client, "11111111100011", "Acme")
    res = client.get("/api/entreprises/11111111100011")
    data = res.get_json()
    assert data["entreprise"]["denomination"] == "Acme"
    assert "relance_info" in data["entreprise"]


def test_get_single_entreprise_route_404(client):
    res = client.get("/api/entreprises/00000000000000")
    assert res.status_code == 404
