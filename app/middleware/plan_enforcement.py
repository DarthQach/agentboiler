import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
from fastapi import HTTPException

from app.db import supabase_client


logger = logging.getLogger(__name__)
STARTER_TOOL_CALL_LIMIT = 500
RESET_INTERVAL = timedelta(days=30)


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("Expected datetime or ISO datetime string.")

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _payment_required(error: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=402,
        detail={"error": error, "message": message},
    )


def _first_row(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list):
        return data[0] if data else None
    if isinstance(data, dict):
        return data
    return None


async def check_tool_call_limit(user_id: str) -> None:
    try:
        response = await anyio.to_thread.run_sync(
            lambda: supabase_client.table("users")
            .select("plan,tool_call_count,tool_call_reset_at")
            .eq("id", user_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to read user plan for tool call enforcement.")
        return

    user = _first_row(response.data)
    if not user:
        raise _payment_required("free_plan", "Upgrade to call tools")

    try:
        plan = user.get("plan") or "free"
        tool_call_count = int(user.get("tool_call_count") or 0)
        tool_call_reset_at = _parse_datetime(user.get("tool_call_reset_at"))
    except Exception:
        logger.exception("Failed to parse user plan for tool call enforcement.")
        return

    now = datetime.now(UTC)
    if tool_call_reset_at + RESET_INTERVAL <= now:
        tool_call_count = 0
        tool_call_reset_at = now
        try:
            await anyio.to_thread.run_sync(
                lambda: supabase_client.table("users")
                .update(
                    {
                        "tool_call_count": tool_call_count,
                        "tool_call_reset_at": tool_call_reset_at.isoformat(),
                    }
                )
                .eq("id", user_id)
                .execute()
            )
        except Exception:
            logger.exception("Failed to reset user tool call count.")
            return

    if plan == "pro":
        return

    if plan == "starter":
        if tool_call_count >= STARTER_TOOL_CALL_LIMIT:
            raise _payment_required(
                "limit_reached",
                "Monthly tool call limit reached. Upgrade to Pro.",
            )
        return

    raise _payment_required("free_plan", "Upgrade to call tools")
