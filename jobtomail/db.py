"""Accès SQLite et configuration persistée."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from typing import Any

from jobtomail.constants import DB_PATH, DEFAULT_NAF_CODES

logger = logging.getLogger(__name__)

_EXTRA_COLUMNS = {
    "est_siege": "INTEGER DEFAULT 0",
    "categorie_entreprise": "TEXT",
    "categorie_juridique": "TEXT",
    "nature": "TEXT DEFAULT 'entreprise'",
    "score_pertinence": "REAL DEFAULT 0",
    "latitude": "REAL",
    "longitude": "REAL",
    "geocode_failed": "INTEGER DEFAULT 0",
    "dirigeants_scanned": "INTEGER DEFAULT 0",
    "email_message_id": "TEXT",
    "email_subject": "TEXT",
    "email_body": "TEXT",
    "email_sent_at": "TEXT",
    "relance_count": "INTEGER DEFAULT 0",
    "last_relance_at": "TEXT",
    "reply_message_id": "TEXT",
    "reply_class": "TEXT",
    "reply_classified_at": "TEXT",
    "reply_from": "TEXT",
    "reply_subject": "TEXT",
    "contact_source": "TEXT",
    "email_hunter_score": "INTEGER",
    "email_quality": "TEXT",
    "email_quality_note": "TEXT",
    "accroche": "TEXT",
    "entretien_date": "TEXT",
    "entretien_next_step": "TEXT",
    "entretien_rappel_at": "TEXT",
    "travel_origin": "TEXT",
    "travel_duration_min": "REAL",
    "travel_distance_km": "REAL",
    "travel_without_tolls": "INTEGER DEFAULT 0",
    "travel_updated_at": "TEXT",
}


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(entreprises)").fetchall()}
    for col, typedef in _EXTRA_COLUMNS.items():
        if col not in existing:
            logger.info("Migration SQLite : ajout colonne entreprises.%s", col)
            conn.execute(f"ALTER TABLE entreprises ADD COLUMN {col} {typedef}")


def init_db() -> None:
    logger.info("Initialisation de la base SQLite : %s", DB_PATH)
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS entreprises (
            siret TEXT PRIMARY KEY,
            siren TEXT,
            denomination TEXT NOT NULL,
            adresse TEXT,
            commune TEXT,
            effectif_code TEXT,
            effectif_libelle TEXT,
            naf_code TEXT,
            naf_libelle TEXT,
            date_creation TEXT,
            est_siege INTEGER DEFAULT 0,
            categorie_entreprise TEXT,
            score_pertinence REAL DEFAULT 0,
            site_web TEXT,
            linkedin_company TEXT,
            serpapi_scanned INTEGER DEFAULT 0,
            contact_prenom TEXT,
            contact_nom TEXT,
            contact_genre TEXT,
            contact_poste TEXT,
            contact_email TEXT,
            contact_linkedin TEXT,
            status TEXT DEFAULT 'a_postuler',
            notes TEXT,
            dirigeants_scanned INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_replies (
            message_id TEXT PRIMARY KEY,
            siret TEXT,
            classification TEXT,
            processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    _migrate(conn)
    conn.commit()
    conn.close()
    logger.info("Base SQLite prête")


def get_config_value(key: str, default: str = "") -> str:
    conn = get_db()
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    conn.close()
    if row and row["value"]:
        return row["value"]
    return default


def set_config_values(data: dict[str, Any]) -> None:
    conn = get_db()
    cursor = conn.cursor()
    for k, v in data.items():
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        cursor.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)",
            (k, str(v) if v is not None else ""),
        )
    conn.commit()
    conn.close()
    logger.info("Config mise à jour (%d clé(s)) : %s", len(data), ", ".join(data.keys()))


def get_all_config() -> dict[str, str]:
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM config").fetchall()
    conn.close()
    return {row["key"]: row["value"] for row in rows}


def env_or_config(key: str, *aliases: str) -> str:
    """Lit d'abord .env, puis la config SQLite (avec alias éventuels)."""
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


