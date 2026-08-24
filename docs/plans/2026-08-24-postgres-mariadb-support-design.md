# Support Postgres / MariaDB — design

Date: 2026-08-24

## Problem

Job2Mail's persistence layer (`jobtomail/db.py`) is hardwired to SQLite:
`sqlite3` connections, `?` positional placeholders, `INSERT OR IGNORE` /
`INSERT OR REPLACE`, `PRAGMA table_info` for migrations, and
`datetime('now', '-Nh')` for age-based deletes. All of this is confined to
`db.py` (routes/services only call `db.*` functions), which is good, but it
means switching database engines is currently impossible without a full
rewrite. We want to let users optionally run Job2Mail against Postgres or
MariaDB instead of the bundled SQLite file, while changing nothing for
users who don't configure anything.

## Goals

- Support three backends: SQLite (default, unchanged behavior), PostgreSQL,
  MariaDB.
- Zero behavior change when no DB backend is configured (SQLite on
  `jobtomail.db`, same as today).
- Same public API surface in `jobtomail/db.py` — no changes needed in
  routes, services, or templates.
- Backend chosen via environment variables (Docker/CI) or via the
  interactive first-run setup wizard (local file, not the app DB — avoids
  a chicken-and-egg problem where DB credentials would need to live inside
  the DB they describe).
- No data migration tooling between backends — switching backends starts
  with empty tables (single-user personal tool, acceptable trade-off).
- Optional Postgres/MariaDB services in `docker-compose.yml`, gated behind
  Compose profiles, default `docker-compose up` stays SQLite-only.

## Non-goals

- No ORM models / ActiveRecord-style objects — SQLAlchemy Core only (query
  builder + engine), not the ORM layer.
- No automatic data migration between backends.
- No support for backends other than SQLite/Postgres/MariaDB.
- No connection pooling tuning beyond SQLAlchemy defaults.

## Architecture

### Schema module

New `jobtomail/schema.py` defines the four tables (`config`, `entreprises`,
`processed_replies`, `jobs`) as `sqlalchemy.MetaData` / `Table` objects
using generic column types (`Integer`, `String`/`Text`, `Float`, `Boolean`,
`DateTime`). SQLAlchemy compiles the correct DDL per dialect at
`create_all()` time — no per-backend CREATE TABLE strings.

### `db.py` — public API unchanged, internals rewritten

Every function in `db.py` keeps its exact name and signature. Internally:

- `get_db()` (a raw `sqlite3.Connection`) is replaced by an internal
  `_connect()` / `_begin()` context manager wrapping
  `engine.connect()` / `engine.begin()`.
- Raw `?`-parameterized SQL strings become `sqlalchemy.text("... :siret")`
  with named parameters.
- Query results use `.mappings()` so callers can keep doing
  `row["column"]` exactly like they do today with `sqlite3.Row`.

### Backend resolution (env → local file → default)

Resolution order, first match wins:

1. **Environment variables**: `DB_BACKEND` (`sqlite` | `postgres` |
   `mariadb`), `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`.
   Used for Docker / CI / explicit deployments.
2. **Local config file**: `db_config.json` at the project root (sibling of
   `jobtomail.db`), written by the first-run setup wizard. Deliberately
   NOT stored in `.env` and NOT stored in the app's `config` DB table —
   the DB backend must be resolvable *before* any DB connection exists.
3. **Default**: `sqlite` on the existing `jobtomail.db` path — identical
   to current behavior when nothing is configured.

`resolve_db_config()` implements this order and returns a dict describing
the backend + connection parameters (or the SQLite path).

### Engine construction

`sqlalchemy.create_engine(url)` where the URL is built from the resolved
config:

- SQLite: `sqlite:///<DB_PATH>`
- Postgres: `postgresql+psycopg2://user:pass@host:port/dbname`
- MariaDB: `mysql+pymysql://user:pass@host:port/dbname`

The engine is cached at module level (`_ENGINE`), built lazily on first
use. An explicit `reset_engine()` function clears the cache — used by
tests after monkeypatching config so a fresh engine picks up the new
target.

### New dependencies

- `sqlalchemy>=2.0`
- `psycopg2-binary` (PostgreSQL driver)
- `PyMySQL` (MariaDB/MySQL driver — pure Python, no native build step
  needed in the Docker image)

### Schema creation & column migration

- Table creation: `metadata.create_all(engine, checkfirst=True)` replaces
  the manual `CREATE TABLE IF NOT EXISTS` blocks — dialect-agnostic.
