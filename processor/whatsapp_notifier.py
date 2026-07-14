"""WhatsApp notifier — sends IMMEDIATE-ticket alerts to the owner's WhatsApp.

Delivers via the local whatsapp-connect service (Baileys bridge). The route URL
is read from WHATSAPP_SEND_URL and the dedicated WA_SEND_SECRET is sent only as
a Bearer credential. Fail-soft: never raises into the orchestrator.

"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

from logging_setup import get_logger, log_event

logger = get_logger(__name__)


def send_whatsapp(
    ticket_id: int,
    subject: str,
    customer_email: str,
    message_summary: str,
    reason: str,
) -> bool:
    """Send a WhatsApp alert to the owner about an IMMEDIATE/HIGH ticket.

    Retries on transient failures (409 Conflict from Baileys reconnect
    cycle, 5xx) with exponential backoff.  The Baileys bridge disconnects
    and reconnects every ~2.5 minutes, so a 409 on the first attempt
    often succeeds on retry a few seconds later.
    """
    url = os.getenv("WHATSAPP_SEND_URL", "").strip()
    send_secret = os.getenv("WA_SEND_SECRET", "").strip()
    body = (
        f"*[PRIORITY ALERT] Ticket #{ticket_id}*\n"
        f"Subject: {subject}\n"
        f"Customer: {customer_email}\n"
        f"Reason: {reason}\n"
        f"Summary: {message_summary[:200]}\n"
        f"Link: https://buttonsbebe.gorgias.com/tickets/{ticket_id}"
    )

    missing = []
    if not url:
        missing.append("WHATSAPP_SEND_URL")
    if not send_secret:
        missing.append("WA_SEND_SECRET")
    if missing:
        log_event(logger, "WARNING",
                  f"WhatsApp alert skipped — {', '.join(missing)} not set",
                  ticket_id=ticket_id, reason=reason)
        return False

    max_retries = 3
    backoff_seconds = [2, 5, 10]

    for attempt in range(max_retries + 1):
        try:
            data = json.dumps({"text": body}).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {send_secret}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                ok = 200 <= resp.status < 300
            if ok:
                if attempt > 0:
                    log_event(logger, "INFO",
                              "WhatsApp alert sent on retry",
                              ticket_id=ticket_id, attempt=attempt + 1)
                else:
                    log_event(logger, "INFO", "WhatsApp alert sent",
                              ticket_id=ticket_id)
                return True

            # Non-2xx non-retryable — log and give up
            log_event(logger, "WARNING",
                      "WhatsApp alert returned non-2xx",
                      ticket_id=ticket_id, status=resp.status)
            return False

        except urllib.error.HTTPError as exc:
            # 409 = Baileys temporarily disconnected (reconnecting)
            # 5xx = server error, transient
            # 4xx (other) = permanent, don't retry
            retryable = exc.code == 409 or exc.code >= 500
            if retryable and attempt < max_retries:
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                log_event(logger, "WARNING",
                          f"WhatsApp alert got {exc.code}, retrying in {wait}s",
                          ticket_id=ticket_id, attempt=attempt + 1,
                          max_retries=max_retries)
                time.sleep(wait)
                continue
            log_event(logger, "ERROR",
                      f"WhatsApp alert failed: HTTP {exc.code}",
                      ticket_id=ticket_id, attempts=attempt + 1)
            return False

        except Exception as exc:  # noqa: BLE001 -- alerts must never crash the loop
            if attempt < max_retries:
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                log_event(logger, "WARNING",
                          f"WhatsApp alert error, retrying in {wait}s: {exc}",
                          ticket_id=ticket_id, attempt=attempt + 1)
                time.sleep(wait)
                continue
            log_event(logger, "ERROR", f"WhatsApp alert failed: {exc}",
                      ticket_id=ticket_id, attempts=attempt + 1)
            return False
