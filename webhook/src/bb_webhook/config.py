"""Configuration loader — reads .env and provides validated settings."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the project root (consolidated 2026-07-08).
# Previously loaded from webhook/.env; now all components share the single
# main .env at /root/Buttonsbebe Agent/.env
# config.py is at: webhook/src/bb_webhook/config.py
# parents[0]=bb_webhook, [1]=src, [2]=webhook, [3]=Buttonsbebe Agent (project root)
_AGENT_ROOT = Path(__file__).resolve().parents[3]
_ENV_PATH = _AGENT_ROOT / ".env"
_DEMO_MODE_AT_IMPORT = os.environ.get("DEMO_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}
if not _DEMO_MODE_AT_IMPORT:
    load_dotenv(_ENV_PATH)


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env."""

    # load_dotenv at import already populated os.environ, which Settings()
    # reads — one .env parse instead of two (report 01-6).
    model_config = SettingsConfigDict(extra="ignore")

    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    processor_result_secret: str = Field(default="", alias="PROCESSOR_RESULT_SECRET", repr=False)

    # ── Gorgias ───────────────────────────────────────────
    gorgias_subdomain: str = Field(default="buttonsbebe", alias="GORGIAS_SUBDOMAIN")
    gorgias_api_email: str = Field(default="", alias="GORGIAS_API_EMAIL")
    gorgias_api_key: str = Field(default="", alias="GORGIAS_API_KEY")
    gorgias_base_url_override: str = Field(default="", alias="GORGIAS_BASE_URL")

    # ── Webhook security ──────────────────────────────────
    webhook_secret: str = Field(default="", alias="WEBHOOK_SECRET")

    # ── Server ────────────────────────────────────────────
    webhook_host: str = Field(default="127.0.0.1", alias="WEBHOOK_HOST")
    webhook_port: int = Field(default=8000, alias="WEBHOOK_PORT")

    # ── Human console authentication ─────────────────────
    # The password is stored as a PBKDF2 hash and sessions are signed with a
    # separate secret. Neither value is safe to replace with a plaintext
    # password or to check into the repository.
    console_username: str = Field(default="chaim", alias="CONSOLE_USERNAME")
    console_password_hash: str = Field(default="", alias="CONSOLE_PASSWORD_HASH")
    console_session_secret: str = Field(default="", alias="CONSOLE_SESSION_SECRET")

    # ── Queue / idempotency DB ────────────────────────────
    webhook_db_path: str = Field(default="./data/webhook.db", alias="WEBHOOK_DB_PATH")

    # ── Shopify (client-credentials grant) ───────────────
    shopify_shop: str = Field(default="buttonsbebe", alias="SHOPIFY_SHOP")
    shopify_client_id: str = Field(default="", alias="SHOPIFY_CLIENT_ID")
    shopify_client_secret: str = Field(default="", alias="SHOPIFY_CLIENT_SECRET")

    # ── Demo-only isolation checks ────────────────────────
    support_store_name: str = Field(default="Buttons Bebe", alias="SUPPORT_STORE_NAME")
    feedback_kb_root: str = Field(default="", alias="FEEDBACK_KB_ROOT")
    hermes_profile: str = Field(default="", alias="HERMES_PROFILE")
    hermes_rewrite_toolsets: str = Field(default="", alias="HERMES_REWRITE_TOOLSETS")
    hermes_ignore_rules: bool = Field(default=False, alias="HERMES_IGNORE_RULES")

    # ── Logging ───────────────────────────────────────────
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # ── Derived ──────────────────────────────────────────
    @property
    def gorgias_base_url(self) -> str:
        if self.gorgias_base_url_override:
            return self.gorgias_base_url_override.rstrip("/")
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

    @model_validator(mode="after")
    def validate_demo_boundary(self) -> "Settings":
        """Fail closed before startup if demo mode inherited client settings."""

        if not self.demo_mode:
            return self
        errors: list[str] = []
        if self.shopify_shop != "yznyc1-ez.myshopify.com":
            errors.append("SHOPIFY_SHOP must be the Cute Things demo store")
        if self.gorgias_subdomain != "cute-things-demo":
            errors.append("GORGIAS_SUBDOMAIN must be cute-things-demo")
        if self.gorgias_base_url != "http://127.0.0.1:8190":
            errors.append("GORGIAS_BASE_URL must be the local demo Gorgias sink")
        if self.webhook_host not in {"127.0.0.1", "localhost", "::1"}:
            errors.append("WEBHOOK_HOST must be loopback in demo mode")
        if self.webhook_port != 8100:
            errors.append("WEBHOOK_PORT must be 8100 in demo mode")
        expected_db = (
            Path(__file__).resolve().parents[3]
            / "webhook" / "data" / "cute-things-demo-webhook.db"
        ).resolve()
        if self.db_path_absolute.resolve() != expected_db:
            errors.append("WEBHOOK_DB_PATH must be the approved Cute Things demo database")
        feedback_root = Path(self.feedback_kb_root)
        if not feedback_root.is_absolute():
            feedback_root = _AGENT_ROOT / feedback_root
        expected_feedback_root = (_AGENT_ROOT / "demo" / "data" / "kb").resolve()
        if feedback_root.resolve() != expected_feedback_root:
            errors.append("FEEDBACK_KB_ROOT must be the approved demo KB directory")
        if self.hermes_profile != "cutethingsdemo":
            errors.append("HERMES_PROFILE must be cutethingsdemo")
        if self.hermes_rewrite_toolsets.strip().lower() == "todo":
            errors.append("HERMES_REWRITE_TOOLSETS must not be the retired 'todo' placeholder")
        if not self.hermes_ignore_rules:
            errors.append("HERMES_IGNORE_RULES must be enabled in demo mode")
        if self.support_store_name != "Cute Things":
            errors.append("SUPPORT_STORE_NAME must be Cute Things")
        if errors:
            raise ValueError("unsafe demo webhook configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    """Cached singleton — import this everywhere."""
    return Settings()
