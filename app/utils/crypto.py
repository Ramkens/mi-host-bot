"""Symmetric encryption for secrets stored in the DB.

Uses cryptography.Fernet with a key derived from `settings.secret_key` so
admins can rotate the master secret by updating the env var (after which
existing ciphertexts must be re-encrypted; not in scope here).
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


def _key() -> bytes:
    """Derive a Fernet key (32 url-safe-b64 bytes) from SECRET_KEY."""
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt(plaintext: str) -> str:
    return Fernet(_key()).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str) -> str:
    try:
        return Fernet(_key()).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("ciphertext is invalid or SECRET_KEY changed") from exc
