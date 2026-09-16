from __future__ import annotations

from cryptography.fernet import Fernet


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


def test_post_then_get_config_roundtrip(client, monkeypatch):
    # INSEE_TOKEN reste une variable d'environnement globale côté serveur
    # (jamais stockée par utilisateur) — on la fournit via l'environnement
    # pour que needs_setup passe à False une fois les autres champs remplis.
    monkeypatch.setenv("INSEE_TOKEN", "dummy-token")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())

    # needs_setup ne repose plus sur EMAIL_ADDRESS/EMAIL_PASSWORD (SMTP) mais
    # sur la présence d'un compte Gmail OAuth connecté (refresh token stocké).
    from jobtomail import db

    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    db.save_google_refresh_token(user_id, "fake-refresh-token")

    payload = {
        "candidate_name": "Jean Dupont",
        "INSEE_TOKEN": "user-supplied-token-should-be-ignored",
    }
    post_res = client.post("/api/config", json=payload)
    assert post_res.status_code == 200

    get_res = client.get("/api/config")
    data = get_res.get_json()
    assert data["candidate_name"] == "Jean Dupont"
    # Le token posté par le client ne doit jamais être persisté ni utilisé —
    # seule la variable d'environnement compte.
    assert data["INSEE_TOKEN"] == "dummy-token"
    assert data["google_connected_email"] == "test-user@example.com"
    assert data["needs_setup"] is False


def test_post_config_never_persists_insee_token(client):
    """INSEE_TOKEN : reste une variable d'environnement globale côté serveur,
    jamais exposée/persistée pour l'utilisateur (design spec §3)."""
    client.post("/api/config", json={"INSEE_TOKEN": "attacker-or-user-supplied"})
    res = client.get("/api/config")
    assert res.status_code == 200
    assert res.get_json()["INSEE_TOKEN"] == ""


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


def test_magic_link_login_sets_real_user_id_in_session(client, app):
    """Après un login réussi par lien magique, session['user_id'] doit pointer
    vers une vraie ligne de la table users (et non juste un entier magique en
    dur)."""
    from jobtomail.routes import auth as auth_module

    with app.app_context():
        token = auth_module.magic_link.generate_token("someone@example.com")

    res = client.get(f"/auth/magic/{token}")
    assert res.status_code in (302, 303)

    with client.session_transaction() as sess:
        assert sess["authenticated"] is True
        assert "user_id" in sess
        user_id = sess["user_id"]

    from jobtomail import db

    row = db.get_user_by_email("someone@example.com")
    assert row is not None
    assert row["id"] == user_id
