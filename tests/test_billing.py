import os
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

from tests.auth_helpers import auth_headers, configure_auth_env

configure_auth_env()
os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault(
    "SUPABASE_SECRET_KEY",
    "sb_secret_test",
)
os.environ.setdefault("STRIPE_SECRET_KEY", "sk_test_placeholder")
os.environ.setdefault("STRIPE_WEBHOOK_SECRET", "whsec_placeholder")

import stripe  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import billing  # noqa: E402


client = TestClient(app)


class FakeLineItems:
    def __init__(self, price_id: str = "price_starter"):
        self.price_id = price_id
        self.calls: list[tuple[str, dict[str, object] | None]] = []

    async def list_async(
        self, session_id: str, params: dict[str, object] | None = None
    ) -> SimpleNamespace:
        self.calls.append((session_id, params))
        return SimpleNamespace(
            data=[SimpleNamespace(price=SimpleNamespace(id=self.price_id))]
        )


class FakeCheckoutSessions:
    def __init__(self, price_id: str = "price_starter"):
        self.line_items = FakeLineItems(price_id)
        self.created_params: dict[str, object] | None = None

    async def create_async(
        self, params: dict[str, object] | None = None
    ) -> SimpleNamespace:
        self.created_params = params
        return SimpleNamespace(url="https://checkout.stripe.test/session")


class FakePortalSessions:
    def __init__(self):
        self.created_params: dict[str, object] | None = None

    async def create_async(
        self, params: dict[str, object] | None = None
    ) -> SimpleNamespace:
        self.created_params = params
        return SimpleNamespace(url="https://billing.stripe.test/portal")


class FakeStripeClient:
    def __init__(self, price_id: str = "price_starter"):
        self.checkout_sessions = FakeCheckoutSessions(price_id)
        self.portal_sessions = FakePortalSessions()
        self.v1 = SimpleNamespace(
            checkout=SimpleNamespace(sessions=self.checkout_sessions),
            billing_portal=SimpleNamespace(sessions=self.portal_sessions),
        )


class FakeUsersQuery:
    def __init__(self, data: dict[str, object] | list[object] | None = None):
        self.data = data if data is not None else []
        self.upserted: tuple[dict[str, object], dict[str, object]] | None = None
        self.updated: dict[str, object] | None = None
        self.filters: list[tuple[str, object]] = []
        self.selected: tuple[tuple[object, ...], dict[str, object]] | None = None

    def select(self, *args: object, **kwargs: object) -> "FakeUsersQuery":
        self.selected = (args, kwargs)
        return self

    def upsert(self, value: dict[str, object], **kwargs: object) -> "FakeUsersQuery":
        self.upserted = (value, kwargs)
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
        return SimpleNamespace(data=self.data)


class FakeSupabase:
    def __init__(
        self,
        execute_error: Exception | None = None,
        rows: list[dict[str, object]] | None = None,
    ):
        self.execute_error = execute_error
        self.rows = rows or []
        self.queries: list[FakeUsersQuery] = []

    def table(self, name: str) -> FakeUsersQuery:
        assert name == "users"
        data = self.rows.pop(0) if self.rows else []
        query = FakeUsersQuery(data)
        if self.execute_error is not None:
            query.execute = lambda: (_ for _ in ()).throw(self.execute_error)
        self.queries.append(query)
        return query


def configure_settings() -> None:
    billing.settings.stripe_starter_price_id = "price_starter"
    billing.settings.stripe_pro_price_id = "price_pro"
    billing.settings.stripe_success_url = "https://app.test/success"
    billing.settings.stripe_cancel_url = "https://app.test/cancel"
    billing.settings.stripe_portal_return_url = "https://app.test/billing"
    billing.settings.stripe_webhook_secret = "whsec_test"


