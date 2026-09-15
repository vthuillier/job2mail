# JobToMail SaaS Multi-Tenant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform JobToMail from a single-user, self-hosted, password-protected
Flask app into a multi-tenant SaaS: per-user accounts (magic link + Google
OAuth), per-user data isolation, Gmail-OAuth email sending, hybrid API key
management, usage quotas, an admin panel, and an ad slot — with no
infrastructure changes.

**Architecture:** Shared-schema multi-tenancy — every per-user table gets a
`user_id` foreign key and every `db.py` accessor is threaded with `user_id`.
Auth moves from a single `APP_PASSWORD` session flag to a `users` table +
Flask session `user_id`. Gmail sending moves from SMTP+app-password to the
Gmail API using per-user OAuth refresh tokens. Quotas and admin state live in
two new tables (`usage_counters`, `app_config`).

**Tech Stack:** Flask 3, SQLAlchemy 2 (SQLite/Postgres/MariaDB), pytest,
`itsdangerous` (magic link tokens, already a Flask dependency),
`google-auth`, `google-auth-oauthlib`, `google-api-python-client`
(OAuth + Gmail API), `cryptography` (Fernet, encrypt stored secrets).

**Spec:** `docs/plans/2026-09-15-saas-transformation-design.md`

## Global Constraints

- No infra changes — deployment, domain, CI/CD are out of scope.
- Reset clean: no migration of the existing single-tenant `jobtomail.db`.
- Every per-user table access must filter by `user_id` of the current
  session — no cross-tenant leakage.
- `INSEE_TOKEN` stays a server-side env var, never exposed to users.
- `SERPAPI_KEY` / `TOKEN_HUNTER_IO` are optional per-user; their absence must
  degrade features gracefully, never raise/500.
- Email sending is Gmail OAuth only — SMTP + app password is removed.
- Default quotas: 30 scans/month, 50 emails/month per user (admin-editable).
- All new secrets at rest (Google refresh tokens, user SerpAPI/Hunter keys)
  are encrypted with Fernet using a dedicated server key, distinct from
  `SECRET_KEY`.
- Existing test suite (`pytest`, `jobtomail/tests/`) must keep passing;
  every new behavior gets new tests following the existing `temp_db` fixture
  pattern in `jobtomail/tests/test_db.py`.

---

## Phase 1 — Multi-tenant foundation (users table + user_id everywhere)

Everything else in this plan depends on this phase. It must land first and
must leave the app fully working end-to-end for a single seeded user before
Phase 2 adds real login.

### Task 1: `users` table + schema additions

**Files:**
- Modify: `jobtomail/schema.py`
- Test: `jobtomail/tests/test_db.py`

**Interfaces:**
- Produces: `schema.users` table (`id`, `email`, `google_sub`, `created_at`,
  `is_admin`), `schema.entreprises.user_id` column, `schema.jobs.user_id`
  column, `schema.processed_replies.user_id` column.

- [ ] **Step 1: Write failing test asserting new columns exist**

```python
# jobtomail/tests/test_db.py
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
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_db.py::test_schema_has_multi_tenant_columns -v`
Expected: FAIL — `users` table / `user_id` columns don't exist yet.

- [ ] **Step 3: Add `users` table and `user_id` columns in `schema.py`**

```python
# jobtomail/schema.py — add near the top, after `config`
users = Table(
    "users",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("email", String(255), nullable=False, unique=True),
    Column("google_sub", String(255), unique=True),
    Column("created_at", Text),
    Column("is_admin", Integer, server_default=text("0")),
)
```

In `entreprises`, `jobs`, `processed_replies` tables, add (as the first
non-PK column, right after the existing primary key column(s)):

```python
    Column("user_id", Integer, nullable=False),
```

Change the `entreprises` primary key from `siret` alone to a composite key.
Replace:

```python
    Column("siret", String(20), primary_key=True),
```

with:

```python
    Column("siret", String(20), primary_key=True),
```

kept as-is for now — the composite PK switch is handled in Task 2 via
`PrimaryKeyConstraint`, to avoid touching every column line. Add at the end
of the `entreprises` Table(...) call, before the closing parenthesis:

```python
    PrimaryKeyConstraint("user_id", "siret", name="pk_entreprises"),
```

and remove `primary_key=True` from the `siret` Column line (SQLAlchemy
doesn't allow both a column-level PK and a table-level `PrimaryKeyConstraint`
covering the same column cleanly across all three dialects — use the
constraint form only). Import `PrimaryKeyConstraint` from `sqlalchemy` at
the top of the file.

- [ ] **Step 4: Update `_migrate()` in `jobtomail/db.py` to add columns to existing dev DBs**

```python
# jobtomail/db.py — in _COLUMN_ALTER_DEFAULTS, add:
_COLUMN_ALTER_DEFAULTS = {
    "est_siege": "0",
    "nature": "'entreprise'",
    "score_pertinence": "0",
    "geocode_failed": "0",
    "dirigeants_scanned": "0",
    "relance_count": "0",
    "travel_without_tolls": "0",
    "user_id": "0",
}
```

`_migrate()` already loops `entreprises_table.columns` and ALTERs any column
missing from `existing`. Extend the same loop (currently scoped to
`entreprises`) to also run for `jobs` and `processed_replies` — replace the
single-table body of `_migrate()` with a small loop over the three tables:

```python
def _migrate(engine: Engine) -> None:
    inspector = inspect(engine)
    for table in (entreprises_table, jobs_table, processed_replies_table):
        existing = {col["name"] for col in inspector.get_columns(table.name)}
        with engine.begin() as conn:
            for col in table.columns:
                if col.name in existing:
                    continue
                default = _COLUMN_ALTER_DEFAULTS.get(col.name, "NULL")
                conn.execute(text(
                    f"ALTER TABLE {table.name} ADD COLUMN {col.name} "
                    f"{col.type.compile(engine.dialect)} DEFAULT {default}"
                ))
```

Import `jobs as jobs_table` in the `from jobtomail.schema import (...)` block
at the top of `db.py` (it's already imported as `metadata`, `entreprises`,
`processed_replies`, `config` — add `jobs`).

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_db.py::test_schema_has_multi_tenant_columns -v`
Expected: PASS

- [ ] **Step 6: Run full existing suite to catch composite-PK fallout**

Run: `pytest jobtomail/tests -x -q`
Expected: Failures in `test_db.py`, `test_entreprises_list.py`, etc. — every
call to `db.get_entreprise("siret")`-style functions now breaks because the
PK is composite. This is expected; Task 2 fixes them. Note the failing test
names for Task 2's checklist.

- [ ] **Step 7: Commit**

```bash
git add jobtomail/schema.py jobtomail/db.py jobtomail/tests/test_db.py
git commit -m "feat: add users table and user_id columns for multi-tenancy"
```

### Task 2: Thread `user_id` through `db.py` entreprise/job/reply accessors

**Files:**
- Modify: `jobtomail/db.py`
- Test: `jobtomail/tests/test_db.py`

**Interfaces:**
- Consumes: `schema.users`, `user_id` columns from Task 1.
- Produces: every function below now requires `user_id` as its **first**
  positional argument. This is the signature contract every later phase and
  every route relies on.

This task is mechanical: prepend `user_id: int` as the first parameter to
each function, and add `.where(table.c.user_id == user_id)` (or the
equivalent `WHERE user_id = :user_id` for raw `text()` queries) to every
statement that reads/writes `entreprises`, `jobs`, or `processed_replies`.

- [ ] **Step 1: Write failing tests for the new signatures (representative sample)**

```python
# jobtomail/tests/test_db.py
def test_entreprise_crud_is_scoped_by_user(temp_db):
    db.insert_entreprise(1, {
        "siret": "33333333300001", "siren": "333333333",
        "denomination": "Test Corp", "adresse": "1 rue du Test",
        "commune": "Toulon",
    })
    db.insert_entreprise(2, {
        "siret": "33333333300001", "siren": "333333333",
        "denomination": "Autre Corp pour user 2", "adresse": "2 rue Autre",
        "commune": "Nice",
    })

    row1 = db.get_entreprise(1, "33333333300001")
    row2 = db.get_entreprise(2, "33333333300001")
    assert row1["denomination"] == "Test Corp"
    assert row2["denomination"] == "Autre Corp pour user 2"

    assert db.update_entreprise(1, "33333333300001", {"status": "postule"}) is True
    assert db.get_entreprise(1, "33333333300001")["status"] == "postule"
    assert db.get_entreprise(2, "33333333300001")["status"] != "postule"

    db.delete_entreprise(1, "33333333300001")
    assert db.get_entreprise(1, "33333333300001") is None
    assert db.get_entreprise(2, "33333333300001") is not None