def list_entreprises() -> list[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT * FROM entreprises
        ORDER BY COALESCE(score_pertinence, 0) DESC, denomination COLLATE NOCASE ASC
        """
    ).fetchall()
    conn.close()
    return list(rows)


def get_entreprise(siret: str) -> sqlite3.Row | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM entreprises WHERE siret = ?", (siret,)).fetchone()
    conn.close()
    return row


def update_entreprise(siret: str, fields: dict[str, Any]) -> bool:
    existing = get_entreprise(siret)
    if not existing:
        return False
    keys = list(fields.keys())
    values = [fields[k] for k in keys]
    query = "UPDATE entreprises SET " + ", ".join(f"{k} = ?" for k in keys) + " WHERE siret = ?"
    conn = get_db()
    conn.execute(query, values + [siret])
    conn.commit()
    conn.close()
    logger.info("Entreprise %s mise à jour (%s)", siret, ", ".join(keys))
    return True


def delete_entreprise(siret: str) -> None:
    conn = get_db()
    conn.execute("DELETE FROM entreprises WHERE siret = ?", (siret,))
    conn.commit()
    conn.close()
    logger.info("Entreprise %s supprimée", siret)


def reset_entreprises() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM entreprises")
    count = cursor.fetchone()["n"]
    cursor.execute("DELETE FROM entreprises")
    conn.commit()
    conn.close()
    logger.warning("Table entreprises vidée (%d ligne(s))", count)
    return count


def insert_entreprise_ignore(row: dict[str, Any]) -> bool:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR IGNORE INTO entreprises (
            siret, siren, denomination, adresse, commune,
            effectif_code, effectif_libelle, naf_code, naf_libelle, date_creation,
            est_siege, categorie_entreprise, categorie_juridique, nature,
            score_pertinence, latitude, longitude
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["siret"],
            row.get("siren"),
            row["denomination"],
            row.get("adresse"),
            row.get("commune"),
            row.get("effectif_code"),
            row.get("effectif_libelle"),
            row.get("naf_code"),
            row.get("naf_libelle"),
            row.get("date_creation"),
            1 if row.get("est_siege") else 0,
            row.get("categorie_entreprise"),
            row.get("categorie_juridique"),
            row.get("nature") or "entreprise",
            row.get("score_pertinence") or 0,
            row.get("latitude"),
            row.get("longitude"),
        ),
    )
    inserted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return inserted


def insert_entreprise(row: dict[str, Any]) -> None:
    """Insère une entreprise. Lève sqlite3.IntegrityError si le SIRET existe déjà."""
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO entreprises (
                siret, siren, denomination, adresse, commune,
                effectif_code, effectif_libelle, naf_code, naf_libelle, date_creation,
                est_siege, categorie_entreprise, categorie_juridique, nature,
                score_pertinence, latitude, longitude, site_web, linkedin_company, notes, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["siret"],
                row.get("siren"),
                row["denomination"],
                row.get("adresse"),
                row.get("commune"),
                row.get("effectif_code"),
                row.get("effectif_libelle"),
                row.get("naf_code"),
                row.get("naf_libelle"),
                row.get("date_creation"),
                1 if row.get("est_siege") else 0,
                row.get("categorie_entreprise"),
                row.get("categorie_juridique"),
                row.get("nature") or "entreprise",
                row.get("score_pertinence") or 0,
                row.get("latitude"),
                row.get("longitude"),
                row.get("site_web") or None,
                row.get("linkedin_company") or None,
                row.get("notes") or None,
                row.get("status") or "a_postuler",
            ),
        )
        conn.commit()
        logger.info("Entreprise ajoutée manuellement : %s (%s)", row["denomination"], row["siret"])
    finally:
        conn.close()


def replace_entreprises_cleaned(
    keep_rows: list[dict[str, Any]],
    keep_sirets: set[str],
) -> dict[str, int]:
    """Supprime les SIRET non retenus et met à jour score / notes des gardés."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT siret FROM entreprises")
    all_sirets = {r["siret"] for r in cursor.fetchall()}
    to_delete = all_sirets - keep_sirets
    for siret in to_delete:
        cursor.execute("DELETE FROM entreprises WHERE siret = ?", (siret,))

    updated = 0
    for row in keep_rows:
        cursor.execute(
            """
            UPDATE entreprises
            SET score_pertinence = ?,
                notes = COALESCE(?, notes),
                est_siege = ?,
                categorie_entreprise = COALESCE(?, categorie_entreprise),
                categorie_juridique = COALESCE(?, categorie_juridique),
                nature = COALESCE(?, nature)
            WHERE siret = ?
            """,
            (
                row.get("score_pertinence") or 0,
                row.get("notes"),
                1 if row.get("est_siege") else 0,
                row.get("categorie_entreprise"),
                row.get("categorie_juridique"),
                row.get("nature"),
                row["siret"],
            ),
        )
        updated += cursor.rowcount

    conn.commit()
    conn.close()
    logger.info("Clean DB — deleted=%d updated=%d", len(to_delete), updated)
    return {"deleted": len(to_delete), "updated": updated}


