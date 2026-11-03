from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All configuration comes from environment variables (or a local .env file)."""

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "sqlite:///./verdant.db"

    # Shared secret used by the chat platform to sign webhook bodies.
    webhook_secret: str = "verdant-demo-secret-2026"
    # Only messages for this brand are accepted (the schema already stores brand_id per row).
    brand_id: str = "verdant-co"

    together_api_key: str = ""
    together_model: str = "meta-llama/Llama-3.3-70B-Instruct-Turbo"
    together_base_url: str = "https://api.together.xyz/v1"
    llm_timeout_seconds: float = 30.0
    llm_max_attempts: int = 3

    # Seed agent created on startup if it does not exist yet.
    agent_seed_email: str = ""
    agent_seed_password: str = ""
    agent_seed_name: str = "Support Agent"

    session_ttl_hours: int = 12
    cookie_secure: bool = False  # set true when served over HTTPS

    log_level: str = "INFO"

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Render/Heroku/Supabase hand out postgres:// URLs; SQLAlchemy needs a driver name.
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
