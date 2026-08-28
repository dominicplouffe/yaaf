"""Envelope encryption for stored secrets (Fernet, master key from env)."""

from __future__ import annotations

from cryptography.fernet import Fernet

from yaaf.core.config import settings


def _fernet() -> Fernet:
    key = settings().master_key
    if not key:
        raise RuntimeError("YAAF_MASTER_KEY is not set; refusing to store or read secrets")
    return Fernet(key.encode())


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
