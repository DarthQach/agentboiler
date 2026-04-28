"""
Provider-agnostic JWT verification via JWKS.

To swap auth providers, set JWKS_URL to the provider's JWKS endpoint:
- Supabase: https://YOUR_PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json
- Clerk: https://YOUR_CLERK_DOMAIN/.well-known/jwks.json
- Auth0: https://YOUR_AUTH0_DOMAIN/.well-known/jwks.json

Production deployments should also set JWT_AUDIENCE and JWT_ISSUER. Without
those checks, a token signed by the trusted provider for a different app may
pass signature validation.
"""

from time import monotonic
from typing import Any

import httpx
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


JWKS_CACHE_TTL_SECONDS = 300
JWKS_FETCH_TIMEOUT_SECONDS = 5.0
ALLOWED_ASYMMETRIC_ALGORITHMS = {"RS256", "ES256", "EdDSA"}

security = HTTPBearer(auto_error=False)

_jwks_cache: dict[str, Any] | None = None
_jwks_cache_expires_at = 0.0


def _auth_service_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503, detail="Authentication service unavailable"
    )


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=401, detail="Invalid token")


def _token_expired() -> HTTPException:
    return HTTPException(status_code=401, detail="Token expired")


def _clear_jwks_cache() -> None:
    global _jwks_cache, _jwks_cache_expires_at
    _jwks_cache = None
    _jwks_cache_expires_at = 0.0


async def _fetch_jwks() -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=JWKS_FETCH_TIMEOUT_SECONDS) as client:
            response = await client.get(settings.jwks_url)
            response.raise_for_status()
            jwks = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise _auth_service_unavailable() from exc

    if not isinstance(jwks, dict) or not isinstance(jwks.get("keys"), list):
        raise _auth_service_unavailable()

    return jwks


async def _get_jwks(*, force_refresh: bool = False) -> dict[str, Any]:
    global _jwks_cache, _jwks_cache_expires_at

    now = monotonic()
    if not force_refresh and _jwks_cache is not None and now < _jwks_cache_expires_at:
        return _jwks_cache

    jwks = await _fetch_jwks()
    _jwks_cache = jwks
    _jwks_cache_expires_at = now + JWKS_CACHE_TTL_SECONDS
    return jwks


def _find_jwk(jwks: dict[str, Any], kid: str) -> dict[str, Any] | None:
    for key in jwks["keys"]:
        if isinstance(key, dict) and key.get("kid") == kid:
            return key
    return None


async def _get_signing_jwk(kid: str) -> dict[str, Any]:
    jwks = await _get_jwks()
    jwk = _find_jwk(jwks, kid)
    if jwk is not None:
        return jwk

    refreshed_jwks = await _get_jwks(force_refresh=True)
    refreshed_jwk = _find_jwk(refreshed_jwks, kid)
    if refreshed_jwk is None:
        raise _invalid_token()
    return refreshed_jwk


def _allowed_algorithms(jwk: dict[str, Any]) -> list[str]:
    jwk_alg = jwk.get("alg")
    if jwk_alg is None:
        return sorted(ALLOWED_ASYMMETRIC_ALGORITHMS)
    if jwk_alg not in ALLOWED_ASYMMETRIC_ALGORITHMS:
        raise _invalid_token()
    return [str(jwk_alg)]


def _decode_token(token: str, jwk: dict[str, Any]) -> dict[str, Any]:
    try:
        algorithms = _allowed_algorithms(jwk)
        key = jwt.PyJWK.from_dict(jwk).key
        payload = jwt.decode(
            token,
            key=key,
            algorithms=algorithms,
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"verify_aud": settings.jwt_audience is not None},
        )
    except jwt.ExpiredSignatureError as exc:
        raise _token_expired() from exc
    except jwt.PyJWTError as exc:
        raise _invalid_token() from exc

    if not isinstance(payload, dict):
        raise _invalid_token()
    return payload


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Not authenticated")

    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise _invalid_token() from exc

    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise _invalid_token()

    jwk = await _get_signing_jwk(kid)
    return _decode_token(token, jwk)
