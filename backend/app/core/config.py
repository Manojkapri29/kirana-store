"""Application settings, read from environment variables (and an optional `.env` file).

Every variable uses the `KIRANA_` prefix, e.g. `KIRANA_ENVIRONMENT`.
Nothing secret is hard-coded here; see `.env.example` for the available variables.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# The backend/ directory. Relative SQLite paths are resolved against it, so the database location
# does not depend on which directory a command happens to be started from.
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="KIRANA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Shop Manager API"
    app_version: str = "0.1.0"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False  # never true in production (rejected at start-up)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    # "json" (one object per line, for log collectors) or "text" (for a terminal).
    log_format: Literal["json", "text"] = "text"

    @field_validator("environment", mode="before")
    @classmethod
    def _environment_aliases(cls, value: object) -> object:
        """`testing` and `prod` are accepted spellings of `test` and `production`."""
        if isinstance(value, str):
            return {"testing": "test", "prod": "production", "dev": "development"}.get(
                value.strip().lower(), value.strip().lower()
            )
        return value

    # The browser address of the frontend (used for links and to check CORS in production).
    frontend_url: str | None = None
    # Required in production; never logged or shown. (Session tokens are random and stored hashed; this key is kept for signing.)
    secret_key: SecretStr | None = None

    # --- Rate limiting (per shop, or per client address where there is no shop). "calls per window". ---
    rate_limit_enabled: bool = True
    rate_limit_window_seconds: int = Field(default=60, ge=1, le=3600)
    rate_limit_ai: int = Field(default=30, ge=1)  # assistant questions, insights, document reading
    rate_limit_image: int = Field(default=20, ge=1)  # photo analysis
    rate_limit_external: int = Field(default=40, ge=1)  # barcode lookup, outside price checks
    rate_limit_coupon: int = Field(
        default=600, ge=1
    )  # bill pricing and coupon checks (a cashier types quickly)
    rate_limit_export: int = Field(default=20, ge=1)
    rate_limit_webhook: int = Field(default=120, ge=1)
    # Argon2id needs ~64 MiB per hash. Sign-ins beyond this many at once wait their turn instead of all taking memory together.
    password_hash_concurrency: int = Field(default=4, ge=1, le=64)
    rate_limit_store_order: int = Field(default=10, ge=1)  # public: orders placed from one address per window
    rate_limit_sync: int = Field(default=600, ge=1)  # offline batches (up to 50 operations each)
    rate_limit_message: int = Field(default=60, ge=1)  # messages sent to customers cost money and can annoy people
    rate_limit_payment: int = Field(default=120, ge=1)
    rate_limit_admin: int = Field(default=60, ge=1)  # internal administration
    rate_limit_admin_auth_failures: int = Field(default=10, ge=1)  # bad admin tokens per window per address
    # Sign-in attempts per window, per client address and per email (the public storefront does not exist yet).
    rate_limit_auth: int = Field(default=10, ge=1)
    rate_limit_public: int = Field(default=60, ge=1)

    # --- Shop account lifecycle: what each state allows. "blocked" = nothing but the account page;
    # "read_only" =
    # looking is allowed, changing is not. Policies are configuration, not code. ---
    suspended_policy: Literal["read_only", "blocked"] = "read_only"
    deactivated_policy: Literal["read_only", "blocked"] = "blocked"

    # --- Feature flags: switch a whole capability off for every shop (an operator's emergency brake). ---
    feature_ai: bool = True
    feature_exports: bool = True
    feature_notifications: bool = True
    feature_price_lookups: bool = True

    # --- Backups. SQLite only for now; the storage is behind an interface so object storage can follow. ---
    backup_dir: str = "./data/backups"
    backup_storage_provider: str = "local"  # "local" is the only one written; others report "not configured"
    backup_keep_daily_days: int = Field(default=14, ge=1, le=3650)
    backup_keep_weekly_weeks: int = Field(default=8, ge=0, le=520)
    backup_min_free_mb: int = Field(
        default=200, ge=0
    )  # refuse to start a backup with less free space than this
    backup_stale_after_hours: int = Field(default=36, ge=1)  # the owner is told when the last backup is older
    # Restoring over the live database from the API is off unless an operator turns it on. The CLI always
    # works.
    restore_via_api_enabled: bool = False

    # --- Notifications. In-app always works. Other channels stay "not configured" until set up. ---
    notification_email_provider: str | None = None
    notification_sms_provider: str | None = None
    notification_whatsapp_provider: str | None = None
    notification_push_provider: str | None = None
    # --- File storage (Phase 17). "local" (default) keeps files in a private folder; "s3" uses an S3-compatible service. The credentials
    # are ONE environment variable named here, holding `ACCESS_KEY_ID:SECRET_ACCESS_KEY` (its name must start KIRANA_INTEGRATION_).
    storage_provider: Literal["local", "s3"] = "local"
    storage_s3_endpoint: str | None = None
    storage_s3_bucket: str | None = None
    storage_s3_region: str = "us-east-1"
    storage_s3_path_style: bool = True
    storage_credentials_ref: str = "KIRANA_INTEGRATION_STORAGE_CREDENTIALS"
    notification_max_attempts: int = Field(default=5, ge=1, le=20)
    notification_backoff_seconds: int = Field(default=60, ge=1)

    # Upload limits shared by every file entry point (the image settings below are the existing names).
    allowed_image_types: Annotated[list[str], NoDecode] = ["image/jpeg", "image/png", "image/webp"]

    @field_validator("allowed_image_types", mode="before")
    @classmethod
    def _split_types(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    # SQLite for the MVP; a PostgreSQL URL (postgresql+psycopg://...) works after the Phase 15 gate.
    database_url: str = "sqlite:///./data/kirana.db"
    # How long a SQLite connection waits for another writer before failing with "database is locked".
    db_busy_timeout_ms: int = 5000
    # PostgreSQL connection pool (ignored by SQLite). Total connections a process may hold = pool size + overflow; keep
    # workers x that number under the database's max_connections.
    db_pool_size: int = Field(default=10, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=200)
    db_pool_recycle_seconds: int = Field(default=1800, ge=30)
    db_pool_timeout_seconds: int = Field(default=30, ge=1)

    # NoDecode lets us accept a plain comma-separated string instead of JSON.
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    # Optional file for internal error diagnostics (JSON lines, redacted). Unset: the normal server log only.
    diagnostics_log_file: str | None = None

    # --- Photos (image intelligence). Optional. Limits protect the server; a provider is backend only. ---
    image_max_bytes: int = Field(default=5_000_000, ge=50_000, le=15_000_000)  # the largest photo accepted
    image_max_side: int = Field(default=8000, ge=256, le=20000)  # the longest side, in pixels
    image_storage_dir: str = "./data/images"  # private, never served directly; relative to backend/
    image_max_per_shop: int = Field(default=500, ge=0, le=100_000)  # photos a shop may keep in total
    # Which image-analysis provider to use (none ship with the app). Unset means "not configured": photos can
    # still be taken and a barcode read in the browser is still looked up, but no picture is sent anywhere.
    image_analysis_provider: str | None = None
    image_analysis_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("IMAGE_ANALYSIS_API_KEY", "KIRANA_IMAGE_ANALYSIS_API_KEY")
    )

    @field_validator("image_analysis_api_key", mode="before")
    @classmethod
    def _blank_image_key_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() in ("", "your_api_key_here"):
            return None
        return value

    # --- AI assistant (Phase 10). Optional. The key is backend only; nothing here reaches the browser. ---
    # Which provider to use ("anthropic" today; the provider layer is replaceable). Unset = "not configured":
    # the assistant's ready-made questions, insights and reports still work, free-form questions are limited.
    ai_provider: str | None = None
    ai_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("AI_API_KEY", "KIRANA_AI_API_KEY")
    )
    ai_model: str | None = None  # the provider's own model name; a sensible default is used when unset
    ai_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    ai_max_output_tokens: int = Field(default=1024, ge=64, le=8192)

    @field_validator("ai_api_key", mode="before")
    @classmethod
    def _blank_ai_key_is_unset(cls, value: object) -> object:
        if isinstance(value, str) and value.strip() in ("", "your_api_key_here"):
            return None
        return value

    # --- External price/product providers. Backend only: none of these ever reaches the browser. ---
    # Master switch. Off means no outgoing request is ever made; every price check reports "disabled".
    external_lookups_enabled: bool = True
    # Seconds to wait for a provider before giving up on it (a slow provider never slows a bill down).
    external_timeout_seconds: float = Field(default=4.0, gt=0, le=30)
    # A price seen within this many hours is served from the shop's own saved copy without asking again.
    price_cache_ttl_hours: int = Field(default=24, ge=0, le=24 * 30)
    # Open Food Facts asks every app to identify itself: AppName/Version (contact).
    off_user_agent: str = "ShopManager/0.1 (contact: not-set)"
    # UPCitemdb key. Read from UPCITEMDB_API_KEY (or KIRANA_UPCITEMDB_API_KEY) in the backend environment
    # or `.env` only. A SecretStr never appears in a repr or a log line. Unset means "not configured".
    upcitemdb_api_key: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("UPCITEMDB_API_KEY", "KIRANA_UPCITEMDB_API_KEY")
    )

    @field_validator("upcitemdb_api_key", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, value: object) -> object:
        """`UPCITEMDB_API_KEY=` or the .env.example placeholder means "no key"."""
        if isinstance(value, str) and value.strip() in ("", "your_api_key_here"):
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    # --- Sign-in and sessions ---
    # A session ends after this long without use, and at the latest after the absolute limit, whatever the activity.
    session_idle_minutes: int = Field(default=60, ge=5, le=1440)
    session_absolute_hours: int = Field(default=12, ge=1, le=168)
    session_cookie_name: str = "kirana_session"
    csrf_cookie_name: str = "kirana_csrf"
    # None = decide from the environment: Secure in production (needs HTTPS), not Secure in development (plain HTTP).
    session_cookie_secure: bool | None = None
    session_cookie_samesite: Literal["lax", "strict"] = "lax"
    # After this many wrong passwords in a row the account is paused for a while (and the attempts are rate limited).
    login_max_failures: int = Field(default=5, ge=3, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)
    password_min_length: int = Field(default=10, ge=8, le=64)
    # Argon2id cost. The defaults follow the OWNER guidance (19 MiB, 2 passes is the floor); tests lower them for speed.
    password_hash_time_cost: int = Field(default=3, ge=1, le=10)
    password_hash_memory_kib: int = Field(default=65536, ge=1024, le=1048576)
    invitation_expiry_hours: int = Field(default=72, ge=1, le=720)
    # Behind a reverse proxy the client address is in X-Forwarded-For; trust it only when the proxy sets it.
    trust_proxy_headers: bool = False
    # Development shortcut: every request acts as the seeded development owner, with no sign-in. Never in production.
    dev_auth_bypass: bool = False

    # --- Monitoring and background jobs ---
    metrics_enabled: bool = False
    metrics_token: SecretStr | None = None  # required to read /metrics when it is enabled
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_backoff_seconds: int = Field(default=30, ge=1, le=3600)

    @property
    def cookie_secure(self) -> bool:
        return self.is_production if self.session_cookie_secure is None else self.session_cookie_secure

    def production_problems(self) -> list[str]:
        """What is wrong with this configuration for a production start. Names only, never values."""
        if not self.is_production:
            return []
        problems: list[str] = []
        if self.secret_key is None or len(self.secret_key.get_secret_value()) < 32:
            problems.append("KIRANA_SECRET_KEY must be set to a random value of at least 32 characters")
        if not self.frontend_url:
            problems.append("KIRANA_FRONTEND_URL must be set")
        elif not self.frontend_url.startswith("https://"):
            problems.append("KIRANA_FRONTEND_URL must use https")
        if not self.cors_origins or any(origin.strip() == "*" for origin in self.cors_origins):
            problems.append("KIRANA_CORS_ORIGINS must list the frontend origin(s), not '*'")
        elif self.frontend_url and self.frontend_url.rstrip("/") not in [
            o.rstrip("/") for o in self.cors_origins
        ]:
            problems.append("KIRANA_CORS_ORIGINS must include KIRANA_FRONTEND_URL")
        if self.debug:
            problems.append("KIRANA_DEBUG must be false")
        if self.log_level == "DEBUG":
            problems.append("KIRANA_LOG_LEVEL must not be DEBUG")
        if not self.rate_limit_enabled:
            problems.append("KIRANA_RATE_LIMIT_ENABLED must not be false")
        if self.password_hash_memory_kib < 19456 or self.password_hash_time_cost < 2:
            problems.append("KIRANA_PASSWORD_HASH_* is below the minimum cost for production")
        if self.dev_auth_bypass:
            problems.append("KIRANA_DEV_AUTH_BYPASS must be false")
        if not self.cookie_secure:
            problems.append("KIRANA_SESSION_COOKIE_SECURE must not be false")
        if self.metrics_enabled and (
            self.metrics_token is None or len(self.metrics_token.get_secret_value()) < 16
        ):
            problems.append("KIRANA_METRICS_TOKEN must be set (16+ characters) when metrics are enabled")
        return problems


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached after first read)."""
    return Settings()
