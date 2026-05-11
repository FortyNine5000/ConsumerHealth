"""Environment configuration loaded from .env or GitHub Actions secrets."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Data Sources ────────────────────────────────────────────────────────
    fred_api_key: str = Field("", alias="FRED_API_KEY")
    bls_api_key: str = Field("", alias="BLS_API_KEY")
    bea_api_key: str = Field("", alias="BEA_API_KEY")
    census_api_key: str = Field("", alias="CENSUS_API_KEY")
    eia_api_key: str = Field("", alias="EIA_API_KEY")

    # ── Database ─────────────────────────────────────────────────────────────
    turso_database_url: str = Field("", alias="TURSO_DATABASE_URL")
    turso_auth_token: str = Field("", alias="TURSO_AUTH_TOKEN")

    # ── AI ───────────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field("", alias="ANTHROPIC_API_KEY")

    # ── Deployment ───────────────────────────────────────────────────────────
    cloudflare_deploy_hook_url: str = Field("", alias="CLOUDFLARE_DEPLOY_HOOK_URL")

    # ── SEC EDGAR ────────────────────────────────────────────────────────────
    sec_user_agent: str = Field(
        "ConsumerCompass/1.0 (admin@example.com)",
        alias="SEC_USER_AGENT",
    )

    # ── Newsletter ───────────────────────────────────────────────────────────
    beehiiv_api_key: str = Field("", alias="BEEHIIV_API_KEY")

    # ── Rate-limiting / tuning ───────────────────────────────────────────────
    fred_sleep_seconds: float = 0.5
    bls_max_series_per_request: int = 50
    backfill_start_date: str = "1990-01-01"


settings = Settings()
