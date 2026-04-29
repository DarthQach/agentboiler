import asyncio
from typing import Any

import anyio
from pydantic_ai import RunContext

from app.agent import AgentDeps, agent
from app.approval_queue import wait_for_approval
from app.config import settings
from app.db import supabase_client
from app.exceptions import ToolRejected
from app.middleware.plan_enforcement import check_tool_call_limit
from app.middleware.tool_counter import increment_tool_call_count


async def _require_approval(
    user_id: str, session_id: str, tool_name: str, tool_args: dict[str, Any]
) -> None:
    await check_tool_call_limit(user_id)

    rejection_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("tool_approvals")
        .select("id", count="exact")
        .eq("session_id", session_id)
        .eq("tool_name", tool_name)
        .eq("status", "rejected")
        .execute()
    )
    rejection_count = rejection_response.count or 0
    if rejection_count >= settings.agent_max_tool_retries:
        raise ToolRejected("max rejections reached")

    approval_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("tool_approvals")
        .insert(
            {
                "session_id": session_id,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "status": "pending",
            }
        )
        .execute()
    )
    approval_row = approval_response.data[0]
    approval_id = approval_row["id"]

    try:
        status = await wait_for_approval(
            approval_id,
            poll_interval=settings.approval_poll_interval,
            timeout=settings.approval_timeout,
        )
    except TimeoutError:
        raise ToolRejected("approval timed out") from None

    if status == "rejected":
        raise ToolRejected(tool_name)


def _schedule_tool_call_increment(user_id: str) -> None:
    asyncio.create_task(increment_tool_call_count(user_id))


@agent.tool
async def web_search(ctx: RunContext[AgentDeps], query: str) -> str:
    tool_name = "web_search"
    await _require_approval(
        ctx.deps.user_id, ctx.deps.session_id, tool_name, {"query": query}
    )

    result = f"[stub] Search results for: {query}"
    _schedule_tool_call_increment(ctx.deps.user_id)
    return result


@agent.tool
async def send_email(
    ctx: RunContext[AgentDeps], to: str, subject: str, body: str
) -> str:
    tool_name = "send_email"
    await _require_approval(
        ctx.deps.user_id,
        ctx.deps.session_id,
        tool_name,
        {"to": to, "subject": subject, "body": body},
    )

    result = f"[stub] Email sent to {to} with subject '{subject}'"
    _schedule_tool_call_increment(ctx.deps.user_id)
    return result


@agent.tool
async def create_file(
    ctx: RunContext[AgentDeps], filename: str, content: str
) -> str:
    tool_name = "create_file"
    await _require_approval(
        ctx.deps.user_id,
        ctx.deps.session_id,
        tool_name,
        {"filename": filename, "content": content},
    )

    result = f"[stub] File '{filename}' created with {len(content)} characters"
    _schedule_tool_call_increment(ctx.deps.user_id)
    return result
