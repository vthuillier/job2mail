"""Exécution de scans longs en tâche de fond (thread) + suivi via SQLite.

gunicorn tourne en plusieurs process (workers) : ce ThreadPoolExecutor n'est
visible que du process qui a reçu le POST initial. C'est voulu — seul ce
worker exécute le job, les autres workers ne font que lire son statut en
base (même mécanisme que `_secret_key` dans app_factory.py), donc un GET
/api/jobs/<id> servi par n'importe quel worker reste cohérent.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from jobtomail import db

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2)


def create_job(user_id: int, kind: str, params: dict[str, Any] | None = None) -> str:
    job_id = uuid.uuid4().hex
    db.create_job_row(user_id, job_id, kind, params)
    return job_id


def submit_job(user_id: int, job_id: str, fn: Callable[[], dict[str, Any]], *, kind: str) -> None:
    def _run() -> None:
        db.update_job_row(user_id, job_id, status="running")
        try:
            result = fn()
        except Exception as e:
            logger.exception("Job %s (%s) échoué", job_id, kind)
            db.update_job_row(user_id, job_id, status="error", error=str(e))
        else:
            db.update_job_row(user_id, job_id, status="done", result=result)

    _executor.submit(_run)


def get_job(user_id: int, job_id: str) -> dict[str, Any] | None:
    return db.get_job_row(user_id, job_id)