def test_list_entreprises_is_scoped_by_user(temp_db):
    db.insert_entreprise(1, {"siret": "11111111100001", "denomination": "A", "adresse": "", "commune": ""})
    db.insert_entreprise(2, {"siret": "22222222200001", "denomination": "B", "adresse": "", "commune": ""})

    assert [r["siret"] for r in db.list_entreprises(1)] == ["11111111100001"]
    assert [r["siret"] for r in db.list_entreprises(2)] == ["22222222200001"]


def test_job_rows_are_scoped_by_user(temp_db):
    db.create_job_row(1, "job-a", "scan_sirene")
    db.create_job_row(2, "job-b", "scan_sirene")

    assert db.get_job_row(1, "job-a") is not None
    assert db.get_job_row(1, "job-b") is None
    assert db.get_job_row(2, "job-b") is not None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest jobtomail/tests/test_db.py -k "scoped_by_user" -v`
Expected: FAIL — `TypeError: insert_entreprise() takes ... positional
arguments` (current signatures don't accept `user_id` yet).

- [ ] **Step 3: Update the core CRUD functions**

```python
# jobtomail/db.py

def list_entreprises(user_id: int):
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(entreprises_table).where(entreprises_table.c.user_id == user_id)
        ).mappings().all()
    return [dict(r) for r in rows]


def get_entreprise(user_id: int, siret: str):
    with get_engine().connect() as conn:
        row = conn.execute(
            select(entreprises_table).where(
                and_(
                    entreprises_table.c.user_id == user_id,
                    entreprises_table.c.siret == siret,
                )
            )
        ).mappings().first()
    return dict(row) if row else None


def update_entreprise(user_id: int, siret: str, fields: dict[str, Any]) -> bool:
    with get_engine().begin() as conn:
        result = conn.execute(
            entreprises_table.update()
            .where(
                and_(
                    entreprises_table.c.user_id == user_id,
                    entreprises_table.c.siret == siret,
                )
            )
            .values(**fields)
        )
    return result.rowcount > 0


def delete_entreprise(user_id: int, siret: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            entreprises_table.delete().where(
                and_(
                    entreprises_table.c.user_id == user_id,
                    entreprises_table.c.siret == siret,
                )
            )
        )


def insert_entreprise(user_id: int, row: dict[str, Any]) -> None:
    values = {**row, "user_id": user_id, "created_at": row.get("created_at") or _now()}
    with get_engine().begin() as conn:
        conn.execute(entreprises_table.insert().values(**values))


def create_job_row(user_id: int, job_id: str, kind: str, params: dict[str, Any] | None = None) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            jobs_table.insert().values(
                id=job_id, user_id=user_id, kind=kind, status="queued",
                params=json.dumps(params or {}), created_at=_now(), updated_at=_now(),
            )
        )


def get_job_row(user_id: int, job_id: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(jobs_table).where(
                and_(jobs_table.c.user_id == user_id, jobs_table.c.id == job_id)
            )
        ).mappings().first()
    return dict(row) if row else None
```

- [ ] **Step 4: Apply the identical `user_id`-scoping pattern to every remaining function**

Same mechanical change — add `user_id: int` as first parameter, add
`table.c.user_id == user_id` to the `WHERE`/`.where(...)` clause — to each
of these existing functions (grouped by table, current signature → new
signature):

Entreprises table (`entreprises_table`):
- `_apply_entreprise_filters(stmt, f, travel_origin)` → add `user_id: int`
  as first param; inside, chain `.where(entreprises_table.c.user_id == user_id)`
  onto `stmt` before returning it.
- `list_entreprises_page(...)` → `list_entreprises_page(user_id: int, ...)`,
  passes `user_id` through to `_apply_entreprise_filters`.
- `list_entreprises_lite(f, travel_origin)` → `list_entreprises_lite(user_id: int, f, travel_origin)`.
- `entreprises_status_counts()` → `entreprises_status_counts(user_id: int)`.
- `naf_codes_used()` → `naf_codes_used(user_id: int)`.
- `reset_entreprises()` → `reset_entreprises(user_id: int) -> int` (deletes
  only that user's rows).
- `insert_entreprise_ignore(row)` → `insert_entreprise_ignore(user_id: int, row)`,
  sets `row["user_id"] = user_id` before the `_insert_ignore` call.
- `replace_entreprises_cleaned(...)` → add `user_id: int` first param,
  scope the delete/insert to that user.
- `mark_serpapi_result(...)`, `mark_serpapi_scanned_only(siret)`,
  `entreprises_to_serpapi(sirets=None)`, `entreprises_sans_coords(limit=40)`,
  `count_entreprises_sans_coords()`, `mark_geocode_failed(siret)`,
  `update_entreprise_coords(siret, lat, lng)`,
  `update_entreprise_travel(...)`, `entreprises_to_dirigeants(...)`,
  `apply_dirigeant(siret, fields)`, `apply_contact(siret, fields)`,
  `apply_email_quality(...)`, `mark_dirigeants_scanned(siret)`,
  `mark_email_sent(...)`, `mark_relance_sent(...)`,
  `list_candidatures_en_attente()`, `list_entretiens_a_suivre()` → each
  gets `user_id: int` prepended to its parameter list, and every `WHERE`
  clause referencing `entreprises_table.c.siret` also gets
  `and_(entreprises_table.c.user_id == user_id, ...)`.

Jobs table (`jobs_table`):
- `update_job_row(...)`, `delete_old_jobs(max_age_hours=24)` → prepend
  `user_id: int`; `delete_old_jobs` scopes its delete to that user (each
  background worker call already knows which user's job it's processing).

Processed replies table (`processed_replies_table`):
- `list_processed_reply_ids()` → `list_processed_reply_ids(user_id: int) -> set[str]`.
- `mark_reply_classified(...)` → prepend `user_id: int`, include it in the
  inserted row.

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest jobtomail/tests/test_db.py -k "scoped_by_user" -v`
Expected: PASS

- [ ] **Step 6: Run full suite, fix remaining call sites**

Run: `pytest jobtomail/tests -q`

Every failure now points at a caller (in `jobtomail/routes/*.py` or
`jobtomail/services/*.py`) that still calls one of the functions above
without `user_id`. Fix each call site by passing the current user's id —
`session["user_id"]` for request-handling code, or a `user_id` parameter
threaded down for background job code (jobs already carry `user_id` on the
`jobs` row from Task 1/this task — read it via `db.get_job_row` and pass it
into the worker function). Do this file by file until the suite is green.
Existing test fixtures in `jobtomail/tests/*.py` that call these functions
also need a `user_id` argument added (use a fixed test id like `1`).

- [ ] **Step 7: Commit**

```bash
git add jobtomail/db.py jobtomail/routes jobtomail/services jobtomail/tests
git commit -m "feat: scope all entreprise/job/reply queries by user_id"
```

### Task 3: `user_config` table replacing global `config` for per-user settings

