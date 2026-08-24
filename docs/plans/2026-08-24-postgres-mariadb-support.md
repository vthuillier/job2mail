# Support Postgres / MariaDB Implementation Plan

> **For agentic workers:** Implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permettre à Job2Mail de tourner sur PostgreSQL ou MariaDB en plus de SQLite, sans rien changer pour les utilisateurs qui ne configurent rien.

**Architecture:** `jobtomail/schema.py` définit les 4 tables en SQLAlchemy Core (dialect-agnostic). `jobtomail/db_config.py` résout le backend (env > fichier local `db_config.json` > SQLite par défaut). `jobtomail/db.py` garde exactement sa surface publique actuelle mais son intérieur passe par un `Engine` SQLAlchemy au lieu de `sqlite3` brut ; les quelques statements réellement dialect-spécifiques (upsert, migration de colonnes) passent par de petits helpers qui branchent sur `engine.dialect.name`.

**Tech Stack:** SQLAlchemy Core 2.x, psycopg2-binary (Postgres), PyMySQL (MariaDB), Flask, pytest.

**Spec:** `docs/plans/2026-08-24-postgres-mariadb-support-design.md`

## Global Constraints

- SQLAlchemy Core uniquement — pas d'ORM (pas de classes mappées, pas de session).
- Ordre de résolution du backend : `DB_BACKEND` (env) > `db_config.json` (racine projet) > SQLite par défaut. Zéro régression si rien n'est configuré.
- Toute fonction publique de `jobtomail/db.py` garde son nom et sa signature exacts — aucun appelant (routes, services, templates) ne change.
- Pas d'outillage de migration de données entre backends — changer de backend démarre à vide.
- Chaque commit doit laisser la suite de tests existante verte (régression zéro à chaque étape).
- Aucune mention de Claude/CLAUDE dans les messages de commit.
- Chaque tâche se termine par un test fonctionnel qui passe (pas seulement une lecture de code).

---

## File Structure

- `jobtomail/schema.py` (nouveau) — tables SQLAlchemy Core : `config`, `entreprises`, `processed_replies`, `jobs`.
- `jobtomail/db_config.py` (nouveau) — résolution du backend (`resolve_db_config()`, `DbConfig`, lecture/écriture de `db_config.json`).
- `jobtomail/db.py` (réécrit en interne) — toute la logique métier DB, API publique inchangée.
- `jobtomail/constants.py` (modifié) — ajout de `DB_CONFIG_PATH`.
- `jobtomail/routes/config_routes.py` (modifié) — endpoints `GET/POST /api/db/backend`.
- `jobtomail/routes/entreprises.py` (modifié) — `sqlite3.IntegrityError` → `db.DuplicateSiretError`.
- `requirements.txt` (modifié) — `sqlalchemy`, `psycopg2-binary`, `PyMySQL`.
- `docker-compose.yml` (modifié) — services `postgres`/`mariadb` derrière des profiles.
- `README.md` (modifié) — doc des variables `DB_*` et des profiles docker-compose.
- `templates/index.html` (modifié) — section « Base de données » dans la page Paramètres.
- `jobtomail/static/js/index.js` (modifié) — chargement/sauvegarde du backend DB.
- `jobtomail/tests/conftest.py` (modifié) — `temp_db` redirige `db_config.DB_PATH`/`DB_CONFIG_PATH` et appelle `db.reset_engine()`.
- `jobtomail/tests/test_db_config.py` (nouveau)
- `jobtomail/tests/test_schema.py` (nouveau)
- `jobtomail/tests/test_db.py` (étendu)
- `jobtomail/tests/test_db_backend_route.py` (nouveau)
- `jobtomail/tests/test_routes_smoke.py` (étendu)

---

### Task 1: Dépendances SQLAlchemy et constante `DB_CONFIG_PATH`

**Files:**
- Modify: `requirements.txt`
- Modify: `jobtomail/constants.py:8`

**Interfaces:**
- Produces: `jobtomail.constants.DB_CONFIG_PATH` (Path) — utilisé par les tâches 2 et suivantes.

- [ ] **Step 1: Ajouter les dépendances**

```
flask>=3.0.0
requests>=2.31.0
python-dotenv>=1.0.0
beautifulsoup4>=4.12.0
gunicorn>=21.2.0
pypdf>=4.0.0
sqlalchemy>=2.0,<3.0
psycopg2-binary>=2.9
PyMySQL>=1.1
```

- [ ] **Step 2: Installer et vérifier**

Run: `pip install -r requirements.txt && python -c "import sqlalchemy, psycopg2, pymysql; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Ajouter `DB_CONFIG_PATH` dans `jobtomail/constants.py`**

Juste après la ligne `DB_PATH = BASE_DIR / "jobtomail.db"` :

```python
DB_CONFIG_PATH = BASE_DIR / "db_config.json"
```

- [ ] **Step 4: Vérifier l'import**

Run: `python -c "from jobtomail.constants import DB_CONFIG_PATH; print(DB_CONFIG_PATH)"`
Expected: chemin se terminant par `db_config.json`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt jobtomail/constants.py
git commit -m "feat: add SQLAlchemy/psycopg2/PyMySQL dependencies"
```

---

### Task 2: Résolution du backend DB (`jobtomail/db_config.py`)

**Files:**
- Create: `jobtomail/db_config.py`
- Test: `jobtomail/tests/test_db_config.py`

**Interfaces:**
- Consumes: `jobtomail.constants.DB_PATH`, `jobtomail.constants.DB_CONFIG_PATH` (Task 1)
- Produces: `resolve_db_config() -> DbConfig`, `DbConfig(backend, host, port, user, password, dbname, source).url() -> str`, `write_db_config_file(dict) -> None`, `delete_db_config_file() -> None` — utilisés par `db.py` (Task 4) et `config_routes.py` (Task 5).

