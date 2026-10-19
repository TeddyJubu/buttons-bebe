"""FastAPI composition root for the Buttons Bebe webhook receiver."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import load_root_env

# Before the router imports: console.py reads HERMES_* from os.environ at import.
load_root_env()

from . import database, session_store, user_store  # noqa: E402
from .middleware.console_session import ConsoleSessionMiddleware
from .config import get_settings
from .logging_utils import get_logger, log_event, setup_logging
from .routers import (
    auth as _auth,
    console as _console,
    dashboard as _dashboard,
    health as _health,
    notifications as _notifications,
    users as _users,
    webhook as _webhook,
)

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_logging()
    settings = get_settings()
    log_event(logger, "INFO", "Starting webhook receiver", host=settings.webhook_host,
              port=settings.webhook_port, tenant=settings.gorgias_subdomain)
    await database.init_db()
    await session_store.initialize(settings.db_path_absolute)
    await user_store.bootstrap_from_settings(settings, settings.db_path_absolute)
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
        _notifications.router, _console.router, _users.router,
    ):
        application.include_router(route_router)
    return application


app = create_app()