def mark_serpapi_result(
    siret: str,
    site_web: str | None,
    linkedin_company: str | None,
    scanned: bool = True,
) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET site_web = COALESCE(NULLIF(?, ''), site_web),
            linkedin_company = COALESCE(NULLIF(?, ''), linkedin_company),
            serpapi_scanned = ?
        WHERE siret = ?
        """,
        (site_web or "", linkedin_company or "", 1 if scanned else 0, siret),
    )
    conn.commit()
    conn.close()


def mark_serpapi_scanned_only(siret: str) -> None:
    conn = get_db()
    conn.execute("UPDATE entreprises SET serpapi_scanned = 1 WHERE siret = ?", (siret,))
    conn.commit()
    conn.close()


def entreprises_to_serpapi(sirets: list[str] | None = None) -> list[sqlite3.Row]:
    conn = get_db()
    if sirets:
        placeholders = ",".join("?" * len(sirets))
        rows = conn.execute(
            f"SELECT siret, denomination, commune FROM entreprises WHERE siret IN ({placeholders})",
            sirets,
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT siret, denomination, commune FROM entreprises
            WHERE serpapi_scanned = 0
            ORDER BY COALESCE(score_pertinence, 0) DESC, denomination COLLATE NOCASE
            """
        ).fetchall()
    conn.close()
    return list(rows)


def entreprises_sans_coords(limit: int = 40) -> list[sqlite3.Row]:
    conn = get_db()
    rows = conn.execute(
        """
        SELECT siret, adresse, commune FROM entreprises
        WHERE (latitude IS NULL OR longitude IS NULL)
          AND COALESCE(geocode_failed, 0) = 0
        ORDER BY COALESCE(score_pertinence, 0) DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return list(rows)


def count_entreprises_sans_coords() -> int:
    conn = get_db()
    n = conn.execute(
        """
        SELECT COUNT(*) AS n FROM entreprises
        WHERE (latitude IS NULL OR longitude IS NULL)
          AND COALESCE(geocode_failed, 0) = 0
        """
    ).fetchone()["n"]
    conn.close()
    return n


def mark_geocode_failed(siret: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE entreprises SET geocode_failed = 1 WHERE siret = ?",
        (siret,),
    )
    conn.commit()
    conn.close()


def update_entreprise_coords(siret: str, latitude: float, longitude: float) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE entreprises SET latitude = ?, longitude = ? WHERE siret = ?",
        (latitude, longitude, siret),
    )
    conn.commit()
    conn.close()


def update_entreprise_travel(
    siret: str,
    *,
    origin: str,
    duration_min: float,
    distance_km: float,
    without_tolls: bool = True,
) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET travel_origin = ?,
            travel_duration_min = ?,
            travel_distance_km = ?,
            travel_without_tolls = ?,
            travel_updated_at = CURRENT_TIMESTAMP
        WHERE siret = ?
        """,
        (origin, duration_min, distance_km, 1 if without_tolls else 0, siret),
    )
    conn.commit()
    conn.close()


