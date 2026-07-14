"""Configuration loader — reads .env and provides validated settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the project root (consolidated 2026-07-08).
# Previously loaded from webhook/.env; now all components share the single
# main .env at /root/Buttonsbebe Agent/.env
# config.py is at: webhook/src/bb_webhook/config.py
# parents[0]=bb_webhook, [1]=src, [2]=webhook, [3]=Buttonsbebe Agent (project root)
_AGENT_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _AGENT_ROOT / ".env"
load_dotenv(_ENV_PATH)


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Gorgias ───────────────────────────────────────────
    gorgias_subdomain: str = Field(default="buttonsbebe", alias="GORGIAS_SUBDOMAIN")
    gorgias_api_email: str = Field(default="", alias="GORGIAS_API_EMAIL")
    gorgias_api_key: str = Field(default="", alias="GORGIAS_API_KEY")

    # ── Webhook security ──────────────────────────────────
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")

    # ── Server ────────────────────────────────────────────
    webhook_host: str = Field(default="127.0.0.1", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8000, alias="WEBHOOK_PORT")

    # ── Queue / idempotency DB ────────────────────────────
    webhook_db_path: str = Field(default="./data/webhook.db", alias="WEBHOOK_DB_PATH")

    # ── Shopify (client-credentials grant) ───────────────
    shopify_shop: str = Field(default="buttonsbebe", alias="SHOPIFY_SHOP")
    shopify_client_id: str = Field(default="", alias="SHOPIFY_CLIENT_ID")
    shopify_client_secret: str = Field(default="", alias="SHOPIFY_CLIENT_SECRET")

    # ── Logging ───────────────────────────────────────────
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── Derived ──────────────────────────────────────────
    @property
    def gorgias_base_url(self) -> str:
        return f"https://{self.gorgias_subdomain}.gorgias.com"

    @property
    def gorgias_auth(self) -> tuple[str, str] | None:
        """Basic auth tuple for Gorgias API, or None if not configured."""
        if self.gorgias_api_email and self.gorgias_api_key:
            return (self.gorgias_api_email, self.gorgias_api_key)
        return None

    @property
    def db_path_absolute(self) -> Path:
        p = Path(self.webhook_db_path)
        if not p.is_absolute():
            # Resolve relative to the project root (webhook/ dir),
            # which is three levels up from config.py:
            # config.py → bb_webhook/ → src/ → webhook/
            p = Path(__file__).resolve().parent.parent.parent / p
        return p

    # ── Validators ───────────────────────────────────────
    @field_validator("webhook_secret")
    @classmethod
    def secret_not_empty(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "WEBHOOK_SECRET must be set. Generate one with: "
                "python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
            )
        return v

    @field_validator("log_level")
    @classmethod
    def valid_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"LOG_LEVEL must be a valid level, got {v}")
        return v


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import this everywhere."""
    return Settings()
