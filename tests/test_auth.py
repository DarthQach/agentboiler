import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from tests.auth_helpers import (
    TEST_JWKS,
    TEST_JWT_AUDIENCE,
    TEST_JWT_ISSUER,
    bearer_credentials,
    configure_auth_env,
    make_token,
)

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)

from app import auth  # noqa: E402
from app.auth import get_current_user  # noqa: E402


class AuthDependencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        auth._clear_jwks_cache()
        auth.settings.jwt_audience = None
        auth.settings.jwt_issuer = None

    async def test_valid_token_returns_payload(self) -> None:
        token = make_token({"sub": "user-id", "role": "authenticated"})

        with patch.object(auth, "_fetch_jwks", AsyncMock(return_value=TEST_JWKS)):
            payload = await get_current_user(bearer_credentials(token))

        self.assertEqual(payload["sub"], "user-id")
        self.assertEqual(payload["role"], "authenticated")

    async def test_jwk_without_alg_uses_asymmetric_allowlist(self) -> None:
        token = make_token()
        jwk_without_alg = {
            key: value for key, value in TEST_JWKS["keys"][0].items() if key != "alg"
        }

        with patch.object(
            auth, "_fetch_jwks", AsyncMock(return_value={"keys": [jwk_without_alg]})
        ):
            payload = await get_current_user(bearer_credentials(token))

        self.assertEqual(payload["sub"], "user-id")

    async def test_expired_token_raises_token_expired(self) -> None:
        token = make_token(expires_delta=timedelta(minutes=-5))

        with patch.object(auth, "_fetch_jwks", AsyncMock(return_value=TEST_JWKS)):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(bearer_credentials(token))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Token expired")

    async def test_malformed_token_raises_invalid_token(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await get_current_user(bearer_credentials("not-a-jwt"))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Invalid token")

    async def test_unknown_kid_forces_refresh_once_before_failing(self) -> None:
        token = make_token(kid="rotated-key")
        fetch_jwks = AsyncMock(return_value={"keys": []})

        with patch.object(auth, "_fetch_jwks", fetch_jwks):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(bearer_credentials(token))

        self.assertEqual(fetch_jwks.await_count, 2)
        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Invalid token")

    async def test_unknown_kid_uses_forced_refresh_when_key_rotated(self) -> None:
        token = make_token(kid="rotated-key")
        rotated_jwks = {
            "keys": [{**TEST_JWKS["keys"][0], "kid": "rotated-key"}],
        }
        fetch_jwks = AsyncMock(side_effect=[{"keys": []}, rotated_jwks])

        with patch.object(auth, "_fetch_jwks", fetch_jwks):
            payload = await get_current_user(bearer_credentials(token))

        self.assertEqual(fetch_jwks.await_count, 2)
        self.assertEqual(payload["sub"], "user-id")

    async def test_jwks_cache_prevents_repeated_fetches_within_ttl(self) -> None:
        token = make_token()
        fetch_jwks = AsyncMock(return_value=TEST_JWKS)

        with patch.object(auth, "_fetch_jwks", fetch_jwks):
            await get_current_user(bearer_credentials(token))
            await get_current_user(bearer_credentials(token))

        self.assertEqual(fetch_jwks.await_count, 1)

    async def test_jwks_fetch_timeout_returns_service_unavailable(self) -> None:
        token = make_token()
        fetch_jwks = AsyncMock(
            side_effect=HTTPException(
                status_code=503, detail="Authentication service unavailable"
            )
        )

        with patch.object(auth, "_fetch_jwks", fetch_jwks):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(bearer_credentials(token))

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(raised.exception.detail, "Authentication service unavailable")

    async def test_fetch_jwks_timeout_is_mapped_to_service_unavailable(self) -> None:
        class TimeoutClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def get(self, url):
                raise httpx.TimeoutException("timed out")

        with patch.object(auth.httpx, "AsyncClient", return_value=TimeoutClient()):
            with self.assertRaises(HTTPException) as raised:
                await auth._fetch_jwks()

        self.assertEqual(raised.exception.status_code, 503)

    async def test_disallowed_symmetric_algorithm_is_rejected(self) -> None:
        token = make_token(algorithm="HS256", key="test-secret-at-least-32-bytes-long")
        jwks = {"keys": [{**TEST_JWKS["keys"][0], "alg": "HS256"}]}

        with patch.object(auth, "_fetch_jwks", AsyncMock(return_value=jwks)):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(bearer_credentials(token))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Invalid token")

    async def test_audience_and_issuer_are_enforced_when_configured(self) -> None:
        auth.settings.jwt_audience = TEST_JWT_AUDIENCE
        auth.settings.jwt_issuer = TEST_JWT_ISSUER
        token = make_token({"aud": "wrong", "iss": TEST_JWT_ISSUER})

        with patch.object(auth, "_fetch_jwks", AsyncMock(return_value=TEST_JWKS)):
            with self.assertRaises(HTTPException) as raised:
                await get_current_user(bearer_credentials(token))

        self.assertEqual(raised.exception.status_code, 401)
        self.assertEqual(raised.exception.detail, "Invalid token")

    async def test_matching_audience_and_issuer_pass_when_configured(self) -> None:
        auth.settings.jwt_audience = TEST_JWT_AUDIENCE
        auth.settings.jwt_issuer = TEST_JWT_ISSUER
        token = make_token({"aud": TEST_JWT_AUDIENCE, "iss": TEST_JWT_ISSUER})

        with patch.object(auth, "_fetch_jwks", AsyncMock(return_value=TEST_JWKS)):
            payload = await get_current_user(bearer_credentials(token))

        self.assertEqual(payload["sub"], "user-id")
