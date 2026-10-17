"""Environment-based configuration (dev / staging / prod). Secrets come from the environment only."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "staging", "prod"] = "dev"
    database_url: str = "postgresql://localhost:5432/qrypt"
    log_level: str = "INFO"
    log_file: str = "logs/server.log"
    mcp_client_key: str | None = None
    per_call_max_bytes: int = 1024
    token_expiry_default_seconds: int = 600
    token_expiry_max_seconds: int = 86400


@lru_cache
def get_settings() -> Settings:
    return Settings()
