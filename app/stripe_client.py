import stripe

from app.config import settings


stripe_client = stripe.StripeClient(
    settings.stripe_secret_key,
    http_client=stripe.HTTPXClient(),
)