class BillingTests(unittest.TestCase):
    def setUp(self) -> None:
        configure_settings()

    def test_checkout_rejects_unknown_price_id(self) -> None:
        response = client.post(
            "/billing/checkout",
            json={"price_id": "price_unknown", "email": "buyer@example.com"},
        )

        self.assertEqual(response.status_code, 400)

    def test_checkout_creates_subscription_session(self) -> None:
        fake_stripe = FakeStripeClient()

        with patch.object(billing, "stripe_client", fake_stripe):
            response = client.post(
                "/billing/checkout",
                json={"price_id": "price_starter", "email": "buyer@example.com"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"checkout_url": "https://checkout.stripe.test/session"},
        )
        self.assertEqual(
            fake_stripe.checkout_sessions.created_params,
            {
                "mode": "subscription",
                "customer_email": "buyer@example.com",
                "line_items": [{"price": "price_starter", "quantity": 1}],
                "success_url": "https://app.test/success",
                "cancel_url": "https://app.test/cancel",
            },
        )

    def test_webhook_signature_failure_returns_400(self) -> None:
        signature_error = stripe.error.SignatureVerificationError(
            "bad signature", "bad_header"
        )

        with patch.object(
            billing.stripe.Webhook,
            "construct_event",
            side_effect=signature_error,
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "bad_header"},
            )

        self.assertEqual(response.status_code, 400)

    def test_checkout_completed_updates_user_plan(self) -> None:
        fake_stripe = FakeStripeClient(price_id="price_pro")
        fake_supabase = FakeSupabase()
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test",
                    "customer": "cus_test",
                    "customer_email": "buyer@example.com",
                }
            },
        }

        with (
            patch.object(billing, "stripe_client", fake_stripe),
            patch.object(billing, "supabase_client", fake_supabase),
            patch.object(billing.stripe.Webhook, "construct_event", return_value=event),
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_header"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            fake_stripe.checkout_sessions.line_items.calls[0][0], "cs_test"
        )
        query = fake_supabase.queries[0]
        self.assertEqual(
            query.upserted,
            (
                {
                    "email": "buyer@example.com",
                    "plan": "pro",
                    "stripe_customer_id": "cus_test",
                },
                {"on_conflict": "email"},
            ),
        )

    def test_checkout_completed_maps_starter_plan(self) -> None:
        fake_stripe = FakeStripeClient(price_id="price_starter")
        fake_supabase = FakeSupabase()
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test",
                    "customer": "cus_test",
                    "customer_email": "buyer@example.com",
                }
            },
        }

        with (
            patch.object(billing, "stripe_client", fake_stripe),
            patch.object(billing, "supabase_client", fake_supabase),
            patch.object(billing.stripe.Webhook, "construct_event", return_value=event),
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_header"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_supabase.queries[0].upserted[0]["plan"], "starter")

    def test_checkout_completed_returns_200_when_supabase_write_fails(self) -> None:
        fake_stripe = FakeStripeClient(price_id="price_pro")
        fake_supabase = FakeSupabase(execute_error=RuntimeError("db down"))
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test",
                    "customer": "cus_test",
                    "customer_email": "buyer@example.com",
                }
            },
        }

        with (
            patch.object(billing, "stripe_client", fake_stripe),
            patch.object(billing, "supabase_client", fake_supabase),
            patch.object(billing.stripe.Webhook, "construct_event", return_value=event),
            patch.object(billing.logger, "exception") as log_exception,
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_header"},
            )

        self.assertEqual(response.status_code, 200)
        log_exception.assert_called_once_with(
            "Failed to update user billing state after checkout."
        )

    def test_subscription_deleted_resets_user_plan(self) -> None:
        fake_supabase = FakeSupabase()
        event = {
            "type": "customer.subscription.deleted",
            "data": {"object": {"customer": "cus_test"}},
        }

        with (
            patch.object(billing, "supabase_client", fake_supabase),
            patch.object(billing.stripe.Webhook, "construct_event", return_value=event),
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_header"},
            )

        self.assertEqual(response.status_code, 200)
        query = fake_supabase.queries[0]
        self.assertEqual(query.updated, {"plan": "free"})
        self.assertEqual(query.filters, [("stripe_customer_id", "cus_test")])

    def test_unhandled_webhook_event_returns_422(self) -> None:
        event = {"type": "invoice.paid", "data": {"object": {}}}

        with patch.object(
            billing.stripe.Webhook, "construct_event", return_value=event
        ):
            response = client.post(
                "/billing/webhook",
                content=b"{}",
                headers={"Stripe-Signature": "valid_header"},
            )

        self.assertEqual(response.status_code, 422)

    def test_portal_creates_session(self) -> None:
        fake_stripe = FakeStripeClient()

        with patch.object(billing, "stripe_client", fake_stripe):
            response = client.get(
                "/billing/portal",
                params={"stripe_customer_id": "cus_test"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"portal_url": "https://billing.stripe.test/portal"},
        )
        self.assertEqual(
            fake_stripe.portal_sessions.created_params,
            {
                "customer": "cus_test",
                "return_url": "https://app.test/billing",
            },
        )

    def test_usage_returns_starter_limit_and_reset(self) -> None:
        reset_at = datetime(2026, 4, 27, tzinfo=UTC)
        fake_supabase = FakeSupabase(
            rows=[
                {
                    "plan": "starter",
                    "tool_call_count": 47,
                    "tool_call_reset_at": reset_at.isoformat(),
                }
            ]
        )

        with patch.object(billing, "supabase_client", fake_supabase):
            response = client.get("/billing/usage", headers=auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "plan": "starter",
                "tool_call_count": 47,
                "tool_call_limit": 500,
                "reset_at": "2026-05-27T00:00:00Z",
            },
        )
        self.assertEqual(fake_supabase.queries[0].filters, [("id", "user-id")])

    def test_usage_returns_null_limit_for_pro(self) -> None:
        reset_at = datetime(2026, 4, 27, tzinfo=UTC)
        fake_supabase = FakeSupabase(
            rows=[
                {
                    "plan": "pro",
                    "tool_call_count": 1000,
                    "tool_call_reset_at": reset_at.isoformat(),
                }
            ]
        )

        with patch.object(billing, "supabase_client", fake_supabase):
            response = client.get("/billing/usage", headers=auth_headers())

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["tool_call_limit"])

    def test_usage_requires_authorization(self) -> None:
        response = client.get("/billing/usage")

        self.assertEqual(response.status_code, 401)

    def test_usage_rejects_malformed_token(self) -> None:
        response = client.get(
            "/billing/usage",
            headers={"Authorization": "Bearer not-a-jwt"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "Invalid token"})
