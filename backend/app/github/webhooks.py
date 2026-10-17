"""GitHub webhook signature verification (X-Hub-Signature-256)."""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_signature(secret: str, body_bytes: bytes, header_value: str | None) -> bool:
    """Return True if header_value is a valid HMAC-SHA256 signature of body_bytes."""
    if not secret or not header_value or not header_value.startswith(_PREFIX):
        return False
    received = header_value[len(_PREFIX) :]
    expected = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received)
