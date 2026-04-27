from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env")

    app_env: str = "development"

    # Blank-safe for the scaffold; required in production integration steps.
    supabase_url: str = ""
    supabase_service_key: str = ""
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""


settings = Settings()
