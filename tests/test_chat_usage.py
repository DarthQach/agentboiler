import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import BackgroundTasks

from tests.auth_helpers import configure_auth_env

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)

from app.routers import chat  # noqa: E402


class FakeQuery:
    def __init__(self, execute_error: Exception | None = None):
        self.execute_error = execute_error
        self.inserted: dict[str, object] | None = None
        self.upserted: tuple[dict[str, object], dict[str, object]] | None = None

    def insert(self, value: dict[str, object]) -> "FakeQuery":
        self.inserted = value
        return self

    def upsert(self, value: dict[str, object], **kwargs: object) -> "FakeQuery":
        self.upserted = (value, kwargs)
        return self

    def execute(self) -> SimpleNamespace:
        if self.execute_error is not None:
            raise self.execute_error
        return SimpleNamespace(data=[])


class FakeSupabase:
    def __init__(self, execute_error: Exception | None = None):
        self.execute_error = execute_error
        self.queries: list[tuple[str, FakeQuery]] = []

    def table(self, name: str) -> FakeQuery:
        query = FakeQuery(self.execute_error)
        self.queries.append((name, query))
        return query


async def run_sync_inline(func):
    return func()


class ChatUsageTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_chat_schedules_usage_logging_background_task(self) -> None:
        result = SimpleNamespace(
            output="hello",
            usage=lambda: SimpleNamespace(request_tokens=100, response_tokens=50),
        )
        background_tasks = BackgroundTasks()

        with (
            patch.object(chat.agent, "run", AsyncMock(return_value=result)),
            patch.object(chat.settings, "agent_model", "gpt-4o-mini"),
        ):
            response = await chat.run_chat(
                chat.ChatRunRequest(prompt="hello", session_id="session-id"),
                background_tasks,
                current_user={"sub": "user-id"},
            )

        self.assertEqual(response, {"response": "hello", "session_id": "session-id"})
        self.assertEqual(len(background_tasks.tasks), 1)
        task = background_tasks.tasks[0]
        self.assertEqual(task.args, ("user-id", "session-id", "gpt-4o-mini", 100, 50, 0.000045))

    async def test_log_usage_upserts_user_before_session_and_usage(self) -> None:
        fake_supabase = FakeSupabase()

        with (
            patch.object(chat, "supabase_client", fake_supabase),
            patch.object(chat.anyio.to_thread, "run_sync", run_sync_inline),
        ):
            await chat._log_usage("user-id", "session-id", "gpt-4o-mini", 100, 50, 0.000045)

        self.assertEqual([name for name, _ in fake_supabase.queries], ["users", "sessions", "usage"])
        self.assertEqual(
            fake_supabase.queries[0][1].upserted,
            (
                {"id": "user-id", "plan": "starter", "tool_call_count": 0},
                {"on_conflict": "id", "ignore_duplicates": True},
            ),
        )
        self.assertEqual(
            fake_supabase.queries[1][1].upserted,
            ({"id": "session-id", "user_id": "user-id"}, {"on_conflict": "id"}),
        )
        self.assertEqual(
            fake_supabase.queries[2][1].inserted,
            {
                "user_id": "user-id",
                "session_id": "session-id",
                "model": "gpt-4o-mini",
                "input_tokens": 100,
                "output_tokens": 50,
                "cost_usd": 0.000045,
            },
        )

    async def test_usage_logging_failure_is_logged_without_raising(self) -> None:
        fake_supabase = FakeSupabase(execute_error=RuntimeError("db down"))

        with (
            patch.object(chat, "supabase_client", fake_supabase),
            patch.object(chat.anyio.to_thread, "run_sync", run_sync_inline),
            patch.object(chat.logger, "exception") as log_exception,
        ):
            await chat._log_usage("user-id", "session-id", "gpt-4o-mini", 100, 50, 0.000045)

        log_exception.assert_called_once_with(
            "Failed to log token usage for chat session %s.", "session-id"
        )