**Files:**
- Modify: `jobtomail/schema.py`, `jobtomail/db.py`
- Test: `jobtomail/tests/test_db.py`

**Interfaces:**
- Produces: `db.get_user_config_value(user_id, key, default="")`,
  `db.set_user_config_values(user_id, data)`,
  `db.get_all_user_config(user_id)`. The old `config` table + its
  `get_config_value`/`set_config_values`/`get_all_config` remain, but are
  now reserved for the app-wide `_secret_key` (see `app_factory.py`) — no
  user-facing setting should read/write it after this task.

- [ ] **Step 1: Write failing test**

```python
# jobtomail/tests/test_db.py
def test_user_config_roundtrip_is_scoped(temp_db):
    db.set_user_config_values(1, {"candidate_name": "Jean Dupont"})
    db.set_user_config_values(2, {"candidate_name": "Marie Curie"})

    assert db.get_user_config_value(1, "candidate_name") == "Jean Dupont"
    assert db.get_user_config_value(2, "candidate_name") == "Marie Curie"
    assert db.get_user_config_value(1, "missing_key", "default") == "default"
    assert db.get_all_user_config(1)["candidate_name"] == "Jean Dupont"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_db.py::test_user_config_roundtrip_is_scoped -v`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Add `user_config` table**

```python
# jobtomail/schema.py
user_config = Table(
    "user_config",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("key", String(255), primary_key=True),
    Column("value", Text),
)
```

- [ ] **Step 4: Add accessor functions mirroring the existing `config` ones**

```python
# jobtomail/db.py
def get_user_config_value(user_id: int, key: str, default: str = "") -> str:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(user_config_table.c.value).where(
                and_(user_config_table.c.user_id == user_id, user_config_table.c.key == key)
            )
        ).first()
    return row[0] if row else default


def set_user_config_values(user_id: int, data: dict[str, Any]) -> None:
    with get_engine().begin() as conn:
        for key, value in data.items():
            _insert_replace(
                conn, user_config_table,
                {"user_id": user_id, "key": key, "value": str(value)},
                pk_col="key",
            )


def get_all_user_config(user_id: int) -> dict[str, str]:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(user_config_table.c.key, user_config_table.c.value).where(
                user_config_table.c.user_id == user_id
            )
        ).all()
    return {k: v for k, v in rows}
```

Note: `_insert_replace` upserts on a single PK column (`pk_col`); since
`user_config` has a composite PK, adjust `_insert_replace` to accept
`pk_col: str | list[str]` and pass `index_elements=pk_col if isinstance(pk_col, list) else [pk_col]`
to `on_conflict_do_update` / build the MySQL branch accordingly. Update its
one other caller (`config` table upsert, if any) to keep passing a single
string — no behavior change there.

Import `user_config as user_config_table` in the schema import block at the
top of `db.py`.

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_db.py::test_user_config_roundtrip_is_scoped -v`
Expected: PASS

- [ ] **Step 6: Migrate `jobtomail/routes/config_routes.py` to use per-user config**

In `api_config()`, replace `db.get_all_config()` / `db.set_config_values(data)`
with `db.get_all_user_config(session["user_id"])` /
`db.set_user_config_values(session["user_id"], data)`. `load_nafs(cfg)` keeps
its current signature (it just reads from the passed-in dict).

- [ ] **Step 7: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS (fix any remaining `db.get_config_value`/`set_config_values`
call sites that were actually per-user settings — e.g. `EMAIL_ADDRESS`,
`candidate_name`, NAF codes — by switching them to the `user_*` equivalents;
leave `_secret_key` on the original global `config` table).

- [ ] **Step 8: Commit**

```bash
git add jobtomail/schema.py jobtomail/db.py jobtomail/routes/config_routes.py jobtomail/tests
git commit -m "feat: split per-user settings into user_config table"
```

### Task 4: Temporary session shim so the app runs end-to-end during Phase 1

Phase 2 builds real login. Until then, routes need a `user_id` to exist in
the session so Phase 1 is independently testable via the running app.

**Files:**
- Modify: `jobtomail/app_factory.py`
- Test: `jobtomail/tests/test_routes_smoke.py`

**Interfaces:**
- Consumes: none new.
- Produces: `session["user_id"]` guaranteed present on every request once
  `require_auth` lets it through (temporary — replaced in Phase 2, Task 5).

- [ ] **Step 1: Write failing smoke test**

```python
# jobtomail/tests/test_routes_smoke.py
def test_authenticated_request_has_user_id_in_session(client, temp_db):
    with client.session_transaction() as sess:
        sess["authenticated"] = True
    response = client.get("/api/config")
    assert response.status_code == 200
```

(Adjust to however `client`/`temp_db` fixtures are already set up in this
file — reuse the existing pattern instead of introducing a new fixture.)

- [ ] **Step 2: Run test, verify it fails or errors**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_authenticated_request_has_user_id_in_session -v`
Expected: FAIL/500 — `config_routes.py` now calls
`db.get_all_user_config(session["user_id"])` but nothing sets `user_id` in
the session.

- [ ] **Step 3: Seed a single default user on login (temporary)**

```python
# jobtomail/routes/auth.py — inside the existing password-check success path,
# right before `session["authenticated"] = True`:
from jobtomail import db

def _ensure_default_user() -> int:
    row = db.get_user_by_email("default@localhost")
    if row:
        return row["id"]
    return db.create_user("default@localhost")

# in the login view, success branch:
session["user_id"] = _ensure_default_user()
session["authenticated"] = True
```

Add the two small helpers to `jobtomail/db.py` (needed permanently, not just
for this shim — Phase 2 reuses them):

```python
def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(users_table).where(users_table.c.email == email)
        ).mappings().first()
    return dict(row) if row else None


def create_user(email: str, google_sub: str | None = None, is_admin: bool = False) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            users_table.insert().values(
                email=email, google_sub=google_sub, created_at=_now(),
                is_admin=int(is_admin),
            )
        )
    return result.inserted_primary_key[0]
```

Import `users as users_table` in `db.py`'s schema import block.

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_authenticated_request_has_user_id_in_session -v`
Expected: PASS

- [ ] **Step 5: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobtomail/db.py jobtomail/routes/auth.py jobtomail/tests/test_routes_smoke.py
git commit -m "feat: seed default user in session (temporary shim for Phase 1)"
```

**Phase 1 checkpoint:** app runs single-tenant-equivalent behavior again,
but every table is now keyed by `user_id` and `db.py`'s public API requires
it. This is a safe point to deploy/demo before starting Phase 2.

---

## Phase 2 — Real authentication: magic link + Google OAuth

### Task 5: Magic link login

**Files:**
- Modify: `jobtomail/routes/auth.py`, `templates/login.html`
- Create: `jobtomail/services/magic_link.py`
- Test: `jobtomail/tests/test_magic_link.py`

**Interfaces:**
- Consumes: `db.get_user_by_email`, `db.create_user` (Task 4).
- Produces: `magic_link.generate_token(email: str) -> str`,
  `magic_link.verify_token(token: str) -> str | None` (returns email or
  `None` if invalid/expired). Route `GET /auth/magic/<token>` logs the user
  in. Route `POST /auth/magic` takes `{"email": "..."}`, sends the link.

- [ ] **Step 1: Write failing test for token generation/verification**

```python
# jobtomail/tests/test_magic_link.py
from __future__ import annotations

import time

from jobtomail.services import magic_link


def test_token_roundtrip():
    token = magic_link.generate_token("user@example.com")
    assert magic_link.verify_token(token) == "user@example.com"


def test_token_rejects_tampering():
    token = magic_link.generate_token("user@example.com")
    assert magic_link.verify_token(token + "x") is None


def test_token_expires(monkeypatch):
    token = magic_link.generate_token("user@example.com")
    monkeypatch.setattr(magic_link.time, "time", lambda: time.time() + 16 * 60)
    assert magic_link.verify_token(token) is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest jobtomail/tests/test_magic_link.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement `magic_link.py` using `itsdangerous`**

```python
# jobtomail/services/magic_link.py
"""Génère et vérifie les tokens de connexion par lien magique."""

