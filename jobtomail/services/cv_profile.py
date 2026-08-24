"""Extraction de mots-clés depuis le CV (cv.pdf) pour affiner le scoring de pertinence."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobtomail import db
from jobtomail.constants import CV_PATH, MOTS_CLES_POSTE
from jobtomail.services.ollama import extract_cv_keywords_ollama

logger = logging.getLogger(__name__)

# Compétences/technologies courantes reconnues en fallback heuristique (sans LLM).
_HEURISTIC_SKILLS = frozenset(
    {
        "python", "javascript", "typescript", "java", "kotlin", "swift", "go", "rust",
        "c++", "c#", "php", "ruby", "sql", "html", "css",
        "react", "vue", "angular", "node", "django", "flask", "fastapi", "spring",
        "docker", "kubernetes", "terraform", "ansible", "aws", "azure", "gcp",
        "git", "linux", "postgresql", "mysql", "mongodb", "redis",
        "devops", "ci/cd", "microservices", "api rest", "agile", "scrum",
        "machine learning", "data science", "ia", "llm",
        *[m.lower() for m in MOTS_CLES_POSTE],
    }
)


def extract_cv_text(path: Path = CV_PATH) -> str | None:
    """Texte brut du CV, ou None si absent/illisible."""
    if not path.exists():
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
        return text or None
    except Exception:
        logger.exception("Échec extraction texte CV (%s)", path)
        return None


def _extract_keywords_heuristic(text: str) -> list[str]:
    lowered = text.lower()
    return sorted({skill for skill in _HEURISTIC_SKILLS if skill in lowered})


def extract_cv_profile(force: bool = False) -> dict[str, Any] | None:
    """
    Profil CV mis en cache (table config, clé "cv_profile").
    Re-extrait seulement si cv.pdf a changé (mtime) ou force=True.
    Retourne None si cv.pdf est absent.
    """
    if not CV_PATH.exists():
        return None

    mtime = CV_PATH.stat().st_mtime
    if not force:
        cached = db.get_config_value("cv_profile")
        if cached:
            try:
                import json

                parsed = json.loads(cached)
                if parsed.get("cv_mtime") == mtime:
                    return parsed
            except json.JSONDecodeError:
                pass

    text = extract_cv_text()
    if not text:
        return None

    keywords = extract_cv_keywords_ollama(text)
    source = "ollama"
    if not keywords:
        keywords = _extract_keywords_heuristic(text)
        source = "heuristic"

    profile = {
        "keywords": keywords,
        "source": source,
        "extracted_at": datetime.now(UTC).isoformat(),
        "cv_mtime": mtime,
    }
    db.set_config_values({"cv_profile": profile})
    logger.info("Profil CV extrait (%s) — %d mot(s)-clé(s)", source, len(keywords))
    return profile
