"""Symmetric encryption for secrets stored in the database (OAuth tokens)."""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from config import get_settings


def _fernet(secret: str | None = None) -> Fernet:
    secret = secret if secret is not None else get_settings().app_secret_key
    if not secret or len(secret) < 32:
        raise RuntimeError("APP_SECRET_KEY must be set (32+ characters). See .env.example.")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt(value: str | None, secret: str | None = None) -> str | None:
    if value is None:
        return None
    return _fernet(secret).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str | None, secret: str | None = None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet(secret).decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("Stored token cannot be decrypted (APP_SECRET_KEY changed?). Reconnect Jobber.") from exc
