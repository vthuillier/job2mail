from __future__ import annotations

import json

from jobtomail import db


def test_schema_has_multi_tenant_columns(temp_db):
    from sqlalchemy import inspect
    from jobtomail.db import get_engine

    inspector = inspect(get_engine())
    user_cols = {c["name"] for c in inspector.get_columns("users")}
    assert {"id", "email", "google_sub", "created_at", "is_admin"} <= user_cols

    entreprise_cols = {c["name"] for c in inspector.get_columns("entreprises")}
    assert "user_id" in entreprise_cols

    job_cols = {c["name"] for c in inspector.get_columns("jobs")}
    assert "user_id" in job_cols

    reply_cols = {c["name"] for c in inspector.get_columns("processed_replies")}
    assert "user_id" in reply_cols


def test_insert_get_update_delete_entreprise(temp_db):
    db.insert_entreprise(
        {
            "siret": "33333333300001",
            "siren": "333333333",
            "denomination": "Test Corp",
            "adresse": "1 rue du Test",
            "commune": "Toulon",
        }
    )

    row = db.get_entreprise("33333333300001")
    assert row is not None
    assert row["denomination"] == "Test Corp"

    updated = db.update_entreprise("33333333300001", {"status": "postule"})
    assert updated is True
    assert db.get_entreprise("33333333300001")["status"] == "postule"

    db.delete_entreprise("33333333300001")
    assert db.get_entreprise("33333333300001") is None


def test_update_entreprise_unknown_siret_returns_false(temp_db):
    assert db.update_entreprise("00000000000000", {"status": "postule"}) is False


def test_config_get_set_roundtrip(temp_db):
    assert db.get_config_value("missing_key", "default") == "default"

    db.set_config_values({"candidate_name": "Jean Dupont", "rayon_km": "25"})

    assert db.get_config_value("candidate_name") == "Jean Dupont"
    cfg = db.get_all_config()
    assert cfg["candidate_name"] == "Jean Dupont"
    assert cfg["rayon_km"] == "25"


def test_env_or_config_prefers_env(temp_db, monkeypatch):
    db.set_config_values({"INSEE_TOKEN": "from-db"})
    monkeypatch.setenv("INSEE_TOKEN", "from-env")

    assert db.env_or_config("INSEE_TOKEN") == "from-env"


def test_env_or_config_falls_back_to_db(temp_db, monkeypatch):
    monkeypatch.delenv("INSEE_TOKEN", raising=False)
    db.set_config_values({"INSEE_TOKEN": "from-db"})

    assert db.env_or_config("INSEE_TOKEN") == "from-db"


def test_load_nafs_default_when_missing(temp_db):
    from jobtomail.constants import DEFAULT_NAF_CODES

    assert db.load_nafs({}) == dict(DEFAULT_NAF_CODES)


def test_load_nafs_valid_json(temp_db):
    custom = {"62.01Z": "Programmation informatique"}
    cfg = {"nafs": json.dumps(custom)}
    assert db.load_nafs(cfg) == custom


def test_load_nafs_invalid_json_falls_back(temp_db):
    from jobtomail.constants import DEFAULT_NAF_CODES

    cfg = {"nafs": "not json"}
    assert db.load_nafs(cfg) == dict(DEFAULT_NAF_CODES)