- Extra-column migration (today's `_EXTRA_COLUMNS` / `_migrate()`):
  `sqlalchemy.inspect(engine).get_columns("entreprises")` replaces
  `PRAGMA table_info` (works identically across all three dialects). For
  each missing column, `ALTER TABLE entreprises ADD COLUMN <name>
  <type>` is issued with the column type compiled for the active dialect
  (`column.type.compile(dialect=engine.dialect)`).

### Dialect-specific statements (small, contained set)

A handful of statements have no single dialect-agnostic SQLAlchemy Core
spelling and need a small per-dialect branch inside `db.py`:

- **Upsert helpers** — `_insert_ignore(conn, table, values)` and
  `_insert_replace(conn, table, values, pk_col)` branch on
  `engine.dialect.name` (`sqlite` / `postgresql` / `mysql`) using
  SQLAlchemy's dialect-specific insert constructs
  (`sqlite.insert().on_conflict_do_nothing()`,
  `postgresql.insert().on_conflict_do_nothing()`,
  `mysql.insert().prefix_with("IGNORE")` for ignore;
  `on_conflict_do_update` / `ON DUPLICATE KEY UPDATE` for replace). Used
  by `insert_entreprise_ignore`, `set_config_values`,
  `mark_reply_classified` (the `processed_replies` upsert).
- **`delete_old_jobs`** — the SQLite-only `datetime('now', '-Nh')`
  expression is replaced by a Python-computed threshold
  (`datetime.utcnow() - timedelta(hours=max_age_hours)`) passed as a bound
  parameter. Dialect-agnostic, no SQL string needed.

Everything else in `db.py` is plain `SELECT` / `UPDATE` / `DELETE` with
named parameters, which SQLAlchemy Core handles identically across all
three dialects.

### First-run setup wizard

`jobtomail/routes/config_routes.py` gains a DB-backend step: if no
`DB_BACKEND` env var and no `db_config.json` exist, the setup UI offers a
choice of SQLite (default, skip this step) / Postgres / MariaDB with
host/port/user/password/dbname fields. On submit, the backend attempts a
connection; on success it writes `db_config.json` and proceeds; on
failure it reports the error and lets the user retry or fall back to
SQLite.

### Docker Compose

`docker-compose.yml` gains `postgres` and `mariadb` services behind
Compose profiles (`--profile postgres`, `--profile mariadb`), each with a
named volume for persistence and a healthcheck. Matching `DB_*`
environment variables are pre-wired to the `web` service so
`docker-compose --profile postgres up` works out of the box. Plain
`docker-compose up` (no profile) is unaffected — still SQLite, matching
current behavior exactly.

### Testing

- `jobtomail/tests/conftest.py`: `temp_db` fixture keeps SQLite (fast, no
  external infra required in CI) — after monkeypatching the resolved
  config to point at a temp file, it calls `db.reset_engine()` so a fresh
  engine targets the temp DB.
- New unit tests cover `resolve_db_config()` priority order (env beats
  file beats default) and the upsert helpers' dialect branching (dialect
  name mocked/forced, not a real Postgres/MariaDB connection — CI stays
  SQLite-only and fast).
- Functional/integration tests exercise the full `db.py` public API
  (insert/get/update/delete, config roundtrip, job lifecycle, upsert
  paths) against a real temporary SQLite database — same style as the
  existing `test_db.py` and `test_jobs.py`, extended to cover the new
  upsert helpers and column-migration path explicitly.
- Manual verification against real Postgres/MariaDB is documented in the
  README (via the new docker-compose profiles) — not part of automated
  CI, since spinning up real Postgres/MariaDB containers in CI is out of
  scope for this change.

## Error handling

Connection failures at startup (unreachable host, bad credentials) fail
fast with a clear log message naming the offending `DB_*` variable /
`db_config.json` field, matching the current SQLite-era failure posture
(no silent fallback to SQLite once a non-SQLite backend is explicitly
configured — masking a misconfiguration would be worse than crashing).

## Files touched

- `jobtomail/schema.py` (new)
- `jobtomail/db.py` (internals rewritten, public API unchanged)
- `jobtomail/constants.py` (add `DB_CONFIG_PATH`)
- `jobtomail/routes/config_routes.py` (setup wizard DB-backend step)
- `requirements.txt` (sqlalchemy, psycopg2-binary, PyMySQL)
- `docker-compose.yml` (postgres/mariadb profiles)
- `README.md` (env vars, docker-compose profile usage)
- `jobtomail/tests/conftest.py` (`reset_engine()` seam)
- `jobtomail/tests/test_db.py` (extended: upsert helpers, migration path)
- `jobtomail/tests/test_db_config.py` (new: backend resolution priority)
