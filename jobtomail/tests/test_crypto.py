from __future__ import annotations

from cryptography.fernet import Fernet

from jobtomail.services import crypto


def _set_key(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENCRYPTION_KEY", Fernet.generate_key().decode())


def test_encrypt_decrypt_roundtrip(monkeypatch):
    _set_key(monkeypatch)
    plaintext = "1//0gSomeRefreshTokenValue"
    ciphertext = crypto.encrypt(plaintext)
    assert ciphertext != plaintext
    assert crypto.decrypt(ciphertext) == plaintext


def test_encrypt_output_differs_from_plaintext_and_is_not_trivially_reversible(monkeypatch):
    _set_key(monkeypatch)
    plaintext = "super-secret-refresh-token"
    ciphertext = crypto.encrypt(plaintext)
    assert plaintext not in ciphertext
