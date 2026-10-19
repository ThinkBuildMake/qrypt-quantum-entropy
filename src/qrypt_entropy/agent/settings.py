"""The agent's own settings. It reads only these names; a database URL, pepper, hashing secret or Qrypt token in the
environment or `.env` is ignored, so the agent process never holds one (AZ-3, A-14)."""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: SecretStr | None = None
    openrouter_base_url: str = (
        "https://openrouter.ai/api/v1"  # a local OpenAI-compatible stub in tests and make demo-chat
    )
    agent_model: str = "minimax/minimax-m3.1-flash:free"  # :free, tool-calling; chosen by trial (DEVIATIONS DEP-1)
    agent_log_file: str = "logs/agent.log"
    agent_results_file: str = "results/agent_run.json"
    mcp_client_key: str | None = None
    token_expiry_default_seconds: int = 600
    log_level: str = "INFO"