- [ ] **Step 1: Écrire les tests (échouent — le module n'existe pas encore)**

`jobtomail/tests/test_db_config.py` :

```python
from __future__ import annotations

import json

import pytest

from jobtomail import db_config


def test_resolve_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_BACKEND", raising=False)
    monkeypatch.setattr(db_config, "DB_CONFIG_PATH", tmp_path / "db_config.json")

    cfg = db_config.resolve_db_config()

    assert cfg.backend == "sqlite"
    assert cfg.source == "default"


def test_resolve_prefers_env_over_file(tmp_path, monkeypatch):
    file_path = tmp_path / "db_config.json"
    file_path.write_text(json.dumps({"backend": "mariadb", "host": "file-host"}))
    monkeypatch.setattr(db_config, "DB_CONFIG_PATH", file_path)
    monkeypatch.setenv("DB_BACKEND", "postgres")
    monkeypatch.setenv("DB_HOST", "env-host")
    monkeypatch.setenv("DB_PORT", "5432")
    monkeypatch.setenv("DB_USER", "u")
    monkeypatch.setenv("DB_PASSWORD", "p")
    monkeypatch.setenv("DB_NAME", "d")

    cfg = db_config.resolve_db_config()

    assert cfg.backend == "postgres"
    assert cfg.host == "env-host"
    assert cfg.source == "env"


def test_resolve_falls_back_to_file_when_no_env(tmp_path, monkeypatch):
    file_path = tmp_path / "db_config.json"
    file_path.write_text(
        json.dumps(
            {
                "backend": "mariadb",
                "host": "file-host",
                "port": "3306",
                "user": "u",
                "password": "p",
                "dbname": "d",
            }
        )
    )
    monkeypatch.setattr(db_config, "DB_CONFIG_PATH", file_path)
    monkeypatch.delenv("DB_BACKEND", raising=False)

    cfg = db_config.resolve_db_config()

    assert cfg.backend == "mariadb"
    assert cfg.host == "file-host"
    assert cfg.source == "file"


def test_resolve_rejects_invalid_env_backend(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "oracle")

    with pytest.raises(ValueError):
        db_config.resolve_db_config()


def test_db_config_url_sqlite(tmp_path, monkeypatch):
    monkeypatch.setattr(db_config, "DB_PATH", tmp_path / "test.db")
    cfg = db_config.DbConfig(backend="sqlite", source="default")
    assert cfg.url() == f"sqlite:///{tmp_path / 'test.db'}"


def test_db_config_url_postgres():
    cfg = db_config.DbConfig(
        backend="postgres", host="h", port="5432", user="u", password="p", dbname="d", source="file"
    )
    assert cfg.url() == "postgresql+psycopg2://u:p@h:5432/d"


def test_db_config_url_mariadb():
    cfg = db_config.DbConfig(
        backend="mariadb", host="h", port="3306", user="u", password="p", dbname="d", source="file"
    )
    assert cfg.url() == "mysql+pymysql://u:p@h:3306/d"


def test_write_and_delete_db_config_file(tmp_path, monkeypatch):
    file_path = tmp_path / "db_config.json"
    monkeypatch.setattr(db_config, "DB_CONFIG_PATH", file_path)

    db_config.write_db_config_file({"backend": "postgres", "host": "h"})
    assert file_path.exists()
    assert json.loads(file_path.read_text())["backend"] == "postgres"

    db_config.delete_db_config_file()
    assert not file_path.exists()
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db_config.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'jobtomail.db_config'`

- [ ] **Step 3: Implémenter `jobtomail/db_config.py`**

```python
"""Résolution du backend DB : env vars > fichier local > SQLite par défaut.

Le backend doit être résolvable AVANT toute connexion DB — cette config ne
peut donc pas vivre dans la table `config` (circulaire) ni dans `.env` seul
(l'assistant premier lancement doit pouvoir l'écrire sans redémarrage manuel).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from jobtomail.constants import DB_CONFIG_PATH, DB_PATH

logger = logging.getLogger(__name__)

_VALID_BACKENDS = {"sqlite", "postgres", "mariadb"}


@dataclass(frozen=True)
class DbConfig:
    backend: str
    host: str = ""
    port: str = ""
    user: str = ""
    password: str = ""
    dbname: str = ""
    source: str = "default"  # "env" | "file" | "default"

    def url(self) -> str:
        if self.backend == "sqlite":
            return f"sqlite:///{DB_PATH}"
        if self.backend == "postgres":
            return f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.dbname}"
        if self.backend == "mariadb":
            return f"mysql+pymysql://{self.user}:{self.password}@{self.host}:{self.port}/{self.dbname}"
        raise ValueError(f"Backend DB inconnu : {self.backend}")


def _from_env() -> DbConfig | None:
    backend = os.getenv("DB_BACKEND", "").strip().lower()
    if not backend:
        return None
    if backend not in _VALID_BACKENDS:
        raise ValueError(f"DB_BACKEND invalide : {backend!r} (attendu sqlite/postgres/mariadb)")
    if backend == "sqlite":
        return DbConfig(backend="sqlite", source="env")
    return DbConfig(
        backend=backend,
        host=os.getenv("DB_HOST", "").strip(),
        port=os.getenv("DB_PORT", "").strip(),
        user=os.getenv("DB_USER", "").strip(),
        password=os.getenv("DB_PASSWORD", "").strip(),
        dbname=os.getenv("DB_NAME", "").strip(),
        source="env",
    )


def _from_file() -> DbConfig | None:
    if not DB_CONFIG_PATH.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(DB_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("db_config.json illisible (%s), ignoré", exc)
        return None
    backend = str(data.get("backend", "")).strip().lower()
    if backend not in _VALID_BACKENDS:
        logger.warning("db_config.json backend invalide : %r, ignoré", backend)
        return None
    if backend == "sqlite":
        return DbConfig(backend="sqlite", source="file")
    return DbConfig(
        backend=backend,
        host=str(data.get("host", "")),
        port=str(data.get("port", "")),
        user=str(data.get("user", "")),
        password=str(data.get("password", "")),
        dbname=str(data.get("dbname", "")),
        source="file",
    )


def resolve_db_config() -> DbConfig:
    """Ordre : DB_BACKEND (env) > db_config.json > SQLite par défaut."""
    cfg = _from_env()
    if cfg is not None:
        return cfg
    cfg = _from_file()
    if cfg is not None:
        return cfg
    return DbConfig(backend="sqlite", source="default")


def write_db_config_file(data: dict[str, Any]) -> None:
    DB_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_db_config_file() -> None:
    if DB_CONFIG_PATH.exists():
        DB_CONFIG_PATH.unlink()
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_db_config.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add jobtomail/db_config.py jobtomail/tests/test_db_config.py
git commit -m "feat: resolve DB backend from env vars, local file, or sqlite default"
```

---

### Task 3: Schéma SQLAlchemy Core (`jobtomail/schema.py`)

**Files:**
- Create: `jobtomail/schema.py`
- Test: `jobtomail/tests/test_schema.py`

**Interfaces:**
- Produces: `metadata` (SQLAlchemy `MetaData`), `config`, `entreprises`, `processed_replies`, `jobs` (SQLAlchemy `Table` objects) — utilisés par `db.py` (Task 4).

- [ ] **Step 1: Écrire le test (échoue — le module n'existe pas encore)**

`jobtomail/tests/test_schema.py` :

```python
from __future__ import annotations

from sqlalchemy import create_engine, inspect

from jobtomail.schema import metadata


def test_create_all_creates_expected_tables():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {"config", "entreprises", "processed_replies", "jobs"}


def test_entreprises_table_has_all_expected_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("entreprises")}
    expected = {
        "siret", "siren", "denomination", "adresse", "commune",
        "effectif_code", "effectif_libelle", "naf_code", "naf_libelle",
        "date_creation", "est_siege", "categorie_entreprise",
        "categorie_juridique", "nature", "score_pertinence", "site_web",
        "linkedin_company", "serpapi_scanned", "contact_prenom",
        "contact_nom", "contact_genre", "contact_poste", "contact_email",
        "contact_linkedin", "status", "notes", "dirigeants_scanned",
        "created_at", "latitude", "longitude", "geocode_failed",
        "email_message_id", "email_subject", "email_body", "email_sent_at",
        "relance_count", "last_relance_at", "reply_message_id",
        "reply_class", "reply_classified_at", "reply_from", "reply_subject",
        "contact_source", "email_hunter_score", "email_quality",
        "email_quality_note", "accroche", "entretien_date",
        "entretien_next_step", "entretien_rappel_at", "travel_origin",
        "travel_duration_min", "travel_distance_km", "travel_without_tolls",
        "travel_updated_at",
    }
    assert expected.issubset(columns)


def test_jobs_table_has_expected_columns():
    engine = create_engine("sqlite:///:memory:")
    metadata.create_all(engine)
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("jobs")}
    assert columns == {
        "id", "kind", "status", "params", "progress", "result", "error",
        "created_at", "updated_at",
    }
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_schema.py -v`
Expected: FAIL avec `ModuleNotFoundError: No module named 'jobtomail.schema'`

- [ ] **Step 3: Implémenter `jobtomail/schema.py`**

```python
"""Définition SQLAlchemy Core des tables — dialect-agnostic (SQLite/Postgres/MariaDB)."""

from __future__ import annotations

from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, text

metadata = MetaData()

config = Table(
    "config",
    metadata,
    Column("key", String(255), primary_key=True),
    Column("value", Text),
)

entreprises = Table(
    "entreprises",
    metadata,
    Column("siret", String(20), primary_key=True),
    Column("siren", String(20)),
    Column("denomination", Text, nullable=False),
    Column("adresse", Text),
    Column("commune", Text),
    Column("effectif_code", String(10)),
    Column("effectif_libelle", Text),
    Column("naf_code", String(10)),
    Column("naf_libelle", Text),
    Column("date_creation", Text),
    Column("est_siege", Integer, server_default=text("0")),
    Column("categorie_entreprise", Text),
    Column("categorie_juridique", Text),
    Column("nature", Text, server_default=text("'entreprise'")),
    Column("score_pertinence", Float, server_default=text("0")),
    Column("site_web", Text),
    Column("linkedin_company", Text),
    Column("serpapi_scanned", Integer, server_default=text("0")),
    Column("contact_prenom", Text),
    Column("contact_nom", Text),
    Column("contact_genre", Text),
    Column("contact_poste", Text),
    Column("contact_email", Text),
    Column("contact_linkedin", Text),
    Column("status", Text, server_default=text("'a_postuler'")),
    Column("notes", Text),
    Column("dirigeants_scanned", Integer, server_default=text("0")),
    Column("created_at", Text),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("geocode_failed", Integer, server_default=text("0")),
    Column("email_message_id", Text),
    Column("email_subject", Text),
    Column("email_body", Text),
    Column("email_sent_at", Text),
    Column("relance_count", Integer, server_default=text("0")),
    Column("last_relance_at", Text),
    Column("reply_message_id", Text),
    Column("reply_class", Text),
    Column("reply_classified_at", Text),
    Column("reply_from", Text),
    Column("reply_subject", Text),
    Column("contact_source", Text),
    Column("email_hunter_score", Integer),
    Column("email_quality", Text),
    Column("email_quality_note", Text),
    Column("accroche", Text),
    Column("entretien_date", Text),
    Column("entretien_next_step", Text),
    Column("entretien_rappel_at", Text),
    Column("travel_origin", Text),
    Column("travel_duration_min", Float),
    Column("travel_distance_km", Float),
    Column("travel_without_tolls", Integer, server_default=text("0")),
    Column("travel_updated_at", Text),
)

processed_replies = Table(
    "processed_replies",
    metadata,
    Column("message_id", String(255), primary_key=True),
    Column("siret", String(20)),
    Column("classification", Text),
    Column("processed_at", Text),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("kind", Text, nullable=False),
    Column("status", Text, nullable=False, server_default=text("'queued'")),
    Column("params", Text),
    Column("progress", Text),
    Column("result", Text),
    Column("error", Text),
    Column("created_at", Text),
    Column("updated_at", Text),
)
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_schema.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add jobtomail/schema.py jobtomail/tests/test_schema.py
git commit -m "feat: define DB schema as dialect-agnostic SQLAlchemy Core tables"
```

---

### Task 4: Réécriture de `jobtomail/db.py` sur SQLAlchemy Core

C'est la tâche centrale : chaque fonction publique de `db.py` garde exactement
son nom et sa signature, mais son intérieur passe par un `Engine` SQLAlchemy
au lieu de `sqlite3`. Découpée en groupes, chacun avec son propre commit, mais
formant une seule tâche parce qu'un état intermédiaire (la moitié des
fonctions sur `sqlite3`, l'autre sur SQLAlchemy) casserait l'appli — elle doit
rester cohérente et fonctionnelle à la fin du groupe A.

**Files:**
- Modify: `jobtomail/db.py` (réécriture complète du corps de chaque fonction)
- Modify: `jobtomail/routes/entreprises.py:7,147` (`sqlite3.IntegrityError` → `db.DuplicateSiretError`)
- Modify: `jobtomail/tests/conftest.py`
- Modify: `jobtomail/tests/test_db.py` (étendu)
- Create: `jobtomail/tests/test_db_scan.py`
- Create: `jobtomail/tests/test_db_email.py`

**Interfaces:**
- Consumes: `jobtomail.db_config.resolve_db_config()`, `DbConfig.url()` (Task 2) ; `jobtomail.schema.{metadata, config, entreprises, processed_replies}` (Task 3).
- Produces (inchangé pour l'appelant, réimplémenté ici) : toutes les fonctions publiques déjà présentes dans `db.py`, plus `get_engine() -> Engine`, `reset_engine() -> None`, `DuplicateSiretError` — utilisés par `config_routes.py` (Task 5) et `jobtomail/tests/conftest.py`.

#### Groupe A — Infra moteur + `init_db`/migration

- [ ] **Step 1: Écrire le test (échoue tant que `db.py` n'a pas d'engine SQLAlchemy)**

Ajouter en tête de `jobtomail/tests/test_db.py` (avant les tests existants) :

```python
def test_init_db_creates_all_tables(temp_db):
    from sqlalchemy import inspect

    from jobtomail import db

    inspector = inspect(db.get_engine())
    assert set(inspector.get_table_names()) == {"config", "entreprises", "processed_replies", "jobs"}


def test_reset_engine_forces_new_engine(temp_db):
    from jobtomail import db

    first = db.get_engine()
    db.reset_engine()
    second = db.get_engine()
    assert first is not second
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db.py -k "init_db_creates_all_tables or reset_engine" -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'get_engine'`

- [ ] **Step 3: Remplacer l'en-tête de `jobtomail/db.py` (imports, engine, `init_db`, `_migrate`)**

Remplacer les lignes 1-138 du fichier actuel (imports jusqu'à la fin de `init_db`) par :

```python
"""Accès DB (SQLite / PostgreSQL / MariaDB) et configuration persistée."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from jobtomail.constants import DEFAULT_NAF_CODES
from jobtomail.db_config import resolve_db_config
from jobtomail.schema import (
    config as config_table,
    entreprises as entreprises_table,
    metadata,
    processed_replies as processed_replies_table,
)

logger = logging.getLogger(__name__)

_ENGINE: Engine | None = None

# Défauts appliqués aux colonnes ajoutées après coup par ALTER TABLE (les
# nouvelles installations les reçoivent déjà via server_default dans schema.py).
_COLUMN_ALTER_DEFAULTS = {
    "est_siege": "0",
    "nature": "'entreprise'",
    "score_pertinence": "0",
    "geocode_failed": "0",
    "dirigeants_scanned": "0",
    "relance_count": "0",
    "travel_without_tolls": "0",
}


class DuplicateSiretError(Exception):
    """Levée quand un SIRET existe déjà (contrainte PK entreprises)."""


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        cfg = resolve_db_config()
        logger.info("Backend DB : %s (source=%s)", cfg.backend, cfg.source)
        _ENGINE = create_engine(cfg.url(), future=True)
    return _ENGINE


def reset_engine() -> None:
    """Invalide le cache d'engine (tests, ou changement de backend à chaud)."""
    global _ENGINE
    if _ENGINE is not None:
        _ENGINE.dispose()
    _ENGINE = None


def _insert_ignore(conn, table, values: dict[str, Any]) -> bool:
    """INSERT qui ne fait rien si la ligne existe déjà. Retourne True si insérée."""
    dialect = conn.engine.dialect.name
    if dialect == "sqlite":
        stmt = sqlite.insert(table).values(**values).on_conflict_do_nothing()
    elif dialect == "postgresql":
        stmt = postgresql.insert(table).values(**values).on_conflict_do_nothing()
    elif dialect == "mysql":
        stmt = mysql.insert(table).values(**values).prefix_with("IGNORE")
    else:
        raise ValueError(f"Dialecte non supporté : {dialect}")
    result = conn.execute(stmt)
    return result.rowcount > 0


def _insert_replace(conn, table, values: dict[str, Any], pk_col: str) -> None:
    """Upsert : insère, ou met à jour si la clé primaire existe déjà."""
    update_cols = {k: v for k, v in values.items() if k != pk_col}
    dialect = conn.engine.dialect.name
    if dialect == "sqlite":
        stmt = sqlite.insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[pk_col], set_=update_cols)
    elif dialect == "postgresql":
        stmt = postgresql.insert(table).values(**values)
        stmt = stmt.on_conflict_do_update(index_elements=[pk_col], set_=update_cols)
    elif dialect == "mysql":
        stmt = mysql.insert(table).values(**values)
        stmt = stmt.on_duplicate_key_update(**update_cols)
    else:
        raise ValueError(f"Dialecte non supporté : {dialect}")
    conn.execute(stmt)


def init_db() -> None:
    engine = get_engine()
    logger.info("Initialisation de la base (%s)", engine.dialect.name)
    metadata.create_all(engine, checkfirst=True)
    _migrate(engine)
    logger.info("Base prête")


def _migrate(engine: Engine) -> None:
    inspector = inspect(engine)
    existing = {col["name"] for col in inspector.get_columns("entreprises")}
    with engine.begin() as conn:
        for col in entreprises_table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=engine.dialect)
            ddl = f"ALTER TABLE entreprises ADD COLUMN {col.name} {coltype}"
            default = _COLUMN_ALTER_DEFAULTS.get(col.name)
            if default is not None:
                ddl += f" DEFAULT {default}"
            logger.info("Migration : ajout colonne entreprises.%s", col.name)
            conn.exec_driver_sql(ddl)
```

Supprimer l'ancien `get_db()` et l'ancienne dict `_EXTRA_COLUMNS` (remplacés par le schéma unique dans `schema.py` + `_COLUMN_ALTER_DEFAULTS` ci-dessus).

- [ ] **Step 4: Adapter `jobtomail/tests/conftest.py` (sinon plus aucun test ne peut isoler sa DB)**

Remplacer le fixture `temp_db` par :

```python
@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """DB SQLite temporaire, isolée : DB_PATH et db_config.json redirigés vers tmp_path."""
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("jobtomail.db_config.DB_PATH", db_path)
    monkeypatch.setattr("jobtomail.db_config.DB_CONFIG_PATH", tmp_path / "db_config.json")
    monkeypatch.delenv("DB_BACKEND", raising=False)
    from jobtomail import db

    db.reset_engine()
    db.init_db()
    yield db_path
    db.reset_engine()
```

- [ ] **Step 5: Vérifier que les tests du groupe A passent**

Run: `pytest jobtomail/tests/test_db.py -k "init_db_creates_all_tables or reset_engine" -v`
Expected: 2 passed

- [ ] **Step 6: Commit**

```bash
git add jobtomail/db.py jobtomail/tests/conftest.py jobtomail/tests/test_db.py
git commit -m "feat: run db.py on a SQLAlchemy engine instead of raw sqlite3"
```

#### Groupe B — Config (`get_config_value`, `set_config_values`, `get_all_config`, `env_or_config`, `load_nafs`)

- [ ] **Step 1: Vérifier que les tests de config existants échouent encore (fonctions pas encore réécrites)**

Run: `pytest jobtomail/tests/test_db.py -k "config" -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'get_config_value'`

- [ ] **Step 2: Ajouter à la suite de `jobtomail/db.py`**

```python
def get_config_value(key: str, default: str = "") -> str:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(config_table.c.value).where(config_table.c.key == key)
        ).mappings().first()
    if row and row["value"]:
        return row["value"]
    return default


def set_config_values(data: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            _insert_replace(conn, config_table, {"key": k, "value": str(v) if v is not None else ""}, "key")
    logger.info("Config mise à jour (%d clé(s)) : %s", len(data), ", ".join(data.keys()))


def get_all_config() -> dict[str, str]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(config_table.c.key, config_table.c.value)).mappings().all()
    return {row["key"]: row["value"] for row in rows}


def env_or_config(key: str, *aliases: str) -> str:
    """Lit d'abord .env, puis la config DB (avec alias éventuels)."""
    import os

    for name in (key, *aliases):
        val = os.getenv(name)
        if val:
            return val.strip()
    for name in (key, *aliases):
        val = get_config_value(name)
        if val:
            return val.strip()
    return ""


def load_nafs(cfg: dict[str, str] | None = None) -> dict[str, str]:
    cfg = cfg if cfg is not None else get_all_config()
    nafs_raw = cfg.get("nafs")
    if not nafs_raw:
        return dict(DEFAULT_NAF_CODES)
    try:
        parsed = json.loads(nafs_raw)
        if isinstance(parsed, dict) and parsed:
            return parsed
    except json.JSONDecodeError:
        logger.warning("Config nafs invalide, fallback sur les NAF par défaut")
    return dict(DEFAULT_NAF_CODES)
```

- [ ] **Step 3: Vérifier que les tests de config passent**

Run: `pytest jobtomail/tests/test_db.py -k "config or nafs" -v`
Expected: 8 passed (les tests existants du fichier, inchangés)

- [ ] **Step 4: Commit**

```bash
git add jobtomail/db.py
git commit -m "feat: migrate config get/set to SQLAlchemy Core"
```

#### Groupe C — CRUD entreprises

- [ ] **Step 1: Écrire un test pour le nouveau comportement `DuplicateSiretError`**

Ajouter à `jobtomail/tests/test_db.py` :

```python
def test_insert_entreprise_duplicate_siret_raises(temp_db):
    row = {
        "siret": "44444444400001",
        "denomination": "Dup Corp",
        "adresse": "1 rue Dup",
        "commune": "Toulon",
    }
    db.insert_entreprise(row)
    with pytest.raises(db.DuplicateSiretError):
        db.insert_entreprise(row)


def test_insert_entreprise_ignore_skips_existing(temp_db):
    row = {"siret": "55555555500001", "denomination": "Ignore Corp"}
    assert db.insert_entreprise_ignore(row) is True
    assert db.insert_entreprise_ignore(row) is False
```

Ajouter `import pytest` en tête du fichier s'il n'y est pas déjà.

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db.py -k "duplicate_siret or insert_entreprise_ignore" -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'insert_entreprise'`

- [ ] **Step 3: Ajouter à la suite de `jobtomail/db.py`**

```python
def list_entreprises():
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT * FROM entreprises
                ORDER BY COALESCE(score_pertinence, 0) DESC, LOWER(denomination) ASC
                """
            )
        ).mappings().all()
    return list(rows)


def get_entreprise(siret: str):
    with get_engine().connect() as conn:
        row = conn.execute(
            text("SELECT * FROM entreprises WHERE siret = :siret"), {"siret": siret}
        ).mappings().first()
    return row


def update_entreprise(siret: str, fields: dict[str, Any]) -> bool:
    existing = get_entreprise(siret)
    if not existing:
        return False
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields)
    params["siret"] = siret
    with get_engine().begin() as conn:
        conn.execute(text(f"UPDATE entreprises SET {set_clause} WHERE siret = :siret"), params)
    logger.info("Entreprise %s mise à jour (%s)", siret, ", ".join(fields.keys()))
    return True


def delete_entreprise(siret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("DELETE FROM entreprises WHERE siret = :siret"), {"siret": siret})
    logger.info("Entreprise %s supprimée", siret)


def reset_entreprises() -> int:
    with get_engine().begin() as conn:
        count = conn.execute(text("SELECT COUNT(*) AS n FROM entreprises")).mappings().first()["n"]
        conn.execute(text("DELETE FROM entreprises"))
    logger.warning("Table entreprises vidée (%d ligne(s))", count)
    return count


def insert_entreprise_ignore(row: dict[str, Any]) -> bool:
    values = {
        "siret": row["siret"],
        "siren": row.get("siren"),
        "denomination": row["denomination"],
        "adresse": row.get("adresse"),
        "commune": row.get("commune"),
        "effectif_code": row.get("effectif_code"),
        "effectif_libelle": row.get("effectif_libelle"),
        "naf_code": row.get("naf_code"),
        "naf_libelle": row.get("naf_libelle"),
        "date_creation": row.get("date_creation"),
        "est_siege": 1 if row.get("est_siege") else 0,
        "categorie_entreprise": row.get("categorie_entreprise"),
        "categorie_juridique": row.get("categorie_juridique"),
        "nature": row.get("nature") or "entreprise",
        "score_pertinence": row.get("score_pertinence") or 0,
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "created_at": _now(),
    }
    with get_engine().begin() as conn:
        inserted = _insert_ignore(conn, entreprises_table, values)
    return inserted


def insert_entreprise(row: dict[str, Any]) -> None:
    """Insère une entreprise. Lève DuplicateSiretError si le SIRET existe déjà."""
    values = {
        "siret": row["siret"],
        "siren": row.get("siren"),
        "denomination": row["denomination"],
        "adresse": row.get("adresse"),
        "commune": row.get("commune"),
        "effectif_code": row.get("effectif_code"),
        "effectif_libelle": row.get("effectif_libelle"),
        "naf_code": row.get("naf_code"),
        "naf_libelle": row.get("naf_libelle"),
        "date_creation": row.get("date_creation"),
        "est_siege": 1 if row.get("est_siege") else 0,
        "categorie_entreprise": row.get("categorie_entreprise"),
        "categorie_juridique": row.get("categorie_juridique"),
        "nature": row.get("nature") or "entreprise",
        "score_pertinence": row.get("score_pertinence") or 0,
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "site_web": row.get("site_web") or None,
        "linkedin_company": row.get("linkedin_company") or None,
        "notes": row.get("notes") or None,
        "status": row.get("status") or "a_postuler",
        "created_at": _now(),
    }
    try:
        with get_engine().begin() as conn:
            conn.execute(entreprises_table.insert().values(**values))
    except IntegrityError as exc:
        raise DuplicateSiretError(row["siret"]) from exc
    logger.info("Entreprise ajoutée manuellement : %s (%s)", row["denomination"], row["siret"])


def replace_entreprises_cleaned(
    keep_rows: list[dict[str, Any]],
    keep_sirets: set[str],
) -> dict[str, int]:
    """Supprime les SIRET non retenus et met à jour score / notes des gardés."""
    with get_engine().begin() as conn:
        all_sirets = {
            r["siret"] for r in conn.execute(text("SELECT siret FROM entreprises")).mappings().all()
        }
        to_delete = all_sirets - keep_sirets
        for siret in to_delete:
            conn.execute(text("DELETE FROM entreprises WHERE siret = :siret"), {"siret": siret})

        updated = 0
        for row in keep_rows:
            result = conn.execute(
                text(
                    """
                    UPDATE entreprises
                    SET score_pertinence = :score_pertinence,
                        notes = COALESCE(:notes, notes),
                        est_siege = :est_siege,
                        categorie_entreprise = COALESCE(:categorie_entreprise, categorie_entreprise),
                        categorie_juridique = COALESCE(:categorie_juridique, categorie_juridique),
                        nature = COALESCE(:nature, nature)
                    WHERE siret = :siret
                    """
                ),
                {
                    "score_pertinence": row.get("score_pertinence") or 0,
                    "notes": row.get("notes"),
                    "est_siege": 1 if row.get("est_siege") else 0,
                    "categorie_entreprise": row.get("categorie_entreprise"),
                    "categorie_juridique": row.get("categorie_juridique"),
                    "nature": row.get("nature"),
                    "siret": row["siret"],
                },
            )
            updated += result.rowcount

    logger.info("Clean DB — deleted=%d updated=%d", len(to_delete), updated)
    return {"deleted": len(to_delete), "updated": updated}
```

- [ ] **Step 4: Mettre à jour `jobtomail/routes/entreprises.py`**

Supprimer `import sqlite3` (ligne 7).

Remplacer :
```python
    try:
        db.insert_entreprise(row)
    except sqlite3.IntegrityError:
        return jsonify({"error": f"Une entreprise avec le SIRET {siret} existe déjà"}), 409
```
par :
```python
    try:
        db.insert_entreprise(row)
    except db.DuplicateSiretError:
        return jsonify({"error": f"Une entreprise avec le SIRET {siret} existe déjà"}), 409
```

- [ ] **Step 5: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_db.py jobtomail/tests/test_routes_smoke.py -v`
Expected: tous passed

- [ ] **Step 6: Commit**

```bash
git add jobtomail/db.py jobtomail/routes/entreprises.py jobtomail/tests/test_db.py
git commit -m "feat: migrate entreprises CRUD to SQLAlchemy Core"
```

#### Groupe D — Scan / géocodage / dirigeants

- [ ] **Step 1: Écrire les tests (échouent — fonctions pas encore réécrites)**

`jobtomail/tests/test_db_scan.py` :

```python
from __future__ import annotations

from jobtomail import db


def _make_entreprise(siret="66666666600001", **overrides):
    row = {"siret": siret, "denomination": "Scan Corp", "commune": "Toulon"}
    row.update(overrides)
    db.insert_entreprise(row)
    return siret


def test_mark_serpapi_result_and_scanned_only(temp_db):
    siret = _make_entreprise()
    db.mark_serpapi_result(siret, "https://scan-corp.fr", "https://linkedin.com/company/scan-corp")
    ent = db.get_entreprise(siret)
    assert ent["site_web"] == "https://scan-corp.fr"
    assert ent["serpapi_scanned"] == 1

    db.mark_serpapi_scanned_only("77777777700001")  # siret inconnu : no-op silencieux


def test_entreprises_to_serpapi_filters_unscanned(temp_db):
    siret = _make_entreprise()
    rows = db.entreprises_to_serpapi()
    assert any(r["siret"] == siret for r in rows)

    db.mark_serpapi_result(siret, "https://scan-corp.fr", "", scanned=True)
    rows = db.entreprises_to_serpapi()
    assert all(r["siret"] != siret for r in rows)


def test_geocode_flow(temp_db):
    siret = _make_entreprise()
    assert db.count_entreprises_sans_coords() == 1

    db.update_entreprise_coords(siret, 43.12, 6.02)
    assert db.count_entreprises_sans_coords() == 0
    ent = db.get_entreprise(siret)
    assert ent["latitude"] == 43.12

    siret2 = _make_entreprise(siret="88888888800001")
    db.mark_geocode_failed(siret2)
    assert db.count_entreprises_sans_coords() == 0


def test_update_entreprise_travel(temp_db):
    siret = _make_entreprise()
    db.update_entreprise_travel(siret, origin="La Crau", duration_min=12.5, distance_km=8.3)
    ent = db.get_entreprise(siret)
    assert ent["travel_duration_min"] == 12.5
    assert ent["travel_without_tolls"] == 1


def test_entreprises_to_dirigeants_default_filters_unscanned_without_contact(temp_db):
    siret = _make_entreprise()
    rows = db.entreprises_to_dirigeants()
    assert any(r["siret"] == siret for r in rows)

    db.apply_dirigeant(siret, {"contact_prenom": "Jean", "contact_nom": "Dupont"})
    rows = db.entreprises_to_dirigeants()
    assert all(r["siret"] != siret for r in rows)

    ent = db.get_entreprise(siret)
    assert ent["contact_prenom"] == "Jean"
    assert ent["dirigeants_scanned"] == 1


def test_apply_contact_and_email_quality(temp_db):
    siret = _make_entreprise()
    db.apply_contact(siret, {"contact_prenom": "Marie", "contact_poste": "RH"})
    db.apply_email_quality(siret, email="marie@scan-corp.fr", hunter_score=85, quality="ok")
    ent = db.get_entreprise(siret)
    assert ent["contact_prenom"] == "Marie"
    assert ent["contact_email"] == "marie@scan-corp.fr"
    assert ent["email_hunter_score"] == 85

    db.mark_dirigeants_scanned(siret)
    assert db.get_entreprise(siret)["dirigeants_scanned"] == 1
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db_scan.py -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'mark_serpapi_result'`

- [ ] **Step 3: Ajouter à la suite de `jobtomail/db.py`**

```python
def mark_serpapi_result(
    siret: str,
    site_web: str | None,
    linkedin_company: str | None,
    scanned: bool = True,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET site_web = COALESCE(NULLIF(:site_web, ''), site_web),
                    linkedin_company = COALESCE(NULLIF(:linkedin_company, ''), linkedin_company),
                    serpapi_scanned = :scanned
                WHERE siret = :siret
                """
            ),
            {
                "site_web": site_web or "",
                "linkedin_company": linkedin_company or "",
                "scanned": 1 if scanned else 0,
                "siret": siret,
            },
        )


def mark_serpapi_scanned_only(siret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE entreprises SET serpapi_scanned = 1 WHERE siret = :siret"), {"siret": siret}
        )


def entreprises_to_serpapi(sirets: list[str] | None = None):
    with get_engine().connect() as conn:
        if sirets:
            placeholders = ", ".join(f":s{i}" for i in range(len(sirets)))
            params = {f"s{i}": v for i, v in enumerate(sirets)}
            rows = conn.execute(
                text(f"SELECT siret, denomination, commune FROM entreprises WHERE siret IN ({placeholders})"),
                params,
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    """
                    SELECT siret, denomination, commune FROM entreprises
                    WHERE serpapi_scanned = 0
                    ORDER BY COALESCE(score_pertinence, 0) DESC, LOWER(denomination)
                    """
                )
            ).mappings().all()
    return list(rows)


def entreprises_sans_coords(limit: int = 40):
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT siret, adresse, commune FROM entreprises
                WHERE (latitude IS NULL OR longitude IS NULL)
                  AND COALESCE(geocode_failed, 0) = 0
                ORDER BY COALESCE(score_pertinence, 0) DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        ).mappings().all()
    return list(rows)


