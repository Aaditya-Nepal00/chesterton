import asyncio

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import Settings
from app.github.auth import AppAuth, TokenSelector, make_app_jwt

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


def test_app_jwt_claims():
    token = make_app_jwt("123", PEM, now=1_000_000)
    claims = jwt.decode(
        token,
        KEY.public_key(),
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims == {"iat": 1_000_000 - 60, "exp": 1_000_000 + 540, "iss": "123"}


def _mock_github(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(f"{request.method} {request.url.path}")
        if request.url.path.endswith("/installation"):
            return httpx.Response(200, json={"id": 77})
        return httpx.Response(
            201,
            json={
                "token": f"tok{len(calls)}",
                "expires_at": "2026-10-17T11:00:00Z",
                "permissions": {"contents": "read"},
            },
        )

    return httpx.AsyncClient(
        base_url="https://api.github.com", transport=httpx.MockTransport(handler)
    )


def test_installation_token_is_cached_until_a_minute_before_expiry():
    expiry = 1_792_234_800  # 2026-10-17T11:00:00Z
    now = [expiry - 600]
    auth = AppAuth("123", PEM, clock=lambda: now[0])
    calls: list[str] = []

    async def run():
        async with _mock_github(calls) as http:
            first = await auth.installation_token("acme/demo", http)
            again = await auth.installation_token("ACME/demo", http)
            now[0] = expiry - 30  # inside the 60s refresh margin
            refreshed = await auth.installation_token("acme/demo", http)
            return first, again, refreshed

    first, again, refreshed = asyncio.run(run())
    assert first == again and refreshed != first
    assert calls.count("POST /app/installations/77/access_tokens") == 2


def test_token_selector_uses_app_only_for_demo_repo():
    settings = Settings(github_token="pat", github_demo_repo="acme/demo", _env_file=None)
    sel = TokenSelector(settings, AppAuth("123", PEM))
    assert sel.uses_app("acme/demo") and not sel.uses_app("encode/httpx")
    assert asyncio.run(sel.token_for("encode/httpx")) == "pat"
    assert not TokenSelector(settings, None).uses_app("acme/demo")