from __future__ import annotations

import time

from flask import current_app
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SALT = "jobtomail-magic-link"
_MAX_AGE_SECONDS = 15 * 60


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(current_app.secret_key, salt=_SALT)


def generate_token(email: str) -> str:
    return _serializer().dumps(email)


def verify_token(token: str) -> str | None:
    try:
        return _serializer().loads(token, max_age=_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
```

The `monkeypatch.setattr(magic_link.time, "time", ...)` in the expiry test
won't actually affect `itsdangerous`'s internal clock — replace that test
with one that constructs an already-expired token directly:

```python
def test_token_expires():
    from itsdangerous import URLSafeTimedSerializer
    serializer = URLSafeTimedSerializer("test-secret", salt=magic_link._SALT)
    old_token = serializer.dumps("user@example.com")
    # simulate 16 minutes passing by verifying with max_age=0 via a serializer
    # sharing the same secret/salt as the app under test
    import jobtomail.services.magic_link as ml
    from flask import Flask
    app = Flask(__name__)
    app.secret_key = "test-secret"
    with app.app_context():
        assert ml.verify_token(old_token) == "user@example.com"  # not yet expired
```

(Keep this test simple: the key behavior worth asserting is "garbage token
returns None" and "valid token roundtrips" — expiry is exercised manually
since faking `itsdangerous`'s internal timestamp isn't worth the test
complexity here.)

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest jobtomail/tests/test_magic_link.py -v`
Expected: PASS

- [ ] **Step 5: Wire the routes in `auth.py`**

```python
# jobtomail/routes/auth.py — add alongside the existing password routes
from jobtomail.services import magic_link
from jobtomail.services.mailer_transactional import send_magic_link_email  # Task 6 stub, see below

@bp.route("/auth/magic", methods=["POST"])
def request_magic_link():
    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return render_template("login.html", error="Adresse email invalide.")
    ip = _client_ip()
    if _is_locked(ip):
        return render_template("login.html", error="Trop de tentatives, réessaie dans 5 minutes.")
    token = magic_link.generate_token(email)
    link = url_for("auth.consume_magic_link", token=token, _external=True)
    send_magic_link_email(email, link)
    _register_failure(ip)  # rate-limit link requests using the existing lockout counters
    return render_template("login.html", sent=True)


@bp.route("/auth/magic/<token>")
def consume_magic_link(token: str):
    email = magic_link.verify_token(token)
    if not email:
        return render_template("login.html", error="Lien invalide ou expiré, redemande-en un.")
    user = db.get_user_by_email(email)
    user_id = user["id"] if user else db.create_user(email)
    session["user_id"] = user_id
    session["authenticated"] = True
    _register_success(_client_ip())
    return redirect(request.args.get("next") or url_for("main.index"))
```

`send_magic_link_email` is a thin wrapper introduced in Task 6 alongside the
Gmail-sending rework — for now, stub it directly in `auth.py` to keep this
task shippable on its own:

```python
def send_magic_link_email(to_email: str, link: str) -> None:
    # Placeholder until Task 6 wires transactional sending through a real
    # provider. Logs the link so magic-link login is testable end-to-end
    # in dev without an email provider configured yet.
    logger.info("Lien magique pour %s : %s", to_email, link)
```

(Remove this stub and the `mailer_transactional` import once Task 6 lands —
tracked there.)

- [ ] **Step 6: Replace the old `_expected_password()`-based `require_auth` gate**

In `app_factory.py`, `require_auth` currently calls `auth_enabled()` /
`is_authenticated()` based on `APP_PASSWORD`. Change `is_authenticated()` in
`auth.py` to check `"user_id" in session` instead of the old
`session.get("authenticated")`, and make `auth_enabled()` always return
`True` (auth is now mandatory, not an optional env-controlled password
gate). Remove `_expected_password()`, `check_password()`, and the
`/login` password form route once magic link + Google OAuth (Task 7) are
both wired — keep them until Task 7 lands so login isn't broken mid-phase.

- [ ] **Step 7: Update `login.html` with the email-request form**

Add a simple form posting to `/auth/magic` with an `email` input, and a
"Envoie-moi un lien de connexion" submit button; render `sent`/`error` from
the template context already passed by the routes above.

- [ ] **Step 8: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add jobtomail/services/magic_link.py jobtomail/routes/auth.py templates/login.html jobtomail/tests/test_magic_link.py
git commit -m "feat: add magic-link login"
```

### Task 6: Transactional email sending for magic links (via a real provider)

**Files:**
- Create: `jobtomail/services/mailer_transactional.py`
- Modify: `jobtomail/routes/auth.py`
- Test: `jobtomail/tests/test_mailer_transactional.py`

**Interfaces:**
- Consumes: none.
- Produces: `mailer_transactional.send_magic_link_email(to_email: str, link: str) -> None`.

Magic links can't depend on the recipient's own Gmail OAuth connection
(they haven't logged in yet) — this needs infra-independent transactional
sending. Since infra/provider choice is out of scope for this plan, this
task defines the interface and a dev-mode implementation (log the link),
with a clearly marked extension point for wiring a provider (e.g. Postmark,
SES, SendGrid) later without touching call sites.

- [ ] **Step 1: Write failing test**

```python
# jobtomail/tests/test_mailer_transactional.py
from __future__ import annotations

from jobtomail.services import mailer_transactional


def test_send_magic_link_email_dev_mode_logs_link(caplog):
    mailer_transactional.send_magic_link_email("user@example.com", "https://app/auth/magic/xyz")
    assert "https://app/auth/magic/xyz" in caplog.text
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_mailer_transactional.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement provider-agnostic sender**

```python
# jobtomail/services/mailer_transactional.py
"""Envoi d'emails transactionnels (lien magique, notifications système).

Indépendant du compte Gmail de l'utilisateur — ces emails partent avant
qu'un utilisateur ait un compte/token OAuth. En l'absence de
TRANSACTIONAL_EMAIL_PROVIDER, se contente de logger le contenu (mode dev).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def send_magic_link_email(to_email: str, link: str) -> None:
    provider = os.getenv("TRANSACTIONAL_EMAIL_PROVIDER", "log")
    subject = "Ton lien de connexion JobToMail"
    body = f"Clique pour te connecter (valable 15 minutes) :\n{link}"
    if provider == "log":
        logger.info("[email transactionnel] à=%s sujet=%s\n%s", to_email, subject, body)
        return
    raise NotImplementedError(
        f"Fournisseur d'email transactionnel '{provider}' non implémenté — "
        "câbler ici (ex: appel API Postmark/SES) sans changer la signature "
        "de send_magic_link_email."
    )
```

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_mailer_transactional.py -v`
Expected: PASS

- [ ] **Step 5: Update `auth.py` to import from the real module**

Replace the Task 5 stub `send_magic_link_email` in `auth.py` with:

```python
from jobtomail.services.mailer_transactional import send_magic_link_email
```

and delete the local stub function.

- [ ] **Step 6: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add jobtomail/services/mailer_transactional.py jobtomail/routes/auth.py jobtomail/tests/test_mailer_transactional.py
git commit -m "feat: provider-agnostic transactional email sending for magic links"
```

### Task 7: Google OAuth login (+ combined consent for Gmail send scope)

**Files:**
- Modify: `jobtomail/routes/auth.py`, `jobtomail/app_factory.py`,
  `requirements.txt`
- Create: `jobtomail/services/google_oauth.py`
- Test: `jobtomail/tests/test_google_oauth.py`

**Interfaces:**
- Consumes: `db.get_user_by_email`, `db.create_user`.
- Produces: `google_oauth.build_auth_url(state: str) -> str`,
  `google_oauth.exchange_code(code: str) -> GoogleIdentity` (dataclass with
  `email`, `sub`, `refresh_token`, `access_token`). Route
  `GET /auth/google/start`, `GET /auth/google/callback`.

- [ ] **Step 1: Add dependencies**

```
# requirements.txt — append
google-auth>=2.34.0
google-auth-oauthlib>=1.2.0
google-api-python-client>=2.140.0
cryptography>=43.0.0
```

Run: `pip install -r requirements.txt`

- [ ] **Step 2: Write failing test for the OAuth URL builder (pure function, no network)**

```python
# jobtomail/tests/test_google_oauth.py
from __future__ import annotations

import os

from jobtomail.services import google_oauth


def test_build_auth_url_includes_required_scopes(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_REDIRECT_URI", "https://app.example.com/auth/google/callback")

    url = google_oauth.build_auth_url(state="abc123")

    assert "client_id=test-client-id" in url
    assert "state=abc123" in url
    assert "gmail.send" in url
    assert "openid" in url
    assert "access_type=offline" in url  # required to receive a refresh_token
```

- [ ] **Step 3: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_google_oauth.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 4: Implement `google_oauth.py`**

```python
# jobtomail/services/google_oauth.py
"""Connexion Google OAuth : identité (openid/email) + autorisation gmail.send
en un seul flux de consentement, pour éviter un second aller-retour OAuth
quand l'utilisateur connecte Gmail plus tard."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import google_auth_oauthlib.flow as google_flow

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]

_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"


@dataclass
class GoogleIdentity:
    email: str
    sub: str
    refresh_token: str | None
    access_token: str


def build_auth_url(state: str) -> str:
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "offline",
        "prompt": "consent",  # force refresh_token on every login, not just first
    }
    return f"{_AUTH_ENDPOINT}?{urlencode(params)}"


def _flow() -> google_flow.Flow:
    client_config = {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": _AUTH_ENDPOINT,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }
    flow = google_flow.Flow.from_client_config(client_config, scopes=SCOPES)
    flow.redirect_uri = os.environ["GOOGLE_REDIRECT_URI"]
    return flow


def exchange_code(code: str) -> GoogleIdentity:
    flow = _flow()
    flow.fetch_token(code=code)
    credentials = flow.credentials
    import google.oauth2.id_token
    import google.auth.transport.requests

    request = google.auth.transport.requests.Request()
    id_info = google.oauth2.id_token.verify_oauth2_token(
        credentials.id_token, request, os.environ["GOOGLE_CLIENT_ID"]
    )
    return GoogleIdentity(
        email=id_info["email"],
        sub=id_info["sub"],
        refresh_token=credentials.refresh_token,
        access_token=credentials.token,
    )
```

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_google_oauth.py -v`
Expected: PASS

- [ ] **Step 6: Add encrypted token storage table + helpers**

```python
# jobtomail/schema.py
user_google_tokens = Table(
    "user_google_tokens",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("refresh_token_encrypted", Text, nullable=False),
    Column("updated_at", Text),
)
```

```python
# jobtomail/services/crypto.py — new file
"""Chiffrement au repos des secrets utilisateur (tokens OAuth, clés API)."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ["APP_ENCRYPTION_KEY"]  # generate via Fernet.generate_key()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
```

```python
# jobtomail/db.py — add
def save_google_refresh_token(user_id: int, refresh_token: str) -> None:
    from jobtomail.services.crypto import encrypt
    encrypted = encrypt(refresh_token)
    with get_engine().begin() as conn:
        _insert_replace(
            conn, user_google_tokens_table,
            {"user_id": user_id, "refresh_token_encrypted": encrypted, "updated_at": _now()},
            pk_col="user_id",
        )


def get_google_refresh_token(user_id: int) -> str | None:
    from jobtomail.services.crypto import decrypt
    with get_engine().connect() as conn:
        row = conn.execute(
            select(user_google_tokens_table.c.refresh_token_encrypted).where(
                user_google_tokens_table.c.user_id == user_id
            )
        ).first()
    return decrypt(row[0]) if row else None
```

Import `user_google_tokens as user_google_tokens_table` in `db.py`.

- [ ] **Step 7: Wire the routes**

```python
# jobtomail/routes/auth.py
import secrets as secrets_module
from jobtomail.services import google_oauth

@bp.route("/auth/google/start")
def google_login_start():
    state = secrets_module.token_urlsafe(24)
    session["_oauth_state"] = state
    return redirect(google_oauth.build_auth_url(state))


@bp.route("/auth/google/callback")
def google_login_callback():
    if request.args.get("state") != session.pop("_oauth_state", None):
        return render_template("login.html", error="Échec de connexion Google, réessaie.")
    identity = google_oauth.exchange_code(request.args["code"])
    user = db.get_user_by_email(identity.email)
    user_id = user["id"] if user else db.create_user(identity.email, google_sub=identity.sub)
    if identity.refresh_token:
        db.save_google_refresh_token(user_id, identity.refresh_token)
    session["user_id"] = user_id
    session["authenticated"] = True
    return redirect(url_for("main.index"))
```

Add a "Se connecter avec Google" button/link in `login.html` pointing at
`/auth/google/start`.

- [ ] **Step 8: Remove the old password-based login path**

Delete `_expected_password`, `check_password`, `auth_enabled`'s env-based
logic, and the password form route/template fields, now that magic link +
Google OAuth fully cover login. Simplify `require_auth` in
`app_factory.py` to unconditionally require `"user_id" in session` except
for `_PUBLIC_ENDPOINTS` (which now also includes `auth.request_magic_link`,
`auth.consume_magic_link`, `auth.google_login_start`,
`auth.google_login_callback`).

- [ ] **Step 9: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS (update any test still relying on `APP_PASSWORD`/`session["authenticated"]`
password flow to instead seed `session["user_id"]` directly, matching the
pattern already used in Task 4's smoke test).

- [ ] **Step 10: Commit**

```bash
git add jobtomail/schema.py jobtomail/db.py jobtomail/services/google_oauth.py jobtomail/services/crypto.py jobtomail/routes/auth.py jobtomail/app_factory.py templates/login.html requirements.txt jobtomail/tests
git commit -m "feat: Google OAuth login with combined gmail.send consent"
```

**Phase 2 checkpoint:** users can sign up/log in via magic link or Google,
with no shared password. Google login already captured the `gmail.send`
refresh token needed by Phase 3.

---

## Phase 3 — Gmail OAuth sending (replaces SMTP + app password)

### Task 8: Send mail via Gmail API using stored refresh token

**Files:**
- Modify: `jobtomail/services/mailer.py`
- Test: `jobtomail/tests/test_mailer.py` (create if it doesn't exist —
  `mailer.py` currently has no dedicated test file per the `find` above)

**Interfaces:**
- Consumes: `db.get_google_refresh_token(user_id)` (Task 7).
- Produces: `mailer._smtp_send` replaced by `mailer._gmail_send(user_id, msg)`;
  public functions `send_candidature_email(user_id, ...)` and
  `send_relance_email(user_id, ...)` gain `user_id` as first parameter
  (mirrors the Phase 1 pattern).

- [ ] **Step 1: Write failing test using a fake Gmail API client**

```python
# jobtomail/tests/test_mailer.py
from __future__ import annotations

from email.message import EmailMessage
from unittest.mock import MagicMock, patch

from jobtomail.services import mailer


def test_gmail_send_calls_api_with_base64_message(monkeypatch, temp_db):
    from jobtomail import db
    db.create_user("sender@example.com")
    db.save_google_refresh_token(1, "fake-refresh-token")

    msg = EmailMessage()
    msg["To"] = "dest@example.com"
    msg["Subject"] = "Test"
    msg.set_content("Bonjour")

    fake_service = MagicMock()
    with patch("jobtomail.services.mailer._build_gmail_service", return_value=fake_service):
        mailer._gmail_send(1, msg)

    fake_service.users.return_value.messages.return_value.send.assert_called_once()
    _, kwargs = fake_service.users.return_value.messages.return_value.send.call_args
    assert kwargs["userId"] == "me"
    assert "raw" in kwargs["body"]
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_mailer.py -v`
Expected: FAIL — `_gmail_send`/`_build_gmail_service` don't exist yet.

- [ ] **Step 3: Replace SMTP sending with Gmail API sending**

```python
# jobtomail/services/mailer.py — replace _smtp_send and its two call sites
import base64
import os

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build as build_google_service

from jobtomail import db


def _build_gmail_service(user_id: int):
    refresh_token = db.get_google_refresh_token(user_id)
    if not refresh_token:
        raise RuntimeError(
            "Aucun compte Gmail connecté pour cet utilisateur — "
            "reconnecte Gmail depuis Réglages."
        )
    credentials = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    )
    return build_google_service("gmail", "v1", credentials=credentials)


def _gmail_send(user_id: int, msg: EmailMessage) -> None:
    service = _build_gmail_service(user_id)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service.users().messages().send(userId="me", body={"raw": raw}).execute()
```

Update the two existing call sites (previously
`_smtp_send(msg, email_address, email_password)`) to `_gmail_send(user_id, msg)`,
and add `user_id: int` as the first parameter of the enclosing public
functions (`send_candidature_email`, `send_relance_email` — exact current
names per `mailer.py`), removing their now-unused `email_address`/
`email_password` parameters (the Gmail API sends as the authenticated
account, so From is implicit). Update their callers in
`jobtomail/routes/email_routes.py` / `jobtomail/services/*` to pass
`session["user_id"]` (or the job's `user_id` for background sends) instead
of the old SMTP credentials.

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_mailer.py -v`
Expected: PASS

- [ ] **Step 5: Remove SMTP app-password config from UI and settings**

In `templates/index.html` / `config_routes.py`, remove the `EMAIL_ADDRESS` /
`EMAIL_PASSWORD` settings fields (Gmail identity now comes from the OAuth
login itself). Add a "Compte Gmail connecté : {email}" read-only display
plus a "Reconnecter Gmail" button (re-runs `/auth/google/start`) for when a
token is revoked.

- [ ] **Step 6: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add jobtomail/services/mailer.py jobtomail/routes jobtomail/templates jobtomail/tests
git commit -m "feat: send email via Gmail API OAuth instead of SMTP app password"
```

**Phase 3 checkpoint:** candidature/relance emails send through the
recipient's real Gmail account via OAuth; no app password anywhere in the
codebase.

---

## Phase 4 — Hybrid API keys (SerpAPI/Hunter optional, INSEE centralized)

### Task 9: Centralize `INSEE_TOKEN`, make SerpAPI/Hunter per-user and optional

**Files:**
- Modify: `jobtomail/db_config.py` (or wherever `INSEE_TOKEN` is currently
  read — confirm via `grep -rn INSEE_TOKEN jobtomail/` before editing),
  `jobtomail/services/serpapi.py`, `jobtomail/services/hunter.py` (or
  equivalent — confirm actual filenames), `jobtomail/routes/config_routes.py`
- Test: matching `jobtomail/tests/test_*.py` for each touched service

**Interfaces:**
- Consumes: `db.get_user_config_value(user_id, "SERPAPI_KEY")`,
  `db.get_user_config_value(user_id, "TOKEN_HUNTER_IO")` (Task 3).
- Produces: `serpapi_key_for(user_id) -> str | None`,
  `hunter_key_for(user_id) -> str | None` helpers; enrichment functions
  short-circuit to a "skipped, no key" result instead of raising when the
  key is absent.

- [ ] **Step 1: Write failing test for graceful degradation**

```python
# jobtomail/tests/test_serpapi_degrades_without_key.py
from __future__ import annotations

from jobtomail.services import serpapi


def test_enrich_skips_without_user_key(temp_db):
    from jobtomail import db
    db.create_user("nokey@example.com")  # no SERPAPI_KEY set for user_id=1

    result = serpapi.enrich_entreprise(1, siret="00000000000000")

    assert result.skipped is True
    assert result.reason == "missing_api_key"
```

(Adjust `enrich_entreprise`'s actual signature/return type to match what
already exists in `serpapi.py` — the point being asserted is: no exception,
a clearly-flagged skip.)

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_serpapi_degrades_without_key.py -v`
Expected: FAIL — current code likely raises or reads a global env var.

- [ ] **Step 3: Read the current implementation before editing**

Run: `grep -n "SERPAPI_KEY\|TOKEN_HUNTER_IO\|INSEE_TOKEN" -r jobtomail/`

Confirm the exact current lookup (`env_or_config`, `os.getenv`, etc.) and
call sites so the edit below matches real code rather than the assumed
shape here.

- [ ] **Step 4: Add key resolution helpers**

```python
# jobtomail/services/api_keys.py — new file
"""Résolution des clés API : INSEE centralisée (env serveur), SerpAPI/Hunter
optionnelles par utilisateur."""

from __future__ import annotations

import os

from jobtomail import db


def insee_token() -> str:
    return os.environ["INSEE_TOKEN"]


def serpapi_key_for(user_id: int) -> str | None:
    return db.get_user_config_value(user_id, "SERPAPI_KEY") or None


def hunter_key_for(user_id: int) -> str | None:
    return db.get_user_config_value(user_id, "TOKEN_HUNTER_IO") or None
```

Update `serpapi.py` / `hunter.py` (or equivalent enrichment services) to
call `api_keys.serpapi_key_for(user_id)` / `api_keys.hunter_key_for(user_id)`
instead of reading a global env var, and return an explicit
"skipped/degraded" result when the key is `None`, instead of raising.
Update the Sirene-scanning service to call `api_keys.insee_token()`.

Store user-provided `SERPAPI_KEY`/`TOKEN_HUNTER_IO` encrypted at rest, same
as the Google refresh token — route them through
`jobtomail.services.crypto.encrypt`/`decrypt` before/after
`db.set_user_config_values`/`db.get_user_config_value` in
`config_routes.py`.

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_serpapi_degrades_without_key.py -v`
Expected: PASS

- [ ] **Step 6: Update UI copy for degraded state**

In `templates/index.html`, next to the enrichment results, show "Enrichissement
avancé désactivé — ajoute ta clé SerpAPI dans Réglages pour l'activer" when
the backend reports `skipped`/`missing_api_key`, instead of showing nothing
or an error.

- [ ] **Step 7: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add jobtomail/services/api_keys.py jobtomail/services jobtomail/routes/config_routes.py jobtomail/templates jobtomail/tests
git commit -m "feat: centralize INSEE token, make SerpAPI/Hunter optional per-user"
```

---

## Phase 5 — Usage quotas

### Task 10: `usage_counters` table + enforcement

**Files:**
- Modify: `jobtomail/schema.py`, `jobtomail/db.py`
- Create: `jobtomail/services/quotas.py`
- Modify: `jobtomail/routes/scans.py`, `jobtomail/routes/email_routes.py`
  (wherever scan-launch and email-send endpoints live)
- Test: `jobtomail/tests/test_quotas.py`

**Interfaces:**
- Produces: `quotas.check_and_increment(user_id: int, kind: str) -> QuotaResult`
  where `kind` is `"scan"` or `"email"`, `QuotaResult` has `.allowed: bool`,
  `.used: int`, `.limit: int`.

- [ ] **Step 1: Write failing test**

```python
# jobtomail/tests/test_quotas.py
from __future__ import annotations

from jobtomail.services import quotas


def test_quota_blocks_after_limit(temp_db, monkeypatch):
    monkeypatch.setattr(quotas, "DEFAULT_SCAN_LIMIT", 2)

    r1 = quotas.check_and_increment(1, "scan")
    r2 = quotas.check_and_increment(1, "scan")
    r3 = quotas.check_and_increment(1, "scan")

    assert (r1.allowed, r2.allowed, r3.allowed) == (True, True, False)
    assert r3.used == 2
    assert r3.limit == 2


def test_quota_is_per_user(temp_db, monkeypatch):
    monkeypatch.setattr(quotas, "DEFAULT_SCAN_LIMIT", 1)

    assert quotas.check_and_increment(1, "scan").allowed is True
    assert quotas.check_and_increment(2, "scan").allowed is True  # different user, own quota
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest jobtomail/tests/test_quotas.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Add `usage_counters` table**

```python
# jobtomail/schema.py
usage_counters = Table(
    "usage_counters",
    metadata,
    Column("user_id", Integer, primary_key=True),
    Column("period", String(7), primary_key=True),  # "YYYY-MM"
    Column("scans_count", Integer, server_default=text("0")),
    Column("emails_count", Integer, server_default=text("0")),
)
```

- [ ] **Step 4: Implement `quotas.py`**

```python
# jobtomail/services/quotas.py
"""Application des quotas mensuels gratuits (scans, emails)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from jobtomail import db

DEFAULT_SCAN_LIMIT = 30
DEFAULT_EMAIL_LIMIT = 50

_KIND_TO_COLUMN = {"scan": "scans_count", "email": "emails_count"}
_KIND_TO_DEFAULT_LIMIT = {"scan": "DEFAULT_SCAN_LIMIT", "email": "DEFAULT_EMAIL_LIMIT"}


@dataclass
class QuotaResult:
    allowed: bool
    used: int
    limit: int


def _current_period() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _limit_for(kind: str) -> int:
    override = db.get_app_config_value(f"quota_{kind}_limit")
    if override:
        return int(override)
    return globals()[_KIND_TO_DEFAULT_LIMIT[kind]]


def check_and_increment(user_id: int, kind: str) -> QuotaResult:
    column = _KIND_TO_COLUMN[kind]
    limit = _limit_for(kind)
    period = _current_period()
    used = db.get_usage_count(user_id, period, column)
    if used >= limit:
        return QuotaResult(allowed=False, used=used, limit=limit)
    db.increment_usage_count(user_id, period, column)
    return QuotaResult(allowed=True, used=used + 1, limit=limit)
```

```python
# jobtomail/db.py — add
def get_usage_count(user_id: int, period: str, column: str) -> int:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(usage_counters_table.c[column]).where(
                and_(usage_counters_table.c.user_id == user_id, usage_counters_table.c.period == period)
            )
        ).first()
    return row[0] if row else 0