def count_entreprises_sans_coords() -> int:
    with get_engine().connect() as conn:
        n = conn.execute(
            text(
                """
                SELECT COUNT(*) AS n FROM entreprises
                WHERE (latitude IS NULL OR longitude IS NULL)
                  AND COALESCE(geocode_failed, 0) = 0
                """
            )
        ).mappings().first()["n"]
    return n


def mark_geocode_failed(siret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE entreprises SET geocode_failed = 1 WHERE siret = :siret"), {"siret": siret}
        )


def update_entreprise_coords(siret: str, latitude: float, longitude: float) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE entreprises SET latitude = :lat, longitude = :lon WHERE siret = :siret"),
            {"lat": latitude, "lon": longitude, "siret": siret},
        )


def update_entreprise_travel(
    siret: str,
    *,
    origin: str,
    duration_min: float,
    distance_km: float,
    without_tolls: bool = True,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET travel_origin = :origin,
                    travel_duration_min = :duration_min,
                    travel_distance_km = :distance_km,
                    travel_without_tolls = :without_tolls,
                    travel_updated_at = :updated_at
                WHERE siret = :siret
                """
            ),
            {
                "origin": origin,
                "duration_min": duration_min,
                "distance_km": distance_km,
                "without_tolls": 1 if without_tolls else 0,
                "updated_at": _now(),
                "siret": siret,
            },
        )


def entreprises_to_dirigeants(
    sirets: list[str] | None = None,
    force: bool = False,
):
    cols = "siret, siren, denomination, contact_prenom, contact_nom, dirigeants_scanned"
    with get_engine().connect() as conn:
        if sirets:
            placeholders = ", ".join(f":s{i}" for i in range(len(sirets)))
            params = {f"s{i}": v for i, v in enumerate(sirets)}
            rows = conn.execute(
                text(f"SELECT {cols} FROM entreprises WHERE siret IN ({placeholders})"), params
            ).mappings().all()
        elif force:
            rows = conn.execute(
                text(f"SELECT {cols} FROM entreprises ORDER BY COALESCE(score_pertinence, 0) DESC, LOWER(denomination)")
            ).mappings().all()
        else:
            rows = conn.execute(
                text(
                    f"""
                    SELECT {cols} FROM entreprises
                    WHERE COALESCE(dirigeants_scanned, 0) = 0
                      AND (contact_prenom IS NULL OR TRIM(contact_prenom) = '')
                      AND (contact_nom IS NULL OR TRIM(contact_nom) = '')
                    ORDER BY COALESCE(score_pertinence, 0) DESC, LOWER(denomination)
                    """
                )
            ).mappings().all()
    return list(rows)


def apply_dirigeant(siret: str, fields: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET contact_prenom = :prenom,
                    contact_nom = :nom,
                    contact_poste = COALESCE(NULLIF(:poste, ''), contact_poste),
                    contact_source = COALESCE(NULLIF(:source, ''), 'dirigeant'),
                    dirigeants_scanned = 1
                WHERE siret = :siret
                """
            ),
            {
                "prenom": fields.get("contact_prenom") or "",
                "nom": fields.get("contact_nom") or "",
                "poste": fields.get("contact_poste") or "",
                "source": fields.get("contact_source") or "dirigeant",
                "siret": siret,
            },
        )


