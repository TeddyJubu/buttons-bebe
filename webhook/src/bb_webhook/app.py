"""FastAPI composition root for the Buttons Bebe webhook receiver."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import database, deps, session_store
from .middleware.console_session import ConsoleSessionMiddleware
from .config import get_settings
from .logging_utils import get_logger, log_event, setup_logging
from .middleware import rate_limit as _rate_limit
from .routers import (
    auth as _auth,
    console as _console,
    dashboard as _dashboard,
    health as _health,
    notifications as _notifications,
    webhook as _webhook,
)

# Keep the established app-module patch surface without making routers import
# this composition root. deps.resolve() consults these facades at call time.
_DATABASE_NAMES = {"dashboard_ticket_exists", "enqueue_job", "get_dashboard_tickets",
                   "get_parsed_messages", "get_result_stats",
                   "get_setting", "init_db", "ingest_event", "is_duplicate", "record_event",
                   "record_ticket_result", "set_setting"}
_CONSOLE_NAMES = {"_GClient", "_HERMES_BIN", "_HERMES_HOME", "_HERMES_IGNORE_RULES",
                  "_HERMES_PROFILE", "_HERMES_REWRITE_TOOLSETS", "_SUPPORT_STORE_NAME",
                  "_asyncio", "_ledger", "_record_lesson", "action_note", "action_rewrite",
                  "action_send", "learning_stats"}
_DASHBOARD_NAMES = {"dashboard_messages", "dashboard_stats", "dashboard_tickets_api",
                    "record_result_api"}
_NOTIFICATION_NAMES = {"_NOTIFICATION_READ_STATE_KEY", "_current_notifications",
                       "_read_notification_state", "dashboard_notifications_api",
                       "mark_dashboard_notifications_read"}
_HEALTH_NAMES = {"health", "ready"}
_WEBHOOK_NAMES = {"_MAX_WEBHOOK_BODY_BYTES", "is_event_in_future", "is_event_too_old",
                  "parse_event", "receive_gorgias_webhook", "verify_signature"}
_MODULE_NAMES = ((_DATABASE_NAMES, database), (_CONSOLE_NAMES, _console),
                 ({"auth_check", "auth_login", "auth_logout", "auth_page_check",
                   "auth_session"}, _auth),
                 (_DASHBOARD_NAMES, _dashboard), (_NOTIFICATION_NAMES, _notifications),
                 (_HEALTH_NAMES, _health), (_WEBHOOK_NAMES, _webhook))
_RATE_LIMIT_NAMES = {"_MAX_REQUESTS_PER_MINUTE": _rate_limit._MAX_REQUESTS_PER_MINUTE,
                     "_check_rate_limit": _rate_limit._check_rate_limit,
                     "_rate_window": _rate_limit._rate_window}


def __getattr__(name: str):
    """Resolve legacy app-level imports and monkeypatch targets."""
    if name in _RATE_LIMIT_NAMES:
        return _RATE_LIMIT_NAMES[name]
    for names, module in _MODULE_NAMES:
        if name in names:
            return getattr(module, name)
    raise AttributeError(name)


logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = deps.get_settings()
    log_event(logger, "INFO", "Starting webhook receiver", host=settings.webhook_host,
              port=settings.webhook_port, tenant=settings.gorgias_subdomain)
    await deps.database_function("init_db")()
    await session_store.initialize(settings.db_path_absolute)
    yield
    log_event(logger, "INFO", "Shutting down webhook receiver")


def create_app() -> FastAPI:
    application = FastAPI(
        title="Buttons Bebe Webhook Receiver",
        description=("Receives Gorgias ticket-message webhooks, validates signature, "
                     "dedupes, and enqueues jobs for the orchestrator."),
        version="0.2.0",
        lifespan=lifespan,
    )
    application.add_middleware(ConsoleSessionMiddleware)
    for route_router in (
        _health.router, _webhook.router, _auth.router, _dashboard.router,
        _notifications.router, _console.router,
    ):
        application.include_router(route_router)
    return application


app = create_app()
