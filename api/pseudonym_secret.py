"""Machine credential-store access for deterministic cross-machine pseudonyms."""
from __future__ import annotations

import hashlib
import secrets

import keyring


SERVICE = "CanvasExpert"
USERNAME = "pseudonym-secret"


def _encode(secret: bytes) -> str:
    return secret.hex()


def _decode(value: str | None) -> bytes | None:
    if not value:
        return None
    try:
        secret = bytes.fromhex(value)
    except ValueError:
        return None
    return secret if len(secret) == 32 else None


def get_secret() -> bytes | None:
    """Return the configured 32-byte secret, without creating one."""
    return _decode(keyring.get_password(SERVICE, USERNAME))


def ensure_primary_secret() -> bytes:
    """Create the secret once on the machine that initializes the vault seed."""
    existing = get_secret()
    if existing is not None:
        return existing
    secret = secrets.token_bytes(32)
    keyring.set_password(SERVICE, USERNAME, _encode(secret))
    return secret


def set_secret(value: str) -> dict:
    """Store a teacher-supplied 64-character hex secret on another machine."""
    secret = _decode(value.strip() if isinstance(value, str) else None)
    if secret is None:
        return {"ok": False, "error": "secret_must_be_32_bytes_hex"}
    keyring.set_password(SERVICE, USERNAME, _encode(secret))
    return {"ok": True, "fingerprint": fingerprint(secret)}


def fingerprint(secret: bytes | None = None) -> str:
    value = secret if secret is not None else get_secret()
    if value is None:
        return ""
    return hashlib.sha256(value).hexdigest()[:6].upper()
