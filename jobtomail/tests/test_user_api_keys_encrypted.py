from __future__ import annotations

from cryptography.fernet import Fernet
from sqlalchemy import and_, select

from jobtomail import db
from jobtomail.schema import user_config


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def _raw_user_config_value(user_id: int, key: str) -> str | None:
    with db.get_engine().connect() as conn:
        row = conn.execute(
            select(user_config.c.value).where(
                and_(user_config.c.user_id == user_id, user_config.c.key == key)
            )
        ).first()
    return row[0] if row else None


def test_post_config_stores_serpapi_key_encrypted_at_rest(client, monkeypatch):
    _set_key(monkeypatch)
    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    plaintext = "super-secret-serpapi-key"

    res = client.post("/api/config", json={"SERPAPI_KEY": plaintext})
    assert res.status_code == 200

    raw = _raw_user_config_value(user_id, "SERPAPI_KEY")
    assert raw is not None
    assert plaintext not in raw


def test_post_config_stores_hunter_key_encrypted_at_rest(client, monkeypatch):
    _set_key(monkeypatch)
    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    plaintext = "super-secret-hunter-token"

    res = client.post("/api/config", json={"TOKEN_HUNTER_IO": plaintext})
    assert res.status_code == 200

    raw = _raw_user_config_value(user_id, "TOKEN_HUNTER_IO")
    assert raw is not None
    assert plaintext not in raw


def test_get_config_returns_decrypted_serpapi_key(client, monkeypatch):
    _set_key(monkeypatch)
    plaintext = "super-secret-serpapi-key"
    client.post("/api/config", json={"SERPAPI_KEY": plaintext})

    res = client.get("/api/config")
    assert res.status_code == 200
    assert res.get_json()["SERPAPI_KEY"] == plaintext


def test_get_config_returns_decrypted_hunter_key(client, monkeypatch):
    _set_key(monkeypatch)
    plaintext = "super-secret-hunter-token"
    client.post("/api/config", json={"TOKEN_HUNTER_IO": plaintext})

    res = client.get("/api/config")
    assert res.status_code == 200
    assert res.get_json()["TOKEN_HUNTER_IO"] == plaintext


def test_api_keys_serpapi_key_for_decrypts_stored_value(client, monkeypatch):
    _set_key(monkeypatch)
    from jobtomail.services import api_keys

    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    plaintext = "super-secret-serpapi-key"

    client.post("/api/config", json={"SERPAPI_KEY": plaintext})

    assert api_keys.serpapi_key_for(user_id) == plaintext


def test_api_keys_hunter_key_for_decrypts_stored_value(client, monkeypatch):
    _set_key(monkeypatch)
    from jobtomail.services import api_keys

    with client.session_transaction() as sess:
        user_id = sess["user_id"]
    plaintext = "super-secret-hunter-token"

    client.post("/api/config", json={"TOKEN_HUNTER_IO": plaintext})

    assert api_keys.hunter_key_for(user_id) == plaintext


def test_api_keys_serpapi_key_for_none_when_absent(client, monkeypatch):
    _set_key(monkeypatch)
    from jobtomail.services import api_keys

    with client.session_transaction() as sess:
        user_id = sess["user_id"]

    assert api_keys.serpapi_key_for(user_id) is None


def test_api_keys_insee_token_never_reads_user_config(temp_db, monkeypatch):
    """INSEE_TOKEN doit rester une variable d'environnement globale : jamais
    lue depuis la config par utilisateur, même si un attaquant/utilisateur
    parvenait à écrire une valeur sous cette clé côté DB."""
    from jobtomail.services import api_keys

    monkeypatch.delenv("INSEE_TOKEN", raising=False)
    user_id = db.create_user("insee-test@example.com")
    db.set_user_config_values(user_id, {"INSEE_TOKEN": "should-never-be-used"})

    assert api_keys.insee_token() == ""

    monkeypatch.setenv("INSEE_TOKEN", "real-server-token")
    assert api_keys.insee_token() == "real-server-token"
