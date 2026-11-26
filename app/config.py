"""Application settings loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AnyHttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Only what Alembic needs, so migrations run without Keitaro credentials."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://kt:kt@localhost:5432/kt_wrapper"


class Settings(DatabaseSettings):
    """Full runtime configuration of the web app."""

    kt_admin_api_url: AnyHttpUrl
    kt_admin_api_key: SecretStr
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    frontend_dist: Path = Path("frontend/dist")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
