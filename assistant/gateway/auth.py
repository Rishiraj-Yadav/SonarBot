"""Gateway authentication helpers."""

import secrets


def authenticate_token(provided: str, expected: str) -> bool:
    return secrets.compare_digest(provided, expected)