def entreprises_to_dirigeants(
    sirets: list[str] | None = None,
    force: bool = False,
) -> list[sqlite3.Row]:
    conn = get_db()
    if sirets:
        placeholders = ",".join("?" * len(sirets))
        rows = conn.execute(
            f"""
            SELECT siret, siren, denomination, contact_prenom, contact_nom, dirigeants_scanned
            FROM entreprises WHERE siret IN ({placeholders})
            """,
            sirets,
        ).fetchall()
    elif force:
        rows = conn.execute(
            """
            SELECT siret, siren, denomination, contact_prenom, contact_nom, dirigeants_scanned
            FROM entreprises
            ORDER BY COALESCE(score_pertinence, 0) DESC, denomination COLLATE NOCASE
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT siret, siren, denomination, contact_prenom, contact_nom, dirigeants_scanned
            FROM entreprises
            WHERE COALESCE(dirigeants_scanned, 0) = 0
              AND (contact_prenom IS NULL OR TRIM(contact_prenom) = '')
              AND (contact_nom IS NULL OR TRIM(contact_nom) = '')
            ORDER BY COALESCE(score_pertinence, 0) DESC, denomination COLLATE NOCASE
            """
        ).fetchall()
    conn.close()
    return list(rows)


def apply_dirigeant(siret: str, fields: dict[str, Any]) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET contact_prenom = ?,
            contact_nom = ?,
            contact_poste = COALESCE(NULLIF(?, ''), contact_poste),
            contact_source = COALESCE(NULLIF(?, ''), 'dirigeant'),
            dirigeants_scanned = 1
        WHERE siret = ?
        """,
        (
            fields.get("contact_prenom") or "",
            fields.get("contact_nom") or "",
            fields.get("contact_poste") or "",
            fields.get("contact_source") or "dirigeant",
            siret,
        ),
    )
    conn.commit()
    conn.close()


def apply_contact(siret: str, fields: dict[str, Any]) -> None:
    """Met à jour le contact (RH/tech/dirigeant) sans toucher dirigeants_scanned sauf demandé."""
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET contact_prenom = COALESCE(NULLIF(?, ''), contact_prenom),
            contact_nom = COALESCE(NULLIF(?, ''), contact_nom),
            contact_poste = COALESCE(NULLIF(?, ''), contact_poste),
            contact_linkedin = COALESCE(NULLIF(?, ''), contact_linkedin),
            contact_source = COALESCE(NULLIF(?, ''), contact_source)
        WHERE siret = ?
        """,
        (
            fields.get("contact_prenom") or "",
            fields.get("contact_nom") or "",
            fields.get("contact_poste") or "",
            fields.get("contact_linkedin") or "",
            fields.get("contact_source") or "",
            siret,
        ),
    )
    conn.commit()
    conn.close()


