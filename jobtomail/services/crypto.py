"""Chiffrement au repos des secrets utilisateur (tokens OAuth, clés API)."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    key = os.environ["APP_ENCRYPTION_KEY"]  # generate via Fernet.generate_key()
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
