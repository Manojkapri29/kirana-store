"""Application settings, read from environment variables (and an optional `.env` file).

Every variable uses the `KIRANA_` prefix, e.g. `KIRANA_ENVIRONMENT`.
Nothing secret is hard-coded here; see `.env.example` for the available variables.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
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

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings (cached after first read)."""
    return Settings()
