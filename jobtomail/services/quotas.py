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
