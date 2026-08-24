from __future__ import annotations

import json

import pytest

from jobtomail.services import cv_profile


def _write_pdf(path, text: str) -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(path, "wb") as f:
        writer.write(f)
    # pypdf ne fournit pas d'API simple pour écrire du texte réel dans une
    # page vierge sans dépendance supplémentaire — on teste extract_cv_text
    # sur ce PDF vide (texte vide, pas d'erreur) et on teste le contenu réel
    # via les fonctions qui prennent du texte directement.
    _ = text


def test_extract_cv_text_missing_file(tmp_path):
    assert cv_profile.extract_cv_text(tmp_path / "does-not-exist.pdf") is None


def test_extract_cv_text_empty_pdf_no_crash(tmp_path):
    pdf_path = tmp_path / "cv.pdf"
    _write_pdf(pdf_path, "")
    # une page vierge peut renvoyer None (pas de texte) — ne doit jamais lever
    result = cv_profile.extract_cv_text(pdf_path)
    assert result is None or isinstance(result, str)


def test_extract_keywords_heuristic_matches_known_skills():
    text = "Expérience en Python, Flask, Docker et gestion Agile Scrum."
    keywords = cv_profile._extract_keywords_heuristic(text)
    assert "python" in keywords
    assert "flask" in keywords
    assert "docker" in keywords


def test_extract_keywords_heuristic_no_match():
    assert cv_profile._extract_keywords_heuristic("texte sans rapport avec la tech") == []


def test_extract_cv_profile_missing_file_returns_none(temp_db, monkeypatch):
    monkeypatch.setattr(cv_profile, "CV_PATH", cv_profile.CV_PATH.parent / "does-not-exist.pdf")
    assert cv_profile.extract_cv_profile() is None


def test_extract_cv_profile_caches_by_mtime(temp_db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "cv.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(cv_profile, "CV_PATH", pdf_path)

    calls = {"n": 0}

    def fake_extract_text(path=None):
        calls["n"] += 1
        return "python flask docker"

    monkeypatch.setattr(cv_profile, "extract_cv_text", fake_extract_text)
    monkeypatch.setattr(cv_profile, "extract_cv_keywords_ollama", lambda text: None)

    first = cv_profile.extract_cv_profile()
    assert first["source"] == "heuristic"
    assert calls["n"] == 1

    second = cv_profile.extract_cv_profile()
    assert second == first
    assert calls["n"] == 1  # pas de ré-extraction, même mtime

    import os
    import time

    time.sleep(0.01)
    os.utime(pdf_path, None)  # change mtime
    third = cv_profile.extract_cv_profile()
    assert calls["n"] == 2


def test_extract_cv_profile_force_bypasses_cache(temp_db, monkeypatch, tmp_path):
    pdf_path = tmp_path / "cv.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(cv_profile, "CV_PATH", pdf_path)
    monkeypatch.setattr(cv_profile, "extract_cv_text", lambda path=None: "python")
    monkeypatch.setattr(cv_profile, "extract_cv_keywords_ollama", lambda text: None)

    cv_profile.extract_cv_profile()
    calls = {"n": 0}

    def fake_extract_text(path=None):
        calls["n"] += 1
        return "python"

    monkeypatch.setattr(cv_profile, "extract_cv_text", fake_extract_text)
    cv_profile.extract_cv_profile(force=True)
    assert calls["n"] == 1