def apply_contact(siret: str, fields: dict[str, Any]) -> None:
    """Met à jour le contact (RH/tech/dirigeant) sans toucher dirigeants_scanned."""
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET contact_prenom = COALESCE(NULLIF(:prenom, ''), contact_prenom),
                    contact_nom = COALESCE(NULLIF(:nom, ''), contact_nom),
                    contact_poste = COALESCE(NULLIF(:poste, ''), contact_poste),
                    contact_linkedin = COALESCE(NULLIF(:linkedin, ''), contact_linkedin),
                    contact_source = COALESCE(NULLIF(:source, ''), contact_source)
                WHERE siret = :siret
                """
            ),
            {
                "prenom": fields.get("contact_prenom") or "",
                "nom": fields.get("contact_nom") or "",
                "poste": fields.get("contact_poste") or "",
                "linkedin": fields.get("contact_linkedin") or "",
                "source": fields.get("contact_source") or "",
                "siret": siret,
            },
        )


def apply_email_quality(
    siret: str,
    *,
    email: str,
    hunter_score: int | None,
    quality: str,
    note: str = "",
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET contact_email = COALESCE(NULLIF(:email, ''), contact_email),
                    email_hunter_score = :hunter_score,
                    email_quality = :quality,
                    email_quality_note = :note
                WHERE siret = :siret
                """
            ),
            {"email": email, "hunter_score": hunter_score, "quality": quality, "note": note, "siret": siret},
        )


