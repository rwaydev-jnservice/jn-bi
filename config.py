"""Central configuration. Every value comes from environment variables (or a
local .env file during development). Nothing secret is hardcoded here."""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---------------------------------------------------------
    database_url: str = Field(default="", alias="DATABASE_URL")

    # --- App / auth ---------------------------------------------------------
    # Used to encrypt stored OAuth tokens. Long random string (see README).
    app_secret_key: str = Field(default="", alias="APP_SECRET_KEY")
    app_base_url: str = Field(default="http://localhost:8501", alias="APP_BASE_URL")
    bootstrap_admin_email: str = Field(default="", alias="BOOTSTRAP_ADMIN_EMAIL")
    bootstrap_admin_password: str = Field(default="", alias="BOOTSTRAP_ADMIN_PASSWORD")
    bootstrap_admin_name: str = Field(default="Admin", alias="BOOTSTRAP_ADMIN_NAME")

    # --- Reporting ----------------------------------------------------------
    report_timezone: str = Field(default="America/New_York", alias="REPORT_TIMEZONE")
    # Week convention already used by the company's Pipedrive reports: Sun-Sat.
    week_starts_on: str = Field(default="SUNDAY", alias="WEEK_STARTS_ON")

    # --- Jobber (READ ONLY) --------------------------------------------------
    jobber_client_id: str = Field(default="", alias="JOBBER_CLIENT_ID")
    jobber_client_secret: str = Field(default="", alias="JOBBER_CLIENT_SECRET")
    # Only used once to seed the database; Jobber rotates refresh tokens, so the
    # live value is kept (encrypted) in the oauth_tokens table afterwards.
    jobber_refresh_token: str = Field(default="", alias="JOBBER_REFRESH_TOKEN")
    jobber_api_version: str = Field(default="2026-05-12", alias="JOBBER_API_VERSION")
    jobber_graphql_url: str = Field(default="https://api.getjobber.com/api/graphql", alias="JOBBER_GRAPHQL_URL")
    jobber_oauth_base: str = Field(default="https://api.getjobber.com/api/oauth", alias="JOBBER_OAUTH_BASE")
    jobber_page_size: int = Field(default=50, alias="JOBBER_PAGE_SIZE")
    # First sync only pulls records created/updated on or after this date.
    jobber_history_start: str = Field(default="2025-01-01", alias="JOBBER_HISTORY_START")
    # Visits/assessments are pulled for a rolling window around today.
    schedule_lookback_days: int = Field(default=45, alias="SCHEDULE_LOOKBACK_DAYS")
    schedule_lookahead_days: int = Field(default=60, alias="SCHEDULE_LOOKAHEAD_DAYS")

    # --- Pipedrive (READ ONLY) ------------------------------------------------
    pipedrive_api_token: str = Field(default="", alias="PIPEDRIVE_API_TOKEN")
    pipedrive_company_domain: str = Field(default="", alias="PIPEDRIVE_COMPANY_DOMAIN")
    pipedrive_sales_pipeline_id: int = Field(default=1, alias="PIPEDRIVE_SALES_PIPELINE_ID")

    # --- Optional AI classification -----------------------------------------
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")

    # --- Sync -----------------------------------------------------------------
    sync_interval_minutes: int = Field(default=15, alias="SYNC_INTERVAL_MINUTES")
    # Overlap applied to "changed since" watermarks so nothing slips between runs.
    sync_overlap_minutes: int = Field(default=10, alias="SYNC_OVERLAP_MINUTES")
    # Data older than this is flagged as stale in the UI.
    stale_after_minutes: int = Field(default=60, alias="STALE_AFTER_MINUTES")

    # --- Relationship inference -------------------------------------------
    # A repair quote must be created within this many days after the service
    # job's last visit/completion to count as a candidate.
    inference_window_days: int = Field(default=14, alias="INFERENCE_WINDOW_DAYS")
    # An estimate quote may be written up to this many days after the assessment.
    estimate_inference_window_days: int = Field(default=30, alias="ESTIMATE_INFERENCE_WINDOW_DAYS")
    # Approved 2026-10-21. When ON, dashboards, relationships and the process audit use the
    # quote origin engine (origin_flow_type) instead of the legacy flow_type. Set false to roll back.
    use_quote_origin_engine: bool = Field(default=True, alias="USE_QUOTE_ORIGIN_ENGINE")

    @field_validator("pipedrive_company_domain")
    @classmethod
    def _bare_pipedrive_domain(cls, v: str) -> str:
        """Accept 'jnservicehvac', 'jnservicehvac.pipedrive.com' or a full URL."""
        v = (v or "").strip().strip("\"'").lower()
        for prefix in ("https://", "http://"):
            if v.startswith(prefix):
                v = v[len(prefix):]
        v = v.split("/")[0]
        if v.endswith(".pipedrive.com"):
            v = v[: -len(".pipedrive.com")]
        return v

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.report_timezone)

    @property
    def sqlalchemy_url(self) -> str:
        """Render/Railway hand out postgres:// URLs; SQLAlchemy+psycopg3 needs
        postgresql+psycopg://."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        if url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url

    @property
    def jobber_configured(self) -> bool:
        return bool(self.jobber_client_id and self.jobber_client_secret)

    @property
    def pipedrive_configured(self) -> bool:
        return bool(self.pipedrive_api_token and self.pipedrive_company_domain)


@lru_cache
def get_settings() -> Settings:
    return Settings()
