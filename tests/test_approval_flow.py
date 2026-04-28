import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.auth_helpers import configure_auth_env

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)

from app import approval_queue  # noqa: E402
from app import tools  # noqa: E402
from app.exceptions import ToolRejected  # noqa: E402


class FakeQuery:
    def __init__(self, response: SimpleNamespace):
        self.response = response
        self.filters: list[tuple[str, object]] = []
        self.selected: tuple[tuple[object, ...], dict[str, object]] | None = None
        self.inserted: dict[str, object] | None = None

    def select(self, *args: object, **kwargs: object) -> "FakeQuery":
        self.selected = (args, kwargs)
        return self

    def insert(self, value: dict[str, object]) -> "FakeQuery":
        self.inserted = value
        return self

    def eq(self, key: str, value: object) -> "FakeQuery":
        self.filters.append((key, value))
        return self

    def single(self) -> "FakeQuery":
        return self

    def execute(self) -> SimpleNamespace:
        return self.response


class FakeSupabase:
    def __init__(self, responses: list[SimpleNamespace]):
        self.responses = responses
        self.queries: list[FakeQuery] = []

    def table(self, name: str) -> FakeQuery:
        assert name == "tool_approvals"
        query = FakeQuery(self.responses.pop(0))
        self.queries.append(query)
        return query


async def run_sync_inline(func):
    return func()


class WaitForApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_wait_for_approval_returns_approved(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data={"status": "approved"})]
        )

        with (
            patch.object(approval_queue, "supabase_client", fake_supabase),
            patch.object(approval_queue.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            result = await approval_queue.wait_for_approval(
                "approval-id", poll_interval=0, timeout=1
            )

        self.assertEqual(result, "approved")
        self.assertEqual(fake_supabase.queries[0].filters, [("id", "approval-id")])

    async def test_wait_for_approval_returns_rejected(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data={"status": "rejected"})]
        )

        with (
            patch.object(approval_queue, "supabase_client", fake_supabase),
            patch.object(approval_queue.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            result = await approval_queue.wait_for_approval(
                "approval-id", poll_interval=0, timeout=1
            )

        self.assertEqual(result, "rejected")

    async def test_wait_for_approval_times_out(self) -> None:
        fake_supabase = FakeSupabase(
            [SimpleNamespace(data={"status": "pending"}) for _ in range(20)]
        )

        with (
            patch.object(approval_queue, "supabase_client", fake_supabase),
            patch.object(approval_queue.anyio.to_thread, "run_sync", run_sync_inline),
            patch.object(approval_queue.asyncio, "sleep", AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await approval_queue.wait_for_approval(
                    "approval-id", poll_interval=0, timeout=0
                )


class RequireApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_max_rejections_filters_session_tool_and_status(self) -> None:
        fake_supabase = FakeSupabase([SimpleNamespace(data=[], count=3)])

        with (
            patch.object(tools, "supabase_client", fake_supabase),
            patch.object(tools.anyio.to_thread, "run_sync", run_sync_inline),
            patch.object(tools, "check_tool_call_limit", AsyncMock()),
        ):
            with self.assertRaisesRegex(ToolRejected, "max rejections reached"):
                await tools._require_approval(
                    "user-id", "session-id", "send_email", {"to": "a@example.com"}
                )

        self.assertEqual(
            fake_supabase.queries[0].filters,
            [
                ("session_id", "session-id"),
                ("tool_name", "send_email"),
                ("status", "rejected"),
            ],
        )

    async def test_pending_insert_is_wrapped_and_waited(self) -> None:
        fake_supabase = FakeSupabase(
            [
                SimpleNamespace(data=[], count=0),
                SimpleNamespace(data=[{"id": "approval-id"}]),
            ]
        )
        run_sync_calls = 0

        async def tracked_run_sync(func):
            nonlocal run_sync_calls
            run_sync_calls += 1
            return func()

        with (
            patch.object(tools, "supabase_client", fake_supabase),
            patch.object(tools.anyio.to_thread, "run_sync", tracked_run_sync),
            patch.object(tools, "check_tool_call_limit", AsyncMock()),
            patch.object(tools, "wait_for_approval", AsyncMock(return_value="approved")),
        ):
            await tools._require_approval(
                "user-id", "session-id", "web_search", {"query": "x"}
            )

        self.assertEqual(run_sync_calls, 2)
        self.assertEqual(
            fake_supabase.queries[1].inserted,
            {
                "session_id": "session-id",
                "tool_name": "web_search",
                "tool_args": {"query": "x"},
                "status": "pending",
            },
        )

    async def test_timeout_becomes_tool_rejected(self) -> None:
        fake_supabase = FakeSupabase(
            [
                SimpleNamespace(data=[], count=0),
                SimpleNamespace(data=[{"id": "approval-id"}]),
            ]
        )

        with (
            patch.object(tools, "supabase_client", fake_supabase),
            patch.object(tools.anyio.to_thread, "run_sync", run_sync_inline),
            patch.object(tools, "check_tool_call_limit", AsyncMock()),
            patch.object(tools, "wait_for_approval", AsyncMock(side_effect=TimeoutError)),
        ):
            with self.assertRaisesRegex(ToolRejected, "approval timed out"):
                await tools._require_approval(
                    "user-id", "session-id", "web_search", {"query": "x"}
                )

    async def test_plan_limit_is_checked_before_pending_insert(self) -> None:
        fake_supabase = FakeSupabase(
            [
                SimpleNamespace(data=[], count=0),
                SimpleNamespace(data=[{"id": "approval-id"}]),
            ]
        )
        events: list[str] = []

        async def check_limit(user_id: str) -> None:
            events.append(f"check:{user_id}")

        async def tracked_run_sync(func):
            events.append("query")
            return func()

        with (
            patch.object(tools, "supabase_client", fake_supabase),
            patch.object(tools.anyio.to_thread, "run_sync", tracked_run_sync),
            patch.object(tools, "check_tool_call_limit", check_limit),
            patch.object(tools, "wait_for_approval", AsyncMock(return_value="approved")),
        ):
            await tools._require_approval(
                "user-id", "session-id", "web_search", {"query": "x"}
            )

        self.assertEqual(events[0], "check:user-id")

    async def test_increment_fires_after_approved_tool_result(self) -> None:
        ctx = SimpleNamespace(deps=SimpleNamespace(user_id="user-id", session_id="session-id"))

        with (
            patch.object(tools, "_require_approval", AsyncMock()),
            patch.object(tools, "_schedule_tool_call_increment", Mock()) as schedule,
        ):
            result = await tools.web_search(ctx, "x")

        self.assertEqual(result, "[stub] Search results for: x")
        schedule.assert_called_once_with("user-id")

    async def test_increment_does_not_fire_after_rejection(self) -> None:
        ctx = SimpleNamespace(deps=SimpleNamespace(user_id="user-id", session_id="session-id"))

        with (
            patch.object(
                tools,
                "_require_approval",
                AsyncMock(side_effect=ToolRejected("web_search")),
            ),
            patch.object(tools, "_schedule_tool_call_increment", Mock()) as schedule,
        ):
            with self.assertRaises(ToolRejected):
                await tools.web_search(ctx, "x")

        schedule.assert_not_called()
