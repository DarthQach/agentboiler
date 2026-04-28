import json
import os
from datetime import UTC, datetime, timedelta
from time import monotonic

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.security import HTTPAuthorizationCredentials


TEST_KEY_ID = "test-key-id"
TEST_JWKS_URL = "https://auth.test/.well-known/jwks.json"
TEST_JWT_AUDIENCE = "agentboiler-test"
TEST_JWT_ISSUER = "https://issuer.test/"

TEST_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TEST_PUBLIC_JWK = json.loads(
    jwt.algorithms.RSAAlgorithm.to_jwk(TEST_PRIVATE_KEY.public_key())
)
TEST_PUBLIC_JWK.update({"kid": TEST_KEY_ID, "alg": "RS256", "use": "sig"})
TEST_JWKS = {"keys": [TEST_PUBLIC_JWK]}


def configure_auth_env() -> None:
    os.environ.setdefault("JWKS_URL", TEST_JWKS_URL)


def make_token(
    payload: dict[str, object] | None = None,
    *,
    kid: str = TEST_KEY_ID,
    algorithm: str = "RS256",
    expires_delta: timedelta = timedelta(minutes=5),
    include_exp: bool = True,
    key: object = TEST_PRIVATE_KEY,
) -> str:
    claims: dict[str, object] = {
        "sub": "user-id",
        "exp": datetime.now(UTC) + expires_delta,
    }
    if not include_exp:
        claims.pop("exp")
    if payload:
        claims.update(payload)

    return jwt.encode(claims, key, algorithm=algorithm, headers={"kid": kid})


def bearer_credentials(token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def seed_jwks_cache() -> None:
    from app import auth

    auth._jwks_cache = TEST_JWKS
    auth._jwks_cache_expires_at = monotonic() + auth.JWKS_CACHE_TTL_SECONDS


def auth_headers(user_id: str = "user-id") -> dict[str, str]:
    seed_jwks_cache()
    token = make_token({"sub": user_id})
    return {"Authorization": f"Bearer {token}"}