def apply_email_quality(
    siret: str,
    *,
    email: str,
    hunter_score: int | None,
    quality: str,
    note: str = "",
) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET contact_email = COALESCE(NULLIF(?, ''), contact_email),
            email_hunter_score = ?,
            email_quality = ?,
            email_quality_note = ?
        WHERE siret = ?
        """,
        (email, hunter_score, quality, note, siret),
    )
    conn.commit()
    conn.close()


def mark_dirigeants_scanned(siret: str) -> None:
    conn = get_db()
    conn.execute(
        "UPDATE entreprises SET dirigeants_scanned = 1 WHERE siret = ?",
        (siret,),
    )
    conn.commit()
    conn.close()


def mark_email_sent(
    siret: str,
    email: str,
    *,
    message_id: str | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET status = 'postule',
            contact_email = COALESCE(NULLIF(?, ''), contact_email),
            email_message_id = COALESCE(NULLIF(?, ''), email_message_id),
            email_subject = COALESCE(NULLIF(?, ''), email_subject),
            email_body = COALESCE(NULLIF(?, ''), email_body),
            email_sent_at = CURRENT_TIMESTAMP
        WHERE siret = ?
        """,
        (email, message_id or "", subject or "", body or "", siret),
    )
    conn.commit()
    conn.close()
    logger.info("Statut 'postule' pour %s (email=%s, msg-id=%s)", siret, email, message_id)


def mark_relance_sent(
    siret: str,
    *,
    message_id: str | None = None,
    body: str | None = None,
) -> None:
    conn = get_db()
    conn.execute(
        """
        UPDATE entreprises
        SET status = 'relance',
            relance_count = COALESCE(relance_count, 0) + 1,
            last_relance_at = CURRENT_TIMESTAMP,
            email_message_id = COALESCE(NULLIF(?, ''), email_message_id),
            email_body = COALESCE(NULLIF(?, ''), email_body)
        WHERE siret = ?
        """,
        (message_id or "", body or "", siret),
    )
    conn.commit()
    conn.close()
    logger.info("Relance enregistrée pour %s (msg-id=%s)", siret, message_id)


def list_candidatures_en_attente() -> list[dict[str, Any]]:
    """Entreprises en attente de réponse (postulé / relancé), avec infos mail."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT siret, denomination, status, contact_email,
               email_message_id, email_subject, email_sent_at,
               reply_message_id, reply_class, relance_count, last_relance_at
        FROM entreprises
        WHERE status IN ('postule', 'relance')
          AND email_sent_at IS NOT NULL
        ORDER BY email_sent_at DESC
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_entretiens_a_suivre() -> list[dict[str, Any]]:
    """Entreprises en entretien / offre avec date ou rappel renseigné."""
    conn = get_db()
    rows = conn.execute(
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
          denomination COLLATE NOCASE
        """
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def list_processed_reply_ids() -> set[str]:
    conn = get_db()
    rows = conn.execute("SELECT message_id FROM processed_replies").fetchall()
    conn.close()
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
    note_line = (
        f"[Réponse auto {classification}] "
        f"de {from_email or '?'} — {(subject or '')[:80]}"
    )
    if reason:
        note_line += f"\n→ {reason.strip()}"
    if excerpt:
        note_line += f"\n{(excerpt or '')[:200]}"

    existing_notes = (ent["notes"] or "").strip()
    notes = f"{existing_notes}\n{note_line}".strip() if existing_notes else note_line

    conn = get_db()
    if apply_status:
        conn.execute(
            """
            UPDATE entreprises
            SET status = ?,
                reply_message_id = ?,
                reply_class = ?,
                reply_classified_at = CURRENT_TIMESTAMP,
                reply_from = ?,
                reply_subject = ?,
                notes = ?
            WHERE siret = ?
            """,
            (classification, message_id, classification, from_email, subject, notes, siret),
        )
    else:
        conn.execute(
            """
            UPDATE entreprises
            SET reply_message_id = ?,
                reply_class = ?,
                reply_classified_at = CURRENT_TIMESTAMP,
                reply_from = ?,
                reply_subject = ?,
                notes = ?
            WHERE siret = ?
            """,
            (message_id, classification, from_email, subject, notes, siret),
        )

    conn.execute(
        """
        INSERT OR REPLACE INTO processed_replies (message_id, siret, classification)
        VALUES (?, ?, ?)
        """,
        (message_id, siret, classification),
    )
    conn.commit()
    conn.close()
    logger.info(
        "Réponse classée pour %s → %s (status_updated=%s)",
        siret,
        classification,
        apply_status,
    )
