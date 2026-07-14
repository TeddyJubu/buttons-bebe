"""Configuration for the job processor.

Loads from the same .env as the webhook receiver.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the project root (consolidated 2026-07-08).
# Previously loaded from webhook/.env; now all components share the single
# main .env at /root/Buttonsbebe Agent/.env
# config.py is at: processor/config.py → parents[1]=processor, parents[2]=Buttonsbebe Agent
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)


class ProcessorSettings(BaseSettings):
    """Central configuration for the job processor."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Gorgias ───────────────────────────────────────────
    gorgias_subdomain: str = Field(default="buttonsbebe", alias="GORGIAS_SUBDOMAIN")
    gorgias_api_email: str = Field(default="", alias="GORGIAS_API_EMAIL")
    gorgias_api_key: str = Field(default="", alias="GORGIAS_API_KEY")

    # ── Database ──────────────────────────────────────────
    webhook_db_path: str = Field(default="./data/webhook.db", alias="WEBHOOK_DB_PATH")

    # ── KB MCP ────────────────────────────────────────────
    kb_mcp_url: str = Field(default="http://127.0.0.1:8077/mcp", alias="KB_MCP_URL")

    # ── LLM ───────────────────────────────────────────────
    # Ollama Cloud (same model Hermes uses) or any OpenAI-compatible endpoint
    llm_base_url: str = Field(default="http://localhost:11434/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="glm-5.2", alias="LLM_MODEL")
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")

    # ── Shopify (client-credentials grant) ───────────────
    shopify_shop: str = Field(default="buttonsbebe", alias="SHOPIFY_SHOP")
    shopify_client_id: str = Field(default="", alias="SHOPIFY_CLIENT_ID")
    shopify_client_secret: str = Field(default="", alias="SHOPIFY_CLIENT_SECRET")

    # ── Processor tuning ──────────────────────────────────
    poll_interval: float = Field(default=2.0, alias="PROCESSOR_POLL_INTERVAL")
    job_timeout: int = Field(default=120, alias="PROCESSOR_JOB_TIMEOUT")  # seconds
    max_retries: int = Field(default=3, alias="PROCESSOR_MAX_RETRIES")
    stale_job_minutes: int = Field(default=10, alias="PROCESSOR_STALE_MINUTES")

    # ── Logging ───────────────────────────────────────────
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def gorgias_base_url(self) -> str:
        return f"https://{self.gorgias_subdomain}.gorgias.com"

    @property
    def gorgias_auth(self) -> tuple[str, str] | None:
        if self.gorgias_api_email and self.gorgias_api_key:
            return (self.gorgias_api_email, self.gorgias_api_key)
        return None

    @property
    def db_path_absolute(self) -> Path:
        p = Path(self.webhook_db_path)
        if not p.is_absolute():
            # Resolve relative to webhook project root
            p = Path(__file__).resolve().parent.parent / "webhook" / p
        return p

    @property
    def shopify_configured(self) -> bool:
        return bool(self.shopify_client_id and self.shopify_client_secret)

_settings: ProcessorSettings | None = None


def get_settings() -> ProcessorSettings:
    global _settings
    if _settings is None:
        _settings = ProcessorSettings()
    return _settings