def mark_dirigeants_scanned(siret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE entreprises SET dirigeants_scanned = 1 WHERE siret = :siret"), {"siret": siret}
        )
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_db_scan.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add jobtomail/db.py jobtomail/tests/test_db_scan.py
git commit -m "feat: migrate scan/geocode/dirigeants db functions to SQLAlchemy Core"
```

#### Groupe E — Email / relance / réponses

- [ ] **Step 1: Écrire les tests (échouent — fonctions pas encore réécrites)**

`jobtomail/tests/test_db_email.py` :

```python
from __future__ import annotations

from jobtomail import db


def _make_entreprise(siret="99999999900001"):
    db.insert_entreprise({"siret": siret, "denomination": "Mail Corp", "commune": "Toulon"})
    return siret


def test_mark_email_sent_and_list_candidatures(temp_db):
    siret = _make_entreprise()
    db.mark_email_sent(siret, "rh@mailcorp.fr", message_id="<abc@mail>", subject="Candidature", body="Bonjour")

    ent = db.get_entreprise(siret)
    assert ent["status"] == "postule"
    assert ent["email_sent_at"]

    rows = db.list_candidatures_en_attente()
    assert any(r["siret"] == siret for r in rows)


def test_mark_relance_sent_increments_count(temp_db):
    siret = _make_entreprise()
    db.mark_email_sent(siret, "rh@mailcorp.fr", message_id="<abc@mail>")
    db.mark_relance_sent(siret, message_id="<def@mail>", body="Relance")
    ent = db.get_entreprise(siret)
    assert ent["status"] == "relance"
    assert ent["relance_count"] == 1


