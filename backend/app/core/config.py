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
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # SQLite for the MVP; a PostgreSQL URL (postgresql+psycopg://...) works after the Phase 15 gate.
    database_url: str = "sqlite:///./data/kirana.db"
    # How long a SQLite connection waits for another writer before failing with "database is locked".
    db_busy_timeout_ms: int = 5000

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


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached after first read)."""
    return Settings()
