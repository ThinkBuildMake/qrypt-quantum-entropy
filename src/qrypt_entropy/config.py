"""Environment-based configuration (dev / staging / prod). Secrets come from the environment only."""

from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# What each profile sets when the environment does not (DP-4): same code, different configuration.
PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "dev": {"log_level": "DEBUG", "log_file": "logs/server.log"},
    "staging": {"log_level": "INFO", "log_file": ""},
    "prod": {"log_level": "INFO", "log_file": ""},
}
# Secrets each process refuses to start without, as environment variable names.
MCP_SERVER_SECRETS = {"PEPPER": "token_pepper"}  # environment variable name -> setting
HTTP_SERVICE_SECRETS = {"PEPPER": "token_pepper", "KEY_HASH_SECRET": "key_hash_secret"}


class ConfigError(Exception):
    """A start-up refusal. The message names the variable, never a value."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", validate_by_name=True, validate_by_alias=True)

    app_env: Literal["dev", "staging", "prod"] = Field("dev", validation_alias=AliasChoices("APP_PROFILE", "APP_ENV"))
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
    rate_limit_window_seconds: float = 600
    rate_limit_max_failures: int = 5
    rate_limit_lock_timeout_ms: int = 2000
    token_pepper: SecretStr | None = Field(None, validation_alias=AliasChoices("PEPPER", "TOKEN_PEPPER"))

    @model_validator(mode="after")
    def _apply_profile(self):
        for name, value in PROFILE_DEFAULTS[self.app_env].items():
            if name not in self.model_fields_set:
                object.__setattr__(self, name, value)
        return self

    def secret_values(self) -> list[str]:
        """Every secret this process holds, so the logger can refuse to write it."""
        secrets = [v.get_secret_value() for v in (self.token_pepper, self.qrypt_api_token) if v]
        return secrets + [v for v in (self.mcp_client_key, self.key_hash_secret) if v]

    def require(self, secrets: dict[str, str]) -> None:
        missing = [env_name for env_name, field in secrets.items() if not getattr(self, field)]
        if missing:
            raise ConfigError(f"required secret not set: {', '.join(missing)}")


@lru_cache
def get_settings() -> Settings:
    return Settings()
