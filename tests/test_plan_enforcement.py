import os
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from tests.auth_helpers import configure_auth_env

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)

from app.middleware import plan_enforcement  # noqa: E402


class FakeUsersQuery:
    def __init__(self, response: SimpleNamespace):
        self.response = response
        self.filters: list[tuple[str, object]] = []
        self.selected: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.updated: dict[str, object] | None = None

    def select(self, *args: object, **kwargs: object) -> "FakeUsersQuery":
        self.selected = (args, kwargs)
        return self

    def update(self, value: dict[str, object]) -> "FakeUsersQuery":
        self.updated = value
        return self

    def eq(self, key: str, value: object) -> "FakeUsersQuery":
        self.filters.append((key, value))
        return self

    def single(self) -> "FakeUsersQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return self.response


class FakeSupabase:
    def __init__(
        self,
        responses: list[SimpleNamespace],
        execute_error: Exception | None = None,
    ):
        self.responses = responses
        self.execute_error = execute_error
        self.queries: list[FakeUsersQuery] = []

    def table(self, name: str) -> FakeUsersQuery:
        assert name == "users"
        response = self.responses.pop(0) if self.responses else SimpleNamespace(data=[])
        query = FakeUsersQuery(response)
        if self.execute_error is not None:
            query.execute = lambda: (_ for _ in ()).throw(self.execute_error)
        self.queries.append(query)
        return query


async def run_sync_inline(func):
    return func()


def user_row(
    plan: str,
    count: int,
    reset_at: datetime | None = None,
) -> dict[str, object]:
    return {
        "plan": plan,
        "tool_call_count": count,
        "tool_call_reset_at": (reset_at or datetime.now(UTC)).isoformat(),
    }


class PlanEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_free_plan_is_blocked(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data=user_row("free", 0))]
        )

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            with self.assertRaises(HTTPException) as raised:
                await plan_enforcement.check_tool_call_limit("user-id")

        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(
            raised.exception.detail,
            {"error": "free_plan", "message": "Upgrade to call tools"},
        )

    async def test_missing_user_is_blocked_as_free_plan(self) -> None:
        fake_supabase = FakeSupabase([SimpleNamespace(data=[])])

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            with self.assertRaises(HTTPException) as raised:
                await plan_enforcement.check_tool_call_limit("user-id")

        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(
            raised.exception.detail,
            {"error": "free_plan", "message": "Upgrade to call tools"},
        )

    async def test_starter_at_limit_is_blocked(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data=user_row("starter", 500))]
        )

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            with self.assertRaises(HTTPException) as raised:
                await plan_enforcement.check_tool_call_limit("user-id")

        self.assertEqual(raised.exception.status_code, 402)
        self.assertEqual(
            raised.exception.detail,
            {
                "error": "limit_reached",
                "message": "Monthly tool call limit reached. Upgrade to Pro.",
            },
        )

    async def test_starter_under_limit_passes(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data=user_row("starter", 499))]
        )

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            await plan_enforcement.check_tool_call_limit("user-id")

    async def test_pro_always_passes(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data=user_row("pro", 10_000))]
        )

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            await plan_enforcement.check_tool_call_limit("user-id")

    async def test_elapsed_reset_window_resets_before_checking(self) -> None:
        stale_reset = datetime.now(UTC) - timedelta(days=31)
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data=user_row("starter", 500, stale_reset))]
        )

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            await plan_enforcement.check_tool_call_limit("user-id")

        self.assertEqual(fake_supabase.queries[1].updated["tool_call_count"], 0)
        self.assertEqual(fake_supabase.queries[1].filters, [("id", "user-id")])

    async def test_supabase_read_failure_fails_open(self) -> None:
        fake_supabase = FakeSupabase([], execute_error=RuntimeError("db down"))

        with (
            patch.object(plan_enforcement, "supabase_client", fake_supabase),
            patch.object(plan_enforcement.anyio.to_thread, "run_sync", run_sync_inline),
            patch.object(plan_enforcement.logger, "exception") as log_exception,
        ):
            await plan_enforcement.check_tool_call_limit("user-id")

        log_exception.assert_called_once_with(
            "Failed to read user plan for tool call enforcement."
        )
