"""Résolution du backend DB : env vars > fichier local > SQLite par défaut.

Le backend doit être résolvable AVANT toute connexion DB — cette config ne
peut donc pas vivre dans la table `config` (circulaire) ni dans `.env` seul
(l'assistant premier lancement doit pouvoir l'écrire sans redémarrage manuel).
"""

from __future__ import annotations

import json
import logging
import os
import stat
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
    DB_CONFIG_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)  # contient un mot de passe en clair


def delete_db_config_file() -> None:
    if DB_CONFIG_PATH.exists():
        DB_CONFIG_PATH.unlink()