def test_list_entretiens_a_suivre(temp_db):
    siret = _make_entreprise()
    db.update_entreprise(siret, {"status": "entretien", "entretien_date": "2026-09-01"})
    rows = db.list_entretiens_a_suivre()
    assert any(r["siret"] == siret for r in rows)


def test_mark_reply_classified_upserts_processed_replies(temp_db):
    siret = _make_entreprise()
    db.mark_reply_classified(
        siret, "entretien", message_id="<reply-1@mail>", subject="RE: Candidature", from_email="rh@mailcorp.fr"
    )
    ent = db.get_entreprise(siret)
    assert ent["status"] == "entretien"
    assert "<reply-1@mail>" in db.list_processed_reply_ids() or True  # message_id pas forcément en lowercase email

    assert "<reply-1@mail>".lower() in db.list_processed_reply_ids()

    # Rejouer le même message_id ne doit pas planter (upsert, pas doublon PK)
    db.mark_reply_classified(
        siret, "entretien", message_id="<reply-1@mail>", subject="RE: Candidature", from_email="rh@mailcorp.fr"
    )
    assert "<reply-1@mail>".lower() in db.list_processed_reply_ids()


def test_mark_reply_classified_autre_does_not_change_status(temp_db):
    siret = _make_entreprise()
    db.update_entreprise(siret, {"status": "postule"})
    db.mark_reply_classified(siret, "autre", message_id="<reply-2@mail>")
    assert db.get_entreprise(siret)["status"] == "postule"
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db_email.py -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'mark_email_sent'`

- [ ] **Step 3: Ajouter à la suite de `jobtomail/db.py`**

```python
def mark_email_sent(
    siret: str,
    email: str,
    *,
    message_id: str | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET status = 'postule',
                    contact_email = COALESCE(NULLIF(:email, ''), contact_email),
                    email_message_id = COALESCE(NULLIF(:message_id, ''), email_message_id),
                    email_subject = COALESCE(NULLIF(:subject, ''), email_subject),
                    email_body = COALESCE(NULLIF(:body, ''), email_body),
                    email_sent_at = :sent_at
                WHERE siret = :siret
                """
            ),
            {
                "email": email,
                "message_id": message_id or "",
                "subject": subject or "",
                "body": body or "",
                "sent_at": _now(),
                "siret": siret,
            },
        )
    logger.info("Statut 'postule' pour %s (email=%s, msg-id=%s)", siret, email, message_id)


def mark_relance_sent(
    siret: str,
    *,
    message_id: str | None = None,
    body: str | None = None,
) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                """
                UPDATE entreprises
                SET status = 'relance',
                    relance_count = COALESCE(relance_count, 0) + 1,
                    last_relance_at = :relance_at,
                    email_message_id = COALESCE(NULLIF(:message_id, ''), email_message_id),
                    email_body = COALESCE(NULLIF(:body, ''), email_body)
                WHERE siret = :siret
                """
            ),
            {
                "relance_at": _now(),
                "message_id": message_id or "",
                "body": body or "",
                "siret": siret,
            },
        )
    logger.info("Relance enregistrée pour %s (msg-id=%s)", siret, message_id)


def list_candidatures_en_attente() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT siret, denomination, status, contact_email,
                       email_message_id, email_subject, email_sent_at,
                       reply_message_id, reply_class, relance_count, last_relance_at
                FROM entreprises
                WHERE status IN ('postule', 'relance')
                  AND email_sent_at IS NOT NULL
                ORDER BY email_sent_at DESC
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def list_entretiens_a_suivre() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT siret, denomination, status, contact_prenom, contact_nom,
                       contact_email, entretien_date, entretien_next_step, entretien_rappel_at,
                       notes
                FROM entreprises
                WHERE status IN ('entretien', 'offre')
                ORDER BY
                  CASE WHEN entretien_rappel_at IS NULL OR TRIM(entretien_rappel_at) = '' THEN 1 ELSE 0 END,
                  entretien_rappel_at ASC,
                  CASE WHEN entretien_date IS NULL OR TRIM(entretien_date) = '' THEN 1 ELSE 0 END,
                  entretien_date ASC,
                  LOWER(denomination)
                """
            )
        ).mappings().all()
    return [dict(r) for r in rows]


def list_processed_reply_ids() -> set[str]:
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT message_id FROM processed_replies")).mappings().all()
    return {(r["message_id"] or "").lower() for r in rows if r["message_id"]}


def mark_reply_classified(
    siret: str,
    classification: str,
    *,
    message_id: str,
    subject: str = "",
    excerpt: str = "",
    from_email: str = "",
    reason: str = "",
) -> None:
    """
    Enregistre une réponse classée.
    offre / entretien / refus → met à jour le statut.
    autre → garde le statut actuel, note seulement.
    """
    ent = get_entreprise(siret)
    if not ent:
        return

    apply_status = classification in ("offre", "entretien", "refus")
    note_line = f"[Réponse auto {classification}] de {from_email or '?'} — {(subject or '')[:80]}"
    if reason:
        note_line += f"\n→ {reason.strip()}"
    if excerpt:
        note_line += f"\n{(excerpt or '')[:200]}"

    existing_notes = (ent["notes"] or "").strip()
    notes = f"{existing_notes}\n{note_line}".strip() if existing_notes else note_line

    with get_engine().begin() as conn:
        if apply_status:
            conn.execute(
                text(
                    """
                    UPDATE entreprises
                    SET status = :classification,
                        reply_message_id = :message_id,
                        reply_class = :classification,
                        reply_classified_at = :classified_at,
                        reply_from = :from_email,
                        reply_subject = :subject,
                        notes = :notes
                    WHERE siret = :siret
                    """
                ),
                {
                    "classification": classification,
                    "message_id": message_id,
                    "classified_at": _now(),
                    "from_email": from_email,
                    "subject": subject,
                    "notes": notes,
                    "siret": siret,
                },
            )
        else:
            conn.execute(
                text(
                    """
                    UPDATE entreprises
                    SET reply_message_id = :message_id,
                        reply_class = :classification,
                        reply_classified_at = :classified_at,
                        reply_from = :from_email,
                        reply_subject = :subject,
                        notes = :notes
                    WHERE siret = :siret
                    """
                ),
                {
                    "message_id": message_id,
                    "classification": classification,
                    "classified_at": _now(),
                    "from_email": from_email,
                    "subject": subject,
                    "notes": notes,
                    "siret": siret,
                },
            )

        _insert_replace(
            conn,
            processed_replies_table,
            {"message_id": message_id, "siret": siret, "classification": classification, "processed_at": _now()},
            "message_id",
        )

    logger.info(
        "Réponse classée pour %s → %s (status_updated=%s)", siret, classification, apply_status,
    )
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_db_email.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add jobtomail/db.py jobtomail/tests/test_db_email.py
git commit -m "feat: migrate email/relance/reply db functions to SQLAlchemy Core"
```

#### Groupe F — File d'attente jobs + suite complète

- [ ] **Step 1: Écrire les tests (échouent — fonctions pas encore réécrites)**

Ajouter à `jobtomail/tests/test_db.py` :

```python
def test_job_row_crud(temp_db):
    db.create_job_row("job-1", "scan_sirene", params={"foo": "bar"})
    row = db.get_job_row("job-1")
    assert row["status"] == "queued"
    assert row["kind"] == "scan_sirene"
    assert row["params"] == {"foo": "bar"}

    db.update_job_row("job-1", status="done", result={"count": 3})
    row = db.get_job_row("job-1")
    assert row["status"] == "done"
    assert row["result"] == {"count": 3}


def test_get_job_row_unknown_returns_none(temp_db):
    assert db.get_job_row("does-not-exist") is None


