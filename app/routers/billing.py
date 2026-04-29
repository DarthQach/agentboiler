import logging
from datetime import UTC, datetime
from typing import Any

import anyio
import stripe
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, EmailStr

from app.auth import get_current_user
from app.config import settings
from app.db import supabase_client
from app.middleware.plan_enforcement import RESET_INTERVAL, STARTER_TOOL_CALL_LIMIT
from app.stripe_client import stripe_client


router = APIRouter(prefix="/billing")
logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    price_id: str
    email: EmailStr


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalResponse(BaseModel):
    portal_url: str


class UsageResponse(BaseModel):
    plan: str
    tool_call_count: int
    tool_call_limit: int | None
    reset_at: str


def _allowed_price_ids() -> dict[str, str]:
    return {
        settings.stripe_starter_price_id: "starter",
        settings.stripe_pro_price_id: "pro",
    }


def _plan_for_price_id(price_id: str | None) -> str | None:
    if not price_id:
        return None
    return _allowed_price_ids().get(price_id)


def _get_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _get_nested(value: Any, *keys: str) -> Any:
    current = value
    for key in keys:
        current = _get_value(current, key)
        if current is None:
            return None
    return current


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        parsed = datetime.now(UTC)

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tool_call_limit(plan: str) -> int | None:
    if plan == "pro":
        return None
    if plan == "starter":
        return STARTER_TOOL_CALL_LIMIT
    return 0


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


async def _upsert_user_plan(email: str, plan: str, stripe_customer_id: str) -> None:
    await anyio.to_thread.run_sync(
        lambda: supabase_client.table("users")
        .upsert(
            {
                "email": email,
                "plan": plan,
                "stripe_customer_id": stripe_customer_id,
            },
            on_conflict="email",
        )
        .execute()
    )


async def _reset_user_plan(stripe_customer_id: str) -> None:
    await anyio.to_thread.run_sync(
        lambda: supabase_client.table("users")
        .update({"plan": "free"})
        .eq("stripe_customer_id", stripe_customer_id)
        .execute()
    )


async def _checkout_session_price_id(session_id: str) -> str | None:
    line_items = await stripe_client.v1.checkout.sessions.line_items.list_async(
        session_id,
        params={"limit": 1},
    )
    data = _get_value(line_items, "data") or []
    if not data:
        return None

    # This starter template creates exactly one Checkout line item. If you add
    # multi-item checkouts, iterate the paginated line item list explicitly.
    return _get_nested(data[0], "price", "id")


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout_session(request: CheckoutRequest) -> CheckoutResponse:
    if _plan_for_price_id(request.price_id) is None:
        raise HTTPException(status_code=400, detail="Unknown Stripe price ID.")

    session = await stripe_client.v1.checkout.sessions.create_async(
        params={
            "mode": "subscription",
            "customer_email": str(request.email),
            "line_items": [{"price": request.price_id, "quantity": 1}],
            "success_url": settings.stripe_success_url,
            "cancel_url": settings.stripe_cancel_url,
        }
    )
    checkout_url = _get_value(session, "url")
    if not checkout_url:
        raise HTTPException(
            status_code=502, detail="Stripe did not return a checkout URL."
        )

    return CheckoutResponse(checkout_url=checkout_url)


@router.post("/webhook")
async def handle_stripe_webhook(
    request: Request,
    stripe_signature: str = Header(alias="Stripe-Signature"),
) -> dict[str, str]:
    payload = await request.body()

    try:
        # Security boundary: never process Stripe webhook payloads before
        # verifying the Stripe-Signature header with the webhook secret.
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, settings.stripe_webhook_secret
        )
    except stripe.error.SignatureVerificationError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid Stripe signature."
        ) from exc

    event_type = _get_value(event, "type")
    event_object = _get_nested(event, "data", "object")

    if event_type == "checkout.session.completed":
        email = _get_value(event_object, "customer_email")
        stripe_customer_id = _get_value(event_object, "customer")
        session_id = _get_value(event_object, "id")
        if not email or not stripe_customer_id or not session_id:
            return {"status": "ignored"}

        price_id = await _checkout_session_price_id(session_id)
        plan = _plan_for_price_id(price_id)
        if plan is None:
            return {"status": "ignored"}

        try:
            await _upsert_user_plan(email, plan, stripe_customer_id)
        except Exception:
            logger.exception("Failed to update user billing state after checkout.")

        return {"status": "ok"}

    if event_type == "customer.subscription.deleted":
        stripe_customer_id = _get_value(event_object, "customer")
        if not stripe_customer_id:
            return {"status": "ignored"}

        try:
            await _reset_user_plan(stripe_customer_id)
        except Exception:
            logger.exception("Failed to reset user billing state after cancellation.")

        return {"status": "ok"}

    raise HTTPException(status_code=422, detail="Unhandled Stripe event type.")


@router.get("/portal", response_model=PortalResponse)
async def create_portal_session(stripe_customer_id: str) -> PortalResponse:
    # The scaffold accepts the Stripe customer ID as a query parameter.
    session = await stripe_client.v1.billing_portal.sessions.create_async(
        params={
            "customer": stripe_customer_id,
            "return_url": settings.stripe_portal_return_url,
        }
    )
    portal_url = _get_value(session, "url")
    if not portal_url:
        raise HTTPException(
            status_code=502, detail="Stripe did not return a portal URL."
        )

    return PortalResponse(portal_url=portal_url)


@router.get("/usage", response_model=UsageResponse)
async def get_usage(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> UsageResponse:
    user_id = _extract_user_id(current_user)
    response = await anyio.to_thread.run_sync(
        lambda: supabase_client.table("users")
        .select("plan,tool_call_count,tool_call_reset_at")
        .eq("id", user_id)
        .execute()
    )
    user = _first_row(response.data)
    plan = user.get("plan") or "free"
    tool_call_count = int(user.get("tool_call_count") or 0)
    reset_at = _parse_datetime(user.get("tool_call_reset_at")) + RESET_INTERVAL

    return UsageResponse(
        plan=plan,
        tool_call_count=tool_call_count,
        tool_call_limit=_tool_call_limit(plan),
        reset_at=reset_at.isoformat().replace("+00:00", "Z"),
    )