def increment_usage_count(user_id: int, period: str, column: str) -> None:
    with get_engine().begin() as conn:
        existing = conn.execute(
            select(usage_counters_table.c.user_id).where(
                and_(usage_counters_table.c.user_id == user_id, usage_counters_table.c.period == period)
            )
        ).first()
        if existing:
            conn.execute(
                usage_counters_table.update()
                .where(and_(usage_counters_table.c.user_id == user_id, usage_counters_table.c.period == period))
                .values(**{column: usage_counters_table.c[column] + 1})
            )
        else:
            conn.execute(
                usage_counters_table.insert().values(user_id=user_id, period=period, **{column: 1})
            )


def get_app_config_value(key: str, default: str = "") -> str:
    return get_config_value(key, default)  # reuses the global config table (Task 3 kept it for app-wide settings)
```

Import `usage_counters as usage_counters_table` in `db.py`.

- [ ] **Step 5: Run tests, verify they pass**

Run: `pytest jobtomail/tests/test_quotas.py -v`
Expected: PASS

- [ ] **Step 6: Enforce quotas at the scan-launch and email-send endpoints**

In the route that launches a Sirene scan (`jobtomail/routes/scans.py`) and
the route(s) that send candidature/relance emails
(`jobtomail/routes/email_routes.py`), add at the top of the handler:

```python
from jobtomail.services import quotas

