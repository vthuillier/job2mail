"""Accès DB (SQLite / PostgreSQL / MariaDB) et configuration persistée."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, create_engine, func, inspect, or_, select, text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from jobtomail.constants import DEFAULT_NAF_CODES
from jobtomail.db_config import resolve_db_config
from jobtomail.schema import (
    config as config_table,
    entreprises as entreprises_table,
    ix_entreprises_score_denom,
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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


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
    ix_entreprises_score_denom.create(engine, checkfirst=True)


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


def _apply_entreprise_filters(stmt, f: dict[str, Any], travel_origin: str | None):
    """Applique les filtres liste/carte/candidats-trajet (mêmes règles que l'UI)."""
    t = entreprises_table

    status = (f.get("status") or "").strip()
    if status and status != "tous":
        stmt = stmt.where(t.c.status == status)

    search = (f.get("search") or "").strip().lower()
    if search:
        like = f"%{search}%"
        stmt = stmt.where(
            or_(
                func.lower(func.coalesce(t.c.denomination, "")).like(like),
                func.lower(func.coalesce(t.c.commune, "")).like(like),
                func.lower(func.coalesce(t.c.contact_nom, "")).like(like),
                func.lower(func.coalesce(t.c.naf_code, "")).like(like),
                func.lower(func.coalesce(t.c.naf_libelle, "")).like(like),
            )
        )

    naf = [c for c in (f.get("naf") or []) if c]
    if naf:
        stmt = stmt.where(t.c.naf_code.in_(naf))

    effectif = [c for c in (f.get("effectif") or []) if c]
    if effectif:
        stmt = stmt.where(func.coalesce(t.c.effectif_code, "NN").in_(effectif))

    commune = (f.get("commune") or "").strip().lower()
    if commune:
        stmt = stmt.where(func.lower(func.coalesce(t.c.commune, "")).like(f"%{commune}%"))

    categorie = f.get("categorie") or ""
    if categorie == "_none":
        stmt = stmt.where(or_(t.c.categorie_entreprise.is_(None), t.c.categorie_entreprise == ""))
    elif categorie:
        stmt = stmt.where(t.c.categorie_entreprise == categorie)

    nature = f.get("nature") or ""
    if nature:
        stmt = stmt.where(func.coalesce(t.c.nature, "entreprise") == nature)

    score_min = f.get("score_min")
    if score_min is not None:
        stmt = stmt.where(func.coalesce(t.c.score_pertinence, 0) >= score_min)

    travel_max_min = f.get("travel_max_min")
    if travel_max_min is not None and travel_origin:
        stmt = stmt.where(
            and_(
                func.lower(func.trim(func.coalesce(t.c.travel_origin, ""))) == travel_origin.strip().lower(),
                t.c.travel_without_tolls == 1,
                t.c.travel_duration_min.isnot(None),
                t.c.travel_duration_min <= travel_max_min,
            )
        )

    if f.get("siege_only"):
        stmt = stmt.where(t.c.est_siege == 1)
    if f.get("has_contact"):
        stmt = stmt.where(
            or_(
                func.trim(func.coalesce(t.c.contact_prenom, "")) != "",
                func.trim(func.coalesce(t.c.contact_nom, "")) != "",
            )
        )
    if f.get("has_email"):
        stmt = stmt.where(func.trim(func.coalesce(t.c.contact_email, "")) != "")
    if f.get("serpapi_scanned"):
        stmt = stmt.where(t.c.serpapi_scanned == 1)

    return stmt


def list_entreprises_page(
    f: dict[str, Any], travel_origin: str | None, page: int, per_page: int
) -> tuple[list[Any], int]:
    """Page filtrée d'entreprises + nombre total de résultats correspondants."""
    t = entreprises_table
    order = (func.coalesce(t.c.score_pertinence, 0).desc(), func.lower(t.c.denomination).asc())
    list_stmt = _apply_entreprise_filters(select(t), f, travel_origin).order_by(*order)
    count_stmt = _apply_entreprise_filters(select(func.count()).select_from(t), f, travel_origin)
    with get_engine().connect() as conn:
        total = conn.execute(count_stmt).scalar_one()
        rows = conn.execute(list_stmt.limit(per_page).offset((page - 1) * per_page)).mappings().all()
    return list(rows), total


def list_entreprises_lite(f: dict[str, Any], travel_origin: str | None) -> list[Any]:
    """Liste allégée (carte + calcul candidats trajet) : tous les résultats, pas de pagination."""
    t = entreprises_table
    cols = [
        t.c.siret,
        t.c.denomination,
        t.c.commune,
        t.c.status,
        t.c.score_pertinence,
        t.c.latitude,
        t.c.longitude,
        t.c.travel_origin,
        t.c.travel_without_tolls,
        t.c.travel_duration_min,
        t.c.travel_distance_km,
    ]
    order = (func.coalesce(t.c.score_pertinence, 0).desc(), func.lower(t.c.denomination).asc())
    stmt = _apply_entreprise_filters(select(*cols), f, travel_origin).order_by(*order)
    with get_engine().connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    return list(rows)


def entreprises_status_counts() -> dict[str, int]:
    """Compteurs globaux par statut (indépendants des filtres actifs), pour cartes/chips."""
    t = entreprises_table
    with get_engine().connect() as conn:
        total = conn.execute(select(func.count()).select_from(t)).scalar_one()
        status_rows = conn.execute(
            select(func.coalesce(t.c.status, "a_postuler").label("status"), func.count().label("n")).group_by(
                func.coalesce(t.c.status, "a_postuler")
            )
        ).all()
    counts = {"tous": total}
    for status, n in status_rows:
        counts[status] = n
    return counts


def naf_codes_used() -> list[dict[str, str]]:
    """Codes NAF distincts réellement présents en base (checklist filtre)."""
    t = entreprises_table
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(t.c.naf_code, func.max(t.c.naf_libelle))
            .where(t.c.naf_code.isnot(None), t.c.naf_code != "")
            .group_by(t.c.naf_code)
            .order_by(t.c.naf_code)
        ).all()
    return [{"code": code, "libelle": libelle or code} for code, libelle in rows]


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
