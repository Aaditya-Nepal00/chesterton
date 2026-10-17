import hashlib
import hmac

import pytest

from app.github.webhooks import verify_signature

SECRET = "test-secret"
BODY = b'{"action":"closed","pull_request":{"merged":true}}'


def _sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature_passes():
    assert verify_signature(SECRET, BODY, _sign(BODY))


def test_tampered_body_fails():
    assert not verify_signature(SECRET, BODY + b" ", _sign(BODY))


def test_wrong_secret_fails():
    assert not verify_signature(SECRET, BODY, _sign(BODY, "other-secret"))


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "sha256=",
        "sha1=" + hashlib.sha1(BODY).hexdigest(),
        "deadbeef",
        "sha256=not-hex-at-all",
    ],
)
def test_missing_or_malformed_header_fails(header):
    assert not verify_signature(SECRET, BODY, header)