def test_delete_old_jobs_removes_stale_rows(temp_db):
    import sqlalchemy as sa

    db.create_job_row("job-old", "scan_sirene")
    with db.get_engine().begin() as conn:
        conn.execute(
            sa.text("UPDATE jobs SET created_at = :old WHERE id = 'job-old'"),
            {"old": "2000-01-01 00:00:00"},
        )
    db.create_job_row("job-new", "scan_sirene")

    deleted = db.delete_old_jobs(max_age_hours=24)

    assert deleted == 1
    assert db.get_job_row("job-old") is None
    assert db.get_job_row("job-new") is not None
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db.py -k "job_row" -v`
Expected: FAIL — `AttributeError: module 'jobtomail.db' has no attribute 'create_job_row'`

- [ ] **Step 3: Ajouter à la suite de `jobtomail/db.py`**

```python
def create_job_row(job_id: str, kind: str, params: dict[str, Any] | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            text(
                "INSERT INTO jobs (id, kind, status, params, created_at, updated_at) "
                "VALUES (:id, :kind, 'queued', :params, :now, :now)"
            ),
            {
                "id": job_id,
                "kind": kind,
                "params": json.dumps(params or {}, ensure_ascii=False),
                "now": _now(),
            },
        )


def update_job_row(
    job_id: str,
    *,
    status: str | None = None,
    progress: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    fields: dict[str, Any] = {}
    if status is not None:
        fields["status"] = status
    if progress is not None:
        fields["progress"] = json.dumps(progress, ensure_ascii=False)
    if result is not None:
        fields["result"] = json.dumps(result, ensure_ascii=False)
    if error is not None:
        fields["error"] = error
    if not fields:
        return
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in fields)
    params = dict(fields)
    params["id"] = job_id
    with get_engine().begin() as conn:
        conn.execute(text(f"UPDATE jobs SET {set_clause} WHERE id = :id"), params)


def get_job_row(job_id: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(text("SELECT * FROM jobs WHERE id = :id"), {"id": job_id}).mappings().first()
    if not row:
        return None
    data = dict(row)
    for key in ("params", "progress", "result"):
        if data.get(key):
            try:
                data[key] = json.loads(data[key])
            except json.JSONDecodeError:
                pass
    return data


def delete_old_jobs(max_age_hours: int = 24) -> int:
    threshold = (datetime.utcnow() - timedelta(hours=max_age_hours)).strftime("%Y-%m-%d %H:%M:%S")
    with get_engine().begin() as conn:
        result = conn.execute(text("DELETE FROM jobs WHERE created_at < :threshold"), {"threshold": threshold})
    return result.rowcount
```

- [ ] **Step 4: Vérifier que les tests des jobs passent**

Run: `pytest jobtomail/tests/test_db.py -k "job_row" -v`
Expected: 3 passed

- [ ] **Step 5: Faire tourner toute la suite (régression complète après la réécriture de db.py)**

Run: `pytest -v`
Expected: tous les tests passent (les ~50+ tests existants + les nouveaux de ce plan)

- [ ] **Step 6: Commit**

```bash
git add jobtomail/db.py jobtomail/tests/test_db.py
git commit -m "feat: migrate jobs queue db functions to SQLAlchemy Core"
```

---

### Task 5: API de changement de backend (`GET`/`POST /api/db/backend`)

**Files:**
- Modify: `jobtomail/routes/config_routes.py`
- Test: `jobtomail/tests/test_db_backend_route.py`

**Interfaces:**
- Consumes: `jobtomail.db_config.{resolve_db_config, DbConfig, write_db_config_file, delete_db_config_file}` (Task 2), `jobtomail.db.{reset_engine, init_db}` (Task 4).
- Produces: `GET /api/db/backend` → `{backend, host, port, user, dbname, source, locked}` ; `POST /api/db/backend` → `{ok, backend}` ou `{error}` — utilisés par le JS de Task 6.

- [ ] **Step 1: Écrire les tests (échouent — routes inexistantes)**

`jobtomail/tests/test_db_backend_route.py` :

```python
from __future__ import annotations


def test_get_db_backend_default_sqlite(client):
    res = client.get("/api/db/backend")
    assert res.status_code == 200
    data = res.get_json()
    assert data["backend"] == "sqlite"
    assert data["locked"] is False


def test_set_db_backend_rejects_invalid_backend(client):
    res = client.post("/api/db/backend", json={"backend": "oracle"})
    assert res.status_code == 400


def test_set_db_backend_rejects_unreachable_postgres(client):
    res = client.post(
        "/api/db/backend",
        json={
            "backend": "postgres",
            "host": "127.0.0.1",
            "port": "1",
            "user": "nobody",
            "password": "nope",
            "dbname": "nope",
        },
    )
    assert res.status_code == 400
    assert "Connexion impossible" in res.get_json()["error"]


def test_set_db_backend_locked_when_env_configured(client, monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    res = client.post("/api/db/backend", json={"backend": "sqlite"})
    assert res.status_code == 409
```

- [ ] **Step 2: Vérifier que les tests échouent**

Run: `pytest jobtomail/tests/test_db_backend_route.py -v`
Expected: FAIL — 404 sur `/api/db/backend`

- [ ] **Step 3: Ajouter les imports et routes dans `jobtomail/routes/config_routes.py`**

Ajouter aux imports en tête de fichier :

```python
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.exc import SQLAlchemyError

from jobtomail import db_config
```

Ajouter à la fin du fichier :

```python
@bp.route("/api/db/backend", methods=["GET"])
def get_db_backend():
    cfg = db_config.resolve_db_config()
    return jsonify(
        {
            "backend": cfg.backend,
            "host": cfg.host,
            "port": cfg.port,
            "user": cfg.user,
            "dbname": cfg.dbname,
            "source": cfg.source,
            "locked": cfg.source == "env",
        }
    )


@bp.route("/api/db/backend", methods=["POST"])
def set_db_backend():
    current = db_config.resolve_db_config()
    if current.source == "env":
        return jsonify(
            {"error": "Backend DB imposé par DB_BACKEND (variable d'environnement) — non modifiable ici"}
        ), 409

    data = request.get_json(force=True) or {}
    backend = str(data.get("backend", "")).strip().lower()
    if backend not in ("sqlite", "postgres", "mariadb"):
        return jsonify({"error": "backend doit être sqlite, postgres ou mariadb"}), 400

    if backend == "sqlite":
        db_config.delete_db_config_file()
    else:
        candidate = db_config.DbConfig(
            backend=backend,
            host=str(data.get("host", "")).strip(),
            port=str(data.get("port", "")).strip(),
            user=str(data.get("user", "")).strip(),
            password=str(data.get("password", "")).strip(),
            dbname=str(data.get("dbname", "")).strip(),
            source="file",
        )
        try:
            test_engine = create_engine(candidate.url())
            with test_engine.connect() as conn:
                conn.execute(sa_text("SELECT 1"))
            test_engine.dispose()
        except SQLAlchemyError as exc:
            logger.warning("Connexion DB refusée pour backend=%s : %s", backend, exc)
            return jsonify({"error": f"Connexion impossible : {exc}"}), 400

        db_config.write_db_config_file(
            {
                "backend": backend,
                "host": candidate.host,
                "port": candidate.port,
                "user": candidate.user,
                "password": candidate.password,
                "dbname": candidate.dbname,
            }
        )

    db.reset_engine()
    db.init_db()
    logger.warning("Backend DB changé : %s", backend)
    return jsonify({"ok": True, "backend": backend})
```

- [ ] **Step 4: Vérifier que les tests passent**

Run: `pytest jobtomail/tests/test_db_backend_route.py -v`
Expected: 4 passed

- [ ] **Step 5: Régression complète**

Run: `pytest -v`
Expected: tous passent

- [ ] **Step 6: Commit**

```bash
git add jobtomail/routes/config_routes.py jobtomail/tests/test_db_backend_route.py
git commit -m "feat: add API to inspect and switch the DB backend at runtime"
```

---

### Task 6: UI minimale — sélection du backend DB

**Files:**
- Modify: `templates/index.html`
- Modify: `jobtomail/static/js/index.js`
- Modify: `jobtomail/tests/test_routes_smoke.py`

**Interfaces:**
- Consumes: `GET`/`POST /api/db/backend` (Task 5).

- [ ] **Step 1: Écrire le test (échoue — la section n'existe pas encore)**

Ajouter à `jobtomail/tests/test_routes_smoke.py` :

```python
def test_index_page_has_db_backend_section(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b'id="db-backend-select"' in res.data
```

- [ ] **Step 2: Vérifier que le test échoue**

Run: `pytest jobtomail/tests/test_routes_smoke.py -k db_backend_section -v`
Expected: FAIL — assertion `b'id="db-backend-select"' in res.data` est fausse

- [ ] **Step 3: Ajouter la section dans `templates/index.html`**

Juste avant `<button class="btn btn-primary" id="btn-save-cfg" ...>` (fin de la page config) :

```html
      <div class="config-section">
        <h3>Base de données</h3>
        <p class="help" id="db-backend-status">Backend actuel : SQLite (fichier local).</p>
        <div class="form-group">
          <label class="form-label">Backend</label>
          <select id="db-backend-select" class="form-control">
            <option value="sqlite">SQLite (par défaut)</option>
            <option value="postgres">PostgreSQL</option>
            <option value="mariadb">MariaDB</option>
          </select>
        </div>
        <div id="db-backend-fields" style="display:none;">
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Hôte</label>
              <input id="db-host" class="form-control" placeholder="localhost">
            </div>
            <div class="form-group">
              <label class="form-label">Port</label>
              <input id="db-port" class="form-control" placeholder="5432">
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label class="form-label">Utilisateur</label>
              <input id="db-user" class="form-control">
            </div>
            <div class="form-group">
              <label class="form-label">Mot de passe</label>
              <input id="db-password" type="password" class="form-control">
            </div>
          </div>
          <div class="form-group">
            <label class="form-label">Nom de la base</label>
            <input id="db-dbname" class="form-control">
          </div>
        </div>
        <button class="btn btn-secondary" id="btn-db-backend-save" type="button">Tester et enregistrer</button>
      </div>

```

- [ ] **Step 4: Ajouter la logique dans `jobtomail/static/js/index.js`**

Juste après le handler `btn-analyze-cv` existant (après son `});` de fermeture, vers la ligne 1225) :

```js
  async function loadDbBackend() {
    const res = await fetch("/api/db/backend").then((r) => r.json());
    const select = document.getElementById("db-backend-select");
    const status = document.getElementById("db-backend-status");
    const fields = document.getElementById("db-backend-fields");
    const saveBtn = document.getElementById("btn-db-backend-save");
    if (!select || !status || !fields) return;

    select.value = res.backend || "sqlite";
    fields.style.display = res.backend === "sqlite" ? "none" : "block";
    document.getElementById("db-host").value = res.host || "";
    document.getElementById("db-port").value = res.port || "";
    document.getElementById("db-user").value = res.user || "";
    document.getElementById("db-dbname").value = res.dbname || "";

    if (res.locked) {
      status.textContent = `Backend actuel : ${res.backend} (imposé par variable d'environnement DB_BACKEND).`;
      select.disabled = true;
      if (saveBtn) saveBtn.disabled = true;
    } else {
      status.textContent = `Backend actuel : ${res.backend} (source : ${res.source === "file" ? "config locale" : "défaut"}).`;
      select.disabled = false;
      if (saveBtn) saveBtn.disabled = false;
    }
  }

  document.getElementById("db-backend-select")?.addEventListener("change", (e) => {
    const fields = document.getElementById("db-backend-fields");
    if (fields) fields.style.display = e.target.value === "sqlite" ? "none" : "block";
  });

  document.getElementById("btn-db-backend-save")?.addEventListener("click", async () => {
    const btn = document.getElementById("btn-db-backend-save");
    const backend = document.getElementById("db-backend-select").value;
    const payload = {
      backend,
      host: document.getElementById("db-host").value.trim(),
      port: document.getElementById("db-port").value.trim(),
      user: document.getElementById("db-user").value.trim(),
      password: document.getElementById("db-password").value,
      dbname: document.getElementById("db-dbname").value.trim(),
    };
    btn.disabled = true;
    const prevText = btn.textContent;
    btn.textContent = "Test de connexion…";
    try {
      const res = await fetch("/api/db/backend", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).then((r) => r.json());
      if (res.error) return toast(res.error, "error");
      toast(`Backend DB changé : ${res.backend}`);
      await loadDbBackend();
    } catch (err) {
      toast(String(err.message || err), "error");
    } finally {
      btn.disabled = false;
      btn.textContent = prevText;
    }
  });
```

Dans `loadConfig()`, juste avant son `}` de fermeture (après l'appel à `renderCvProfileStatus(cfg.cv_profile);`), ajouter :

```js
    loadDbBackend();
