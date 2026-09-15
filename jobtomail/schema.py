"""Définition SQLAlchemy Core des tables — dialect-agnostic (SQLite/Postgres/MariaDB)."""

from __future__ import annotations

from sqlalchemy import Column, Float, Index, Integer, MetaData, String, Table, Text, text

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

ix_entreprises_score_denom = Index(
    "ix_entreprises_score_denom", entreprises.c.score_pertinence, entreprises.c.denomination
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
