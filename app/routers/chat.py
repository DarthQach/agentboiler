import logging
import uuid
from typing import Any

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.agent import AgentDeps, agent
from app.auth import get_current_user
from app.config import settings
from app.db import supabase_client
from app.exceptions import ToolRejected
from app.utils.token_cost import calculate_cost


router = APIRouter(prefix="/chat")
logger = logging.getLogger(__name__)


class ChatRunRequest(BaseModel):
    prompt: str
    session_id: str | None = None


def _extract_user_id(current_user: dict[str, Any]) -> str:
    user_id = current_user.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


async def _log_usage(
    user_id: str,
    session_id: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
) -> None:
    try:
        await anyio.to_thread.run_sync(
            lambda: supabase_client.table("users")
            .upsert(
                {"id": user_id, "plan": "starter", "tool_call_count": 0},
                on_conflict="id",
                ignore_duplicates=True,
            )
            .execute()
        )
        await anyio.to_thread.run_sync(
            lambda: supabase_client.table("sessions")
            .upsert({"id": session_id, "user_id": user_id}, on_conflict="id")
            .execute()
        )
        await anyio.to_thread.run_sync(
            lambda: supabase_client.table("usage")
            .insert(
                {
                    "user_id": user_id,
                    "session_id": session_id,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_usd": cost,
                }
            )
            .execute()
        )
    except Exception:
        logger.exception("Failed to log token usage for chat session %s.", session_id)


@router.post("/run")
async def run_chat(
    request: ChatRunRequest,
    background_tasks: BackgroundTasks,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, str]:
    user_id = _extract_user_id(current_user)
    session_id = request.session_id or str(uuid.uuid4())
    try:
        result = await agent.run(
            request.prompt, deps=AgentDeps(session_id=session_id, user_id=user_id)
        )
    except ToolRejected as exc:
        logger.info("Tool rejected while running chat session %s: %s", session_id, exc)
        return {
            "response": "The requested tool was not approved, so I continued without using it.",
            "session_id": session_id,
        }

    usage = result.usage()
    model = settings.agent_model
    input_tokens = usage.request_tokens
    output_tokens = usage.response_tokens
    cost = calculate_cost(model, input_tokens, output_tokens)
    background_tasks.add_task(
        _log_usage, user_id, session_id, model, input_tokens, output_tokens, cost
    )

    return {"response": result.output, "session_id": session_id}
