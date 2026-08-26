from __future__ import annotations


def test_get_entreprises_empty(client):
    res = client.get("/api/entreprises")
    assert res.status_code == 200
    assert res.get_json()["entreprises"] == []


def test_get_config_defaults(client):
    res = client.get("/api/config")
    assert res.status_code == 200
    data = res.get_json()
    assert data["point_ref"] == "La Crau"
    assert data["needs_setup"] is True


def test_post_then_get_config_roundtrip(client):
    payload = {
        "candidate_name": "Jean Dupont",
        "EMAIL_ADDRESS": "jean@example.com",
        "INSEE_TOKEN": "dummy-token",
    }
    post_res = client.post("/api/config", json=payload)
    assert post_res.status_code == 200

    get_res = client.get("/api/config")
    data = get_res.get_json()
    assert data["candidate_name"] == "Jean Dupont"
    assert data["needs_setup"] is False


def test_post_config_rejects_internal_keys(client):
    client.post("/api/config", json={"_secret_key": "attacker-controlled"})
    res = client.get("/api/config")
    assert res.status_code == 200
    # la clé interne ne doit jamais apparaître dans une réponse publique
    assert "_secret_key" not in res.get_json()


def test_scan_sirene_missing_token_returns_400(client):
    res = client.post("/api/scan/sirene", json={})
    assert res.status_code == 400
    assert "error" in res.get_json()


def test_prune_invalid_min_employees_returns_400(client):
    res = client.post("/api/prune", json={"min_employees": "not-a-number"})
    assert res.status_code == 400


def test_index_page_has_db_backend_section(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b'id="db-backend-select"' in res.data
