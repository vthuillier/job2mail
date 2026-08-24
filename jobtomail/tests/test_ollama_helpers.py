from __future__ import annotations

from jobtomail.services.ollama import _extract_json, _to_index


def test_extract_json_plain():
    assert _extract_json('{"linkedin": 1, "site": 2}') == {"linkedin": 1, "site": 2}


def test_extract_json_fenced_block():
    text = '```json\n{"linkedin": 1}\n```'
    assert _extract_json(text) == {"linkedin": 1}


def test_extract_json_embedded_in_prose():
    text = 'Voici le résultat : {"class": "offre"} merci.'
    assert _extract_json(text) == {"class": "offre"}


def test_extract_json_garbage_returns_none():
    assert _extract_json("pas de json ici") is None


def test_extract_json_empty_string():
    assert _extract_json("") is None


def test_to_index_valid_int():
    assert _to_index(2, 5) == 2


def test_to_index_out_of_range():
    assert _to_index(9, 5) is None


def test_to_index_bool_rejected():
    assert _to_index(True, 5) is None


def test_to_index_string_digit():
    assert _to_index("3", 5) == 3


def test_to_index_null_like_strings():
    for v in ("null", "none", "n/a", "0", "-"):
        assert _to_index(v, 5) is None


def test_to_index_none_value():
    assert _to_index(None, 5) is None
