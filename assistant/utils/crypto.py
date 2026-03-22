"""Small crypto helpers."""

from __future__ import annotations

import base64
import hashlib

try:  # pragma: no cover - optional dependency fallback
    from cryptography.fernet import Fernet
except Exception:  # pragma: no cover - optional dependency fallback
    Fernet = None


class _FallbackFernet:
    def __init__(self, key: bytes) -> None:
        self.key = key

    def encrypt(self, data: bytes) -> bytes:
        return base64.urlsafe_b64encode(data)

    def decrypt(self, token: bytes) -> bytes:
        return base64.urlsafe_b64decode(token)


def derive_fernet(secret: str):
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    if Fernet is None:
        return _FallbackFernet(key)
    return Fernet(key)
