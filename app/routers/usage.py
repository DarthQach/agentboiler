from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_current_user
from app.db import supabase_client
from app.middleware.plan_enforcement import STARTER_TOOL_CALL_LIMIT


router = APIRouter(prefix="/usage")


class PeriodResponse(BaseModel):
    month: int
    year: int


class TokensResponse(BaseModel):
    input: int
    output: int
    total: int


class ToolCallsResponse(BaseModel):
    used: int
    limit: int | None
    remaining: int | None


class UsageResponse(BaseModel):
    period: PeriodResponse
    tokens: TokensResponse
    cost_usd: float
    requests: int
    tool_calls: ToolCallsResponse
    plan: str


def _first_row(data: Any) -> dict[str, Any]:
    if isinstance(data, list):
        return data[0] if data else {}
    if isinstance(data, dict):
        return data
    return {}


def _extract_user_id(current_user: dict[str, Any]) -> str:
    user_id = current_user.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user_id


def _period_bounds(month: int | None, year: int | None) -> tuple[int, int, datetime, datetime]:
    now = datetime.now(UTC)
    selected_month = month or now.month
    selected_year = year or now.year
    start = datetime(selected_year, selected_month, 1, tzinfo=UTC)
    if selected_month == 12:
        end = datetime(selected_year + 1, 1, 1, tzinfo=UTC)
    else:
        end = datetime(selected_year, selected_month + 1, 1, tzinfo=UTC)
    return selected_month, selected_year, start, end


def _tool_call_limit(plan: str) -> int | None:
    if plan == "pro":
        return None
    if plan == "starter":
        return STARTER_TOOL_CALL_LIMIT
    return 0


@router.get("", response_model=UsageResponse)
async def get_usage(
    current_user: dict[str, Any] = Depends(get_current_user),
    month: int | None = Query(default=None, ge=1, le=12),
    year: int | None = None,
) -> UsageResponse:
    user_id = _extract_user_id(current_user)
    selected_month, selected_year, start, end = _period_bounds(month, year)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    await anyio.to_thread.run_sync(
        lambda: supabase_client.table("users")
        .upsert(
            {"id": user_id, "plan": "starter", "tool_call_count": 0},
            on_conflict="id",
            ignore_duplicates=True,
        )
        .execute()
    )

    user_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("users")
        .select("plan")
        .eq("id", user_id)
        .execute()
    )
    user = _first_row(user_response.data)
    plan = str(user.get("plan") or "free")

    usage_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("usage")
        .select("input_tokens,output_tokens,cost_usd")
        .eq("user_id", user_id)
        .gte("created_at", start_iso)
        .lt("created_at", end_iso)
        .execute()
    )
    usage_rows = usage_response.data if isinstance(usage_response.data, list) else []

    total_input_tokens = sum(int(row.get("input_tokens") or 0) for row in usage_rows)
    total_output_tokens = sum(int(row.get("output_tokens") or 0) for row in usage_rows)
    total_cost = sum(Decimal(str(row.get("cost_usd") or "0")) for row in usage_rows)

    sessions_response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("sessions")
        .select("id")
        .eq("user_id", user_id)
        .execute()
    )
    session_rows = sessions_response.data if isinstance(sessions_response.data, list) else []
    session_ids = [str(row["id"]) for row in session_rows if row.get("id")]

    tool_call_count = 0
    if session_ids:
        approvals_response = await anyio.to_thread.run_sync(
            lambda: supabase_client.table("tool_approvals")
            .select("id", count="exact")
            .eq("status", "approved")
            .in_("session_id", session_ids)
            .gte("created_at", start_iso)
            .lt("created_at", end_iso)
            .execute()
        )
        tool_call_count = int(approvals_response.count or 0)

    tool_call_limit = _tool_call_limit(plan)
    remaining = (
        None if tool_call_limit is None else max(tool_call_limit - tool_call_count, 0)
    )

    return UsageResponse(
        period=PeriodResponse(month=selected_month, year=selected_year),
        tokens=TokensResponse(
            input=total_input_tokens,
            output=total_output_tokens,
            total=total_input_tokens + total_output_tokens,
        ),
        cost_usd=float(round(total_cost, 6)),
        requests=len(usage_rows),
        tool_calls=ToolCallsResponse(
            used=tool_call_count,
            limit=tool_call_limit,
            remaining=remaining,
        ),
        plan=plan,
    )
