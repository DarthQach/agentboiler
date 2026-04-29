import logging

import anyio

from app.db import supabase_client


logger = logging.getLogger(__name__)


async def increment_tool_call_count(user_id: str) -> None:
    try:
        response = await anyio.to_thread.run_sync(
            lambda: supabase_client.table("users")
            .select("tool_call_count")
            .eq("id", user_id)
            .single()
            .execute()
        )
        current_count = int((response.data or {}).get("tool_call_count") or 0)

        # The scaffold uses a simple read-then-update counter.
        await anyio.to_thread.run_sync(
            lambda: supabase_client.table("users")
            .update({"tool_call_count": current_count + 1})
            .eq("id", user_id)
            .execute()
        )
    except Exception:
        logger.exception("Failed to increment user tool call count.")
