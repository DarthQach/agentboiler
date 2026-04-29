from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


APP_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=APP_ENV_FILE, extra="ignore")

    app_env: str = "development"
    agent_model: str = Field(
        default="claude-sonnet-4-6",
        validation_alias=AliasChoices("AGENT_MODEL", "DEFAULT_MODEL"),
    )
    agent_system_prompt: str = "You are a helpful assistant."

    # Blank-safe for the scaffold; required in production integration steps.
    supabase_url: str = ""
    supabase_secret_key: str = ""
    supabase_publishable_key: str = ""
    jwks_url: str = Field(
        ...,
        description=(
            "JWKS endpoint used to verify auth provider JWTs. Examples: "
            "Supabase https://YOUR_PROJECT_REF.supabase.co/auth/v1/.well-known/jwks.json; "
            "Clerk https://YOUR_CLERK_DOMAIN/.well-known/jwks.json."
        ),
    )
    jwt_audience: str | None = Field(
        default=None,
        description="Optional JWT audience validation. Recommended for production.",
    )
    jwt_issuer: str | None = Field(
        default=None,
        description="Optional JWT issuer validation. Recommended for production.",
    )
    approval_poll_interval: float = 1.0
    approval_timeout: float = 300.0
    agent_max_tool_retries: int = 3
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_starter_price_id: str = ""
    stripe_pro_price_id: str = ""
    stripe_success_url: str = "http://localhost:3000/billing/success"
    stripe_cancel_url: str = "http://localhost:3000/billing/cancel"
    stripe_portal_return_url: str = "http://localhost:3000/billing"
    frontend_origin: str = "http://localhost:3000"


settings = Settings()
