"""Environment-based configuration (dev / staging / prod). Secrets come from the environment only."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: Literal["dev", "staging", "prod"] = "dev"
    database_url: str = "postgresql://localhost:5432/qrypt"
    log_level: str = "INFO"
    log_file: str = "logs/server.log"
    mcp_client_key: str | None = None
    qrypt_api_token: SecretStr | None = Field(
        default=None, validation_alias=AliasChoices("QRYPT_API_KEY", "QRYPT_API_TOKEN")
    )  # QRYPT_API_KEY is the documented name
    qrypt_base_url: str = "https://api-eus.qrypt.com"
    qrypt_timeout_seconds: float = 5.0
    key_hash_secret: str | None = None
    per_call_max_bytes: int = 1024
    pool_low_mark_bytes: int = 32 * 1024
    pool_high_mark_bytes: int = 128 * 1024
    token_expiry_default_seconds: int = 600
    token_expiry_max_seconds: int = 86400
    token_pepper: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