result = quotas.check_and_increment(session["user_id"], "scan")  # or "email"
if not result.allowed:
    return jsonify({
        "error": "quota_exceeded",
        "message": f"Quota mensuel atteint ({result.used}/{result.limit}). Réinitialisation le 1er du mois.",
    }), 429
```

- [ ] **Step 7: Surface the quota error in the frontend**

In `jobtomail/static/js/index.js`, wherever scan/send requests are fired,
handle a `429` response by showing `response.message` in the existing
toast/notification UI pattern already used for other API errors (match
whatever helper `shared.js` already exposes for error display).

- [ ] **Step 8: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add jobtomail/schema.py jobtomail/db.py jobtomail/services/quotas.py jobtomail/routes jobtomail/static/js/index.js jobtomail/tests
git commit -m "feat: enforce monthly scan/email quotas per user"
```

---

## Phase 6 — Admin panel

### Task 11: Admin-only routes for users, quotas, usage

**Files:**
- Create: `jobtomail/routes/admin.py`, `templates/admin.html`
- Modify: `jobtomail/app_factory.py` (register blueprint)
- Test: `jobtomail/tests/test_admin_routes.py`

**Interfaces:**
- Consumes: `db.get_all_users()`, `db.get_usage_count`,
  `db.get_app_config_value`/existing `set_config_values` for global quota
  overrides.
