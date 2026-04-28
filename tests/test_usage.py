import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tests.auth_helpers import auth_headers, configure_auth_env

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import usage  # noqa: E402


client = TestClient(app)


class FakeQuery:
    def __init__(self, response: SimpleNamespace):
        self.response = response
        self.filters: list[tuple[str, object]] = []
        self.selected: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.upserted: tuple[dict[str, object], dict[str, object]] | None = None

    def select(self, *args: object, **kwargs: object) -> "FakeQuery":
        self.selected = (args, kwargs)
        return self

    def upsert(self, value: dict[str, object], **kwargs: object) -> "FakeQuery":
        self.upserted = (value, kwargs)
        return self

    def eq(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((key, value))
        return self

    def gte(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((f"{key}>=", value))
        return self

    def lt(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((f"{key}<", value))
        return self

    def in_(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((f"{key} in", value))
        return self

    def execute(self) -> SimpleNamespace:
        return self.response


class FakeSupabase:
    def __init__(self, responses: dict[str, list[SimpleNamespace]]):
        self.responses = responses
        self.queries: list[tuple[str, FakeQuery]] = []

    def table(self, name: str) -> FakeQuery:
        response = self.responses[name].pop(0)
        query = FakeQuery(response)
        self.queries.append((name, query))
        return query


async def run_sync_inline(func):
    return func()


class UsageRouteTests(unittest.TestCase):
    def test_starter_response_aggregates_usage_and_tool_calls(self) -> None:
        fake_supabase = FakeSupabase(
            {
                "users": [
                    SimpleNamespace(data=[]),
                    SimpleNamespace(data=[{"plan": "starter"}]),
                ],
                "usage": [
                    SimpleNamespace(
                        data=[
                            {
                                "input_tokens": 12_000,
                                "output_tokens": 3_000,
                                "cost_usd": "0.090000",
                            },
                            {
                                "input_tokens": 400,
                                "output_tokens": 800,
                                "cost_usd": "0.004200",
                            },
                        ]
                    )
                ],
                "sessions": [SimpleNamespace(data=[{"id": "session-id"}])],
                "tool_approvals": [SimpleNamespace(data=[], count=47)],
            }
        )

        with (
            patch.object(usage, "supabase_client", fake_supabase),
            patch.object(usage.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            response = client.get(
                "/usage",
                params={"month": 4, "year": 2026},
                headers=auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "period": {"month": 4, "year": 2026},
                "tokens": {"input": 12400, "output": 3800, "total": 16200},
                "cost_usd": 0.0942,
                "requests": 2,
                "tool_calls": {"used": 47, "limit": 500, "remaining": 453},
                "plan": "starter",
            },
        )
        self.assertEqual(
            fake_supabase.queries[2][1].filters,
            [
                ("user_id", "user-id"),
                ("created_at>=", "2026-04-01T00:00:00+00:00"),
                ("created_at<", "2026-05-01T00:00:00+00:00"),
            ],
        )

    def test_pro_response_returns_null_tool_call_limit(self) -> None:
        fake_supabase = FakeSupabase(
            {
                "users": [
                    SimpleNamespace(data=[]),
                    SimpleNamespace(data=[{"plan": "pro"}]),
                ],
                "usage": [SimpleNamespace(data=[])],
                "sessions": [SimpleNamespace(data=[{"id": "session-id"}])],
                "tool_approvals": [SimpleNamespace(data=[], count=12)],
            }
        )

        with (
            patch.object(usage, "supabase_client", fake_supabase),
            patch.object(usage.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            response = client.get(
                "/usage",
                params={"month": 4, "year": 2026},
                headers=auth_headers(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tool_calls"], {"used": 12, "limit": None, "remaining": None})

    def test_default_month_uses_current_utc_calendar_month(self) -> None:
        now = datetime.now(UTC)
        expected_start = datetime(now.year, now.month, 1, tzinfo=UTC).isoformat()
        if now.month == 12:
            expected_end = datetime(now.year + 1, 1, 1, tzinfo=UTC).isoformat()
        else:
            expected_end = datetime(now.year, now.month + 1, 1, tzinfo=UTC).isoformat()
        fake_supabase = FakeSupabase(
            {
                "users": [
                    SimpleNamespace(data=[]),
                    SimpleNamespace(data=[{"plan": "starter"}]),
                ],
                "usage": [SimpleNamespace(data=[])],
                "sessions": [SimpleNamespace(data=[])],
                "tool_approvals": [],
            }
        )

        with (
            patch.object(usage, "supabase_client", fake_supabase),
            patch.object(usage.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            response = client.get("/usage", headers=auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertIn(("created_at>=", expected_start), fake_supabase.queries[2][1].filters)
        self.assertIn(("created_at<", expected_end), fake_supabase.queries[2][1].filters)

    def test_usage_creates_default_user_row_when_missing(self) -> None:
        user_id = "23fa6a98-17a1-4e5d-b8ff-3f76f951f05e"
        fake_supabase = FakeSupabase(
            {
                "users": [
                    SimpleNamespace(data=[]),
                    SimpleNamespace(data=[{"plan": "starter"}]),
                ],
                "usage": [SimpleNamespace(data=[])],
                "sessions": [SimpleNamespace(data=[])],
                "tool_approvals": [],
            }
        )

        with (
            patch.object(usage, "supabase_client", fake_supabase),
            patch.object(usage.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            response = client.get(
                "/usage",
                params={"month": 4, "year": 2026},
                headers=auth_headers(user_id),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "period": {"month": 4, "year": 2026},
                "tokens": {"input": 0, "output": 0, "total": 0},
                "cost_usd": 0.0,
                "requests": 0,
                "tool_calls": {"used": 0, "limit": 500, "remaining": 500},
                "plan": "starter",
            },
        )
        self.assertEqual(fake_supabase.queries[0][0], "users")
        self.assertEqual(
            fake_supabase.queries[0][1].upserted,
            (
                {"id": user_id, "plan": "starter", "tool_call_count": 0},
                {"on_conflict": "id", "ignore_duplicates": True},
            ),
        )

    def test_invalid_month_returns_validation_error(self) -> None:
        response = client.get(
            "/usage",
            params={"month": 13, "year": 2026},
            headers=auth_headers(),
        )

        self.assertEqual(response.status_code, 422)

    def test_missing_authorization_returns_validation_error(self) -> None:
        response = client.get("/usage")

        self.assertEqual(response.status_code, 401)

    def test_malformed_token_returns_unauthorized(self) -> None:
        response = client.get(
            "/usage",
            headers={"Authorization": "Bearer not-a-jwt"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid token"})
