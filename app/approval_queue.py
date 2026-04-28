import asyncio
import time

import anyio

from app.db import supabase_client


async def wait_for_approval(
    approval_id: str, poll_interval: float = 1.0, timeout: float = 300.0
) -> str:
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        approval_response = await anyio.to_thread.run_sync(
            lambda: supabase_client.table("tool_approvals")
            .select("status")
            .eq("id", approval_id)
            .single()
            .execute()
        )
        status = approval_response.data["status"]

        if status == "approved":
            return "approved"
        if status == "rejected":
            return "rejected"

        await asyncio.sleep(poll_interval)

    raise TimeoutError("approval timed out")