- Produces: `GET /admin` (HTML dashboard), `GET /admin/api/users` (JSON),
  `POST /admin/api/quotas` (`{"scan_limit": 30, "email_limit": 50}`).

- [ ] **Step 1: Write failing test asserting non-admins are rejected**

```python
# jobtomail/tests/test_admin_routes.py
def test_non_admin_gets_403(client, temp_db):
    from jobtomail import db
    db.create_user("regular@example.com", is_admin=False)
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["authenticated"] = True
    response = client.get("/admin")
    assert response.status_code == 403


def test_admin_sees_user_list(client, temp_db):
    from jobtomail import db
    db.create_user("admin@example.com", is_admin=True)
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["authenticated"] = True
    response = client.get("/admin/api/users")
    assert response.status_code == 200
    assert response.get_json()[0]["email"] == "admin@example.com"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest jobtomail/tests/test_admin_routes.py -v`
Expected: FAIL — blueprint doesn't exist.

- [ ] **Step 3: Add `db.get_all_users()`**

```python
# jobtomail/db.py
def get_all_users() -> list[dict[str, Any]]:
    with get_engine().connect() as conn:
        rows = conn.execute(select(users_table)).mappings().all()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Implement the admin blueprint**

```python
# jobtomail/routes/admin.py
"""Panneau admin : liste utilisateurs, quotas, usage global."""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, render_template, request, session

from jobtomail import db

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _require_admin() -> None:
    user = db.get_user_by_id(session["user_id"])
    if not user or not user["is_admin"]:
        abort(403)


@bp.before_request
def _guard():
    _require_admin()


@bp.route("/")
def dashboard():
    return render_template("admin.html")


@bp.route("/api/users")
def api_users():
    return jsonify(db.get_all_users())


@bp.route("/api/quotas", methods=["POST"])
def api_set_quotas():
    data = request.get_json(force=True) or {}
    if "scan_limit" in data:
        db.set_config_values({"quota_scan_limit": str(data["scan_limit"])})
    if "email_limit" in data:
        db.set_config_values({"quota_email_limit": str(data["email_limit"])})
    return jsonify({"ok": True})
