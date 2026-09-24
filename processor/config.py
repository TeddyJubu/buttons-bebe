"""Configuration for the job processor.

Loads from the same .env as the webhook receiver.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the project root (consolidated 2026-07-08).
# Previously loaded from webhook/.env; now all components share the single
# main .env at /root/Buttonsbebe Agent/.env
# config.py is at: processor/config.py → parents[1]=processor, parents[2]=Buttonsbebe Agent
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
_DEMO_MODE_AT_IMPORT = os.environ.get("DEMO_MODE", "").strip().lower() in {
    "1", "true", "yes", "on",
}
if not _DEMO_MODE_AT_IMPORT:
    load_dotenv(_ENV_PATH)


class ProcessorSettings(BaseSettings):
    """Central configuration for the job processor."""

    model_config = SettingsConfigDict(
        env_file=None if _DEMO_MODE_AT_IMPORT else str(_ENV_PATH),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    processor_result_secret: str = Field(default="", alias="PROCESSOR_RESULT_SECRET", repr=False)

    # ── Gorgias ───────────────────────────────────────────
    gorgias_subdomain: str = Field(default="buttonsbebe", alias="GORGIAS_SUBDOMAIN")
    gorgias_api_email: str = Field(default="", alias="GORGIAS_API_EMAIL")
    gorgias_api_key: str = Field(default="", alias="GORGIAS_API_KEY")

    # ── Database ──────────────────────────────────────────
    webhook_db_path: str = Field(default="./data/webhook.db", alias="WEBHOOK_DB_PATH")

    # ── KB MCP ────────────────────────────────────────────
    kb_mcp_url: str = Field(default="http://127.0.0.1:8077/mcp", alias="KB_MCP_URL")

    # ── Shopify (client-credentials grant) ───────────────
    shopify_shop: str = Field(default="buttonsbebe", alias="SHOPIFY_SHOP")
    shopify_client_id: str = Field(default="", alias="SHOPIFY_CLIENT_ID")
    shopify_client_secret: str = Field(default="", alias="SHOPIFY_CLIENT_SECRET")
    support_store_name: str = Field(default="Buttons Bebe", alias="SUPPORT_STORE_NAME")

    # ── Processor tuning ──────────────────────────────────
    poll_interval: float = Field(default=2.0, alias="PROCESSOR_POLL_INTERVAL")
    job_timeout: int = Field(default=120, alias="PROCESSOR_JOB_TIMEOUT")  # seconds
    max_retries: int = Field(default=3, alias="PROCESSOR_MAX_RETRIES")
    stale_job_minutes: int = Field(default=10, alias="PROCESSOR_STALE_MINUTES")
    # How often the idle loop emits a "still alive" log line. This is the
    # marker processor/heartbeat.sh looks for, so it must stay well under
    # HEARTBEAT_STALE_MINUTES (its own setting, not the queue's
    # PROCESSOR_STALE_MINUTES). Set to 0 to disable the idle heartbeat.
    heartbeat_seconds: float = Field(default=120.0, alias="PROCESSOR_HEARTBEAT_SECONDS")

    # ── Hermes tool policy ────────────────────────────────
    # Comma-separated toolsets passed to `hermes -t`. Hermes names each MCP
    # toolset by the configured server key itself, so this list is exactly the
    # three read-only Buttons Bebe servers and nothing
    # else - in particular NOT the `terminal` or `file` toolsets that
    # ~/.hermes/config.yaml grants the CLI platform by default.
    # Set to "" to fall back to whatever config.yaml grants (NOT recommended).
    hermes_toolsets: str = Field(
        default="buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias",
        alias="HERMES_TOOLSETS",
    )
    # Escape hatch. --yolo skips Hermes' approval prompts; with the toolset
    # locked down there is nothing dangerous left to approve, so it is off.
    # Turn it on ONLY as a temporary unblock if a run starts hanging on an
    # approval prompt, and open an issue - see archive/DEV-ISSUES.md #8.
    hermes_skip_approval: bool = Field(default=False, alias="HERMES_SKIP_APPROVAL")
    hermes_profile: str = Field(default="", alias="HERMES_PROFILE")
    hermes_ignore_rules: bool = Field(default=False, alias="HERMES_IGNORE_RULES")
    hermes_bin: str = Field(default="hermes", alias="HERMES_BIN")
    hermes_home: str = Field(default="/root", alias="HERMES_OS_HOME")
    hermes_path: str = Field(
        default="/root/.local/bin:/usr/local/bin:/usr/bin:/bin",
        alias="HERMES_PATH",
    )

    # ── Logging ───────────────────────────────────────────
    log_format: str = Field(default="json", alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @model_validator(mode="after")
    def validate_demo_boundary(self) -> "ProcessorSettings":
        """Reject inherited production settings whenever demo mode is active."""

        if not self.demo_mode:
            return self
        errors: list[str] = []
        if self.shopify_shop != "yznyc1-ez.myshopify.com":
            errors.append("SHOPIFY_SHOP must be the Cute Things demo store")
        if self.gorgias_subdomain != "cute-things-demo":
            errors.append("GORGIAS_SUBDOMAIN must be cute-things-demo")
        expected_db = (
            Path(__file__).resolve().parent.parent
            / "webhook" / "data" / "cute-things-demo-webhook.db"
        ).resolve()
        if self.db_path_absolute.resolve() != expected_db:
            errors.append("WEBHOOK_DB_PATH must be the approved Cute Things demo database")
        if self.kb_mcp_url != "http://127.0.0.1:8177/mcp":
            errors.append("KB_MCP_URL must be the local demo KB")
        if self.hermes_profile != "cutethingsdemo":
            errors.append("HERMES_PROFILE must be cutethingsdemo")
        expected_tools = {
            "buttonsbebe_kb", "buttonsbebe_redo", "buttonsbebe_gorgias",
        }
        actual_tools = {
            item.strip() for item in self.hermes_toolsets.split(",") if item.strip()
        }
        if actual_tools != expected_tools:
            errors.append("HERMES_TOOLSETS must contain only the three demo MCPs")
        if not self.hermes_ignore_rules:
            errors.append("HERMES_IGNORE_RULES must be enabled in demo mode")
        if self.hermes_skip_approval:
            errors.append("HERMES_SKIP_APPROVAL must stay disabled in demo mode")
        if self.support_store_name != "Cute Things":
            errors.append("SUPPORT_STORE_NAME must be Cute Things")
        if errors:
            raise ValueError("unsafe demo processor configuration: " + "; ".join(errors))
        return self

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

@lru_cache(maxsize=1)
def get_settings() -> ProcessorSettings:
    return ProcessorSettings()
