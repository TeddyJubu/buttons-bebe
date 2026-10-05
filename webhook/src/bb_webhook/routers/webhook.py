"""Authenticated Gorgias webhook ingestion."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import database
from ..logging_utils import get_logger, log_event
from ..middleware.rate_limit import _check_rate_limit
from ..webhook_handler import (
    is_event_in_future,
    is_event_too_old,
    parse_event,
    verify_signature,
)

router = APIRouter()
logger = get_logger(__name__)
_MAX_WEBHOOK_BODY_BYTES = 1_048_576


@router.post("/webhook/gorgias/{tenant_id}")
async def receive_gorgias_webhook(request: Request, tenant_id: str) -> JSONResponse:
    """Validate, deduplicate, audit, and enqueue a Gorgias event.

    Authentication deliberately precedes rate limiting, and the durable
    idempotency insert remains after parsing, tenant validation, and replay
    checks. Those ordering guarantees are part of the webhook contract.
    """
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > _MAX_WEBHOOK_BODY_BYTES:
                return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        except ValueError:
            return JSONResponse(status_code=400, content={"error": "invalid_content_length"})

    chunks: list[bytes] = []
    body_size = 0
    async for chunk in request.stream():
        body_size += len(chunk)
        if body_size > _MAX_WEBHOOK_BODY_BYTES:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        chunks.append(chunk)
    raw_body = b"".join(chunks)

    if not verify_signature(
        raw_body,
        request.headers.get("X-Gorgias-Signature"),
        request.query_params.get("secret"),
    ):
        return JSONResponse(status_code=401, content={"error": "invalid_signature"})

    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        log_event(logger, "WARNING", "Rate limit exceeded", client_ip=client_ip)
        return JSONResponse(status_code=429, content={"error": "rate_limited"})

    event = parse_event(raw_body)
    if event is None:
        return JSONResponse(status_code=400, content={"error": "malformed_payload"})

    if event["tenant_id"] != tenant_id:
        log_event(
            logger,
            "WARNING",
            "Tenant mismatch in webhook",
            url_tenant=tenant_id,
            event_tenant=event["tenant_id"],
        )
        return JSONResponse(status_code=404, content={"error": "tenant_not_found"})

    ticket_id = event.get("ticket_id")
    message_id_str = str(event.get("message_id") or "")
    if not ticket_id or not message_id_str:
        log_event(
            logger,
            "WARNING",
            "Webhook missing ticket_id or message_id",
            tenant_id=tenant_id,
            ticket_id=ticket_id,
            message_id=message_id_str,
        )
        return JSONResponse(
            status_code=400,
            content={"error": "missing_ticket_or_message_id"},
        )

    log_event(
        logger,
        "INFO",
        "Webhook received",
        tenant_id=tenant_id,
        ticket_id=ticket_id,
        message_id=message_id_str,
        event_type=event["event_type"],
        author_type=event["author_type"],
        is_customer_message=event["is_customer_message"],
        channel=event["channel"],
    )

    if await database.is_duplicate(message_id_str):
        log_event(
            logger,
            "INFO",
            "Duplicate webhook — skipping",
            message_id=message_id_str,
            ticket_id=ticket_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "message_id": message_id_str},
        )

    if is_event_too_old(event.get("created_at")):
        log_event(
            logger,
            "WARNING",
            "Webhook event too old — rejecting",
            message_id=message_id_str,
            created_at=event.get("created_at"),
        )
        return JSONResponse(status_code=410, content={"error": "event_expired"})
    if is_event_in_future(event.get("created_at")):
        log_event(
            logger,
            "WARNING",
            "Webhook event is future-dated — rejecting",
            message_id=message_id_str,
            created_at=event.get("created_at"),
        )
        return JSONResponse(status_code=400, content={"error": "event_in_future"})

    job_id = await database.ingest_event(
        event=event,
        raw_payload=raw_body.decode("utf-8", errors="replace"),
    )
    if job_id is None:
        log_event(
            logger, "INFO", "Concurrent duplicate webhook — skipping",
            message_id=message_id_str, ticket_id=ticket_id,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "duplicate", "message_id": message_id_str},
        )

    is_customer = event.get("is_customer_message", False)

    if not is_customer:
        log_event(
            logger,
            "INFO",
            "Agent message enqueued for feedback loop",
            job_id=job_id,
            ticket_id=ticket_id,
            message_id=message_id_str,
            author_type=event["author_type"],
        )
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "job_id": job_id,
                "ticket_id": ticket_id,
                "message_id": message_id_str,
                "author_type": event["author_type"],
                "purpose": "feedback_loop",
            },
        )

    log_event(
        logger,
        "INFO",
        "Webhook processed and job enqueued",
        job_id=job_id,
        ticket_id=ticket_id,
        message_id=message_id_str,
        author_type=event["author_type"],
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "job_id": job_id,
            "ticket_id": ticket_id,
            "message_id": message_id_str,
            "author_type": event["author_type"],
            "event_type": event["event_type"],
            "intents": [
                intent.get("name")
                for intent in event.get("intents", [])
                if isinstance(intent, dict)
            ],
        },
    )