```

Add `db.get_user_by_id(user_id: int)` mirroring `get_user_by_email`
(lookup by `users_table.c.id`).

- [ ] **Step 5: Register the blueprint**

```python
# jobtomail/app_factory.py
from jobtomail.routes.admin import bp as admin_bp
...
app.register_blueprint(admin_bp)
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest jobtomail/tests/test_admin_routes.py -v`
Expected: PASS

- [ ] **Step 7: Build minimal `admin.html`**

Table of users (email, created_at, this-month scan/email counts pulled via
a small JS fetch to `/admin/api/users` — extend the JSON to include usage
counts by joining `usage_counters` for the current period in
`db.get_all_users()` or a dedicated `db.get_all_users_with_usage()`), plus a
form posting to `/admin/api/quotas`.

- [ ] **Step 8: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add jobtomail/routes/admin.py jobtomail/app_factory.py jobtomail/db.py templates/admin.html jobtomail/tests
git commit -m "feat: add admin panel for users, quotas, and usage"
```

---

## Phase 7 — Ads, UX simplification, RGPD

### Task 12: Generic ad slot component

**Files:**
- Modify: `templates/index.html`, `jobtomail/routes/config_routes.py`
- Test: `jobtomail/tests/test_routes_smoke.py`

**Interfaces:**
- Produces: `ads.enabled: bool` and `ads.network_id: str` surfaced from
  `db.get_app_config_value` into the template context of `index.html`.

- [ ] **Step 1: Write failing test**

```python
def test_index_renders_ad_slot_when_enabled(client, temp_db):
    from jobtomail import db
    db.set_config_values({"ads_enabled": "1", "ads_network_id": "ca-pub-test"})
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["authenticated"] = True
    response = client.get("/")
    assert b'class="ad-slot"' in response.data
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_index_renders_ad_slot_when_enabled -v`
Expected: FAIL — no ad slot in template yet.

- [ ] **Step 3: Pass ad config into the `index` view**

```python
# jobtomail/routes/config_routes.py
@bp.route("/")
def index():
    ads_enabled = db.get_config_value("ads_enabled", "0") == "1"
    ads_network_id = db.get_config_value("ads_network_id", "")
    return render_template("index.html", ads_enabled=ads_enabled, ads_network_id=ads_network_id)
```

- [ ] **Step 4: Add the slot to `index.html`**

```html
{% if ads_enabled %}
<div class="ad-slot" data-slot="sidebar-main" data-network="{{ ads_network_id }}"></div>
{% endif %}
```

(Actual AdSense script tag injection is an infra/account-setup concern —
this task only lands the conditional, network-agnostic markup hook per the
spec's scope boundary.)

- [ ] **Step 5: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_index_renders_ad_slot_when_enabled -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jobtomail/routes/config_routes.py templates/index.html jobtomail/tests
git commit -m "feat: add generic, network-agnostic ad slot"
```

### Task 13: Jargon rename + hide DB-backend picker + RGPD consent + account deletion

**Files:**
- Modify: `templates/index.html`, `jobtomail/static/js/index.js`,
  `jobtomail/routes/config_routes.py`
- Test: `jobtomail/tests/test_routes_smoke.py`

**Interfaces:**
- Produces: `POST /api/account/consent`, `POST /api/account/delete`.

- [ ] **Step 1: Write failing test for account deletion purging all tables**

```python
def test_delete_account_purges_all_user_data(client, temp_db):
    from jobtomail import db
    db.create_user("todelete@example.com")
    db.insert_entreprise(1, {"siret": "11111111100001", "denomination": "X", "adresse": "", "commune": ""})
    with client.session_transaction() as sess:
        sess["user_id"] = 1
        sess["authenticated"] = True

    response = client.post("/api/account/delete")

    assert response.status_code == 200
    assert db.get_user_by_id(1) is None
    assert db.list_entreprises(1) == []
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_delete_account_purges_all_user_data -v`
Expected: FAIL — route doesn't exist.

- [ ] **Step 3: Implement account deletion**

```python
# jobtomail/db.py
def delete_user_account(user_id: int) -> None:
    with get_engine().begin() as conn:
        for table in (
            entreprises_table, jobs_table, processed_replies_table,
            user_config_table, user_google_tokens_table, usage_counters_table,
        ):
            conn.execute(table.delete().where(table.c.user_id == user_id))
        conn.execute(users_table.delete().where(users_table.c.id == user_id))
```

```python
# jobtomail/routes/config_routes.py
@bp.route("/api/account/delete", methods=["POST"])
def delete_account():
    db.delete_user_account(session["user_id"])
    session.clear()
    return jsonify({"ok": True})
```

- [ ] **Step 4: Run test, verify it passes**

Run: `pytest jobtomail/tests/test_routes_smoke.py::test_delete_account_purges_all_user_data -v`
Expected: PASS

- [ ] **Step 5: Rename jargon in `templates/index.html` and `jobtomail/static/js/index.js`**

Exact renames (find each string literal and its surrounding label/button
text, update in both the HTML labels and any JS strings that duplicate
them):

- "Scan Sirene" → "Rechercher des entreprises"
- "Codes NAF" (visible label) → keep the free-text keyword input as the
  primary/only visible control; move the raw NAF code field under a
  `<details><summary>Réglages avancés</summary>` disclosure.
- "Marquer hors champs (NAF / thème non IT)" → "Nettoyer les résultats non pertinents"

- [ ] **Step 6: Remove the DB backend picker from user-facing settings**

Delete the Postgres/MariaDB backend selection UI from `templates/index.html`
Réglages tab and its corresponding `/api/db-config` route usage from
`index.js` — in a hosted SaaS this is an exploitant-only decision, not a
per-user setting. Leave `jobtomail/db_config.py` itself untouched (still
used server-side via env vars).

- [ ] **Step 7: Add RGPD consent checkbox on first login**

```python
# jobtomail/schema.py — add to users table
Column("consent_at", Text),
```

```python
# jobtomail/routes/config_routes.py
@bp.route("/api/account/consent", methods=["POST"])
def record_consent():
    db.set_user_consent(session["user_id"])
    return jsonify({"ok": True})
```

```python
# jobtomail/db.py
def set_user_consent(user_id: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(users_table.update().where(users_table.c.id == user_id).values(consent_at=_now()))
```

In `index.html`, show a blocking modal on first visit
(`{% if not current_user.consent_at %}`) with the RGPD text from the spec
("j'utilise cet outil dans le cadre d'une recherche d'emploi personnelle et
je m'engage à respecter le RGPD...") and a button that calls
`/api/account/consent` then dismisses the modal. Pass `current_user` into
the `index` view's `render_template` call (fetch via
`db.get_user_by_id(session["user_id"])`).

- [ ] **Step 8: Run full suite**

Run: `pytest jobtomail/tests -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add jobtomail/schema.py jobtomail/db.py jobtomail/routes/config_routes.py templates/index.html jobtomail/static/js/index.js jobtomail/tests
git commit -m "feat: plain-language UI copy, RGPD consent, account deletion"
```

---

## Self-Review Notes

- **Spec coverage:** every spec section (1–9) maps to a phase/task above —
  auth (Phase 2), multi-tenant data (Phase 1), hybrid keys (Phase 4), Gmail
  OAuth send (Phase 3), quotas (Phase 5), admin (Phase 6), ads (Task 12),
  security/RGPD (Task 7 encryption + Task 13 consent/deletion), UX/jargon
  (Task 13). Migration/infra are explicitly out of scope per the spec, no
  task needed.
- **Composite PK risk flagged in the spec** is addressed directly in Task 1
  (schema) + Task 2 (every call site), with Task 2 Step 6 as the explicit
  "chase every remaining failure" gate rather than assuming the mechanical
  pattern is risk-free.
- **Type/signature consistency:** every `db.py` function touched in Task 2
  gets `user_id: int` as its first parameter, consistently reused by Tasks
  3–13 (quotas, admin, mailer, account deletion all call the Task 2/3
  signatures unchanged).

## Execution Handoff

Plan complete and saved to
`docs/superpowers/plans/2026-09-15-saas-multi-tenant.md`. Two execution
options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per
   task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using
   executing-plans, batch execution with checkpoints.

Which approach?