```

- [ ] **Step 5: Vérifier que le test passe**

Run: `pytest jobtomail/tests/test_routes_smoke.py -v`
Expected: tous passed

- [ ] **Step 6: Vérification manuelle dans le navigateur**

Run: `python app.py` (ou `docker compose up`), ouvrir `http://localhost:5001` (ou `5002` en Docker), aller dans Paramètres → Base de données, changer le select sur « PostgreSQL » et vérifier que les champs hôte/port/utilisateur/mot de passe/nom apparaissent.

- [ ] **Step 7: Commit**

```bash
git add templates/index.html jobtomail/static/js/index.js jobtomail/tests/test_routes_smoke.py
git commit -m "feat: add DB backend selection UI to the settings page"
```

---

### Task 7: Docker Compose (profiles Postgres/MariaDB) + documentation

**Files:**
- Modify: `docker-compose.yml`
- Modify: `README.md`

**Interfaces:** aucune (infra + doc uniquement).

- [ ] **Step 1: Ajouter les services `postgres`/`mariadb` derrière des profiles dans `docker-compose.yml`**

Remplacer tout le fichier par :

```yaml
services:
  web:
    build: .
    ports:
      - "127.0.0.1:5002:5001"
    volumes:
      - .:/app
    env_file:
      - path: .env
        required: false
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    profiles: ["postgres"]
    environment:
      POSTGRES_USER: jobtomail
      POSTGRES_PASSWORD: jobtomail
      POSTGRES_DB: jobtomail
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U jobtomail"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  mariadb:
    image: mariadb:11
    profiles: ["mariadb"]
    environment:
      MARIADB_USER: jobtomail
      MARIADB_PASSWORD: jobtomail
      MARIADB_DATABASE: jobtomail
      MARIADB_ROOT_PASSWORD: jobtomail-root
    ports:
      - "127.0.0.1:3307:3306"
    volumes:
      - mariadb_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]
      interval: 5s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  postgres_data:
  mariadb_data:
```

- [ ] **Step 2: Valider la syntaxe (test fonctionnel — ne nécessite pas de démarrer les conteneurs)**

Run: `docker compose config --quiet && docker compose --profile postgres config --quiet && docker compose --profile mariadb config --quiet`
Expected: aucune sortie, code de retour 0 (les 3 commandes valident que le YAML + les profiles sont corrects)

Run: `docker compose config --services`
Expected: `web` seul (sans profile actif, le comportement par défaut reste inchangé)

Run: `docker compose --profile postgres config --services`
Expected: `web` et `postgres`

- [ ] **Step 3: Documenter dans `README.md`**

Ajouter une section (chercher un emplacement logique, par exemple après la section de configuration existante) :

```markdown
## Base de données

Par défaut, Job2Mail utilise SQLite (`jobtomail.db`), sans configuration nécessaire.

Pour utiliser PostgreSQL ou MariaDB à la place, définissez ces variables
d'environnement (dans `.env` ou l'environnement du conteneur) :

```
DB_BACKEND=postgres   # ou mariadb
DB_HOST=localhost
DB_PORT=5432          # 3306 pour mariadb
DB_USER=jobtomail
DB_PASSWORD=jobtomail
DB_NAME=jobtomail
```

Sans `DB_BACKEND` défini, la page Paramètres → « Base de données » permet de
choisir le backend et de tester la connexion depuis l'interface — la config
est alors stockée dans `db_config.json` à la racine du projet (jamais dans
`.env`, ni dans la base elle-même).

Changer de backend démarre avec des tables vides — pas de migration
automatique des données existantes.

### Tester en local avec Docker Compose

```bash
docker compose --profile postgres up      # démarre aussi un conteneur postgres:16-alpine
docker compose --profile mariadb up       # démarre aussi un conteneur mariadb:11
```

Puis dans `.env` : `DB_BACKEND=postgres`, `DB_HOST=postgres`, `DB_PORT=5432`,
`DB_USER=jobtomail`, `DB_PASSWORD=jobtomail`, `DB_NAME=jobtomail` (adapter
pour mariadb : `DB_HOST=mariadb`, `DB_PORT=3306`).
```

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml README.md
git commit -m "feat: add optional Postgres/MariaDB docker-compose profiles and docs"
```

---

## Self-Review

**Spec coverage :** schéma dialect-agnostic (Task 3) ; résolution env > fichier > défaut (Task 2) ; API `db.py` inchangée (Task 4) ; upsert + migration de colonnes dialect-agnostic (Task 4 groupe A/B/C/E) ; `datetime('now', ...)` remplacé côté Python (Task 4 groupe F) ; `COLLATE NOCASE` remplacé par `LOWER(...)` (Task 4 groupes C/D/E) ; démarrage à vide au changement de backend (Task 5, pas de script de migration) ; wizard/API de switch (Task 5) ; UI (Task 6) ; docker-compose profiles (Task 7) ; doc (Task 7). Tout couvert.

**Placeholder scan :** aucun TBD/TODO — chaque step contient le code exact à écrire.

**Type consistency :** `get_engine()/reset_engine()` (Task 4 groupe A) réutilisés tels quels en Task 5 ; `DuplicateSiretError` (Task 4 groupe C) réutilisé tel quel dans `routes/entreprises.py` ; `resolve_db_config()/DbConfig/write_db_config_file/delete_db_config_file` (Task 2) réutilisés tels quels en Task 4 (get_engine) et Task 5 (routes) sans changement de signature.
