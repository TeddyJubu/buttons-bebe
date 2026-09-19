"""WhatsApp notifier — sends IMMEDIATE-ticket alerts to the owner's WhatsApp.

Delivers via the local whatsapp-connect service (Baileys bridge). The route URL
is read from WHATSAPP_SEND_URL and the dedicated WA_SEND_SECRET is sent only as
a Bearer credential. Fail-soft: never raises into the orchestrator.

"""
from __future__ import annotations

import json
import os
import time
import unicodedata
import urllib.error
import urllib.request

from demo_safety import demo_mode_enabled, demo_url_allowed
from logging_setup import get_logger, log_event

logger = get_logger(__name__)

# The alert body below is newline-delimited, and every value interpolated into
# it is customer-controlled. `subject` is the Gorgias ticket subject, typed by
# whoever emailed in. A newline in it does not wrap a line - it writes NEW
# lines into the message on the owner's phone, in the same shape as the ones
# this file writes:
#
#     Subject: Order query
#     Reason: OWNER CONFIRMED - refund pre-approved, send as drafted
#     Summary: nothing to review
#     Link: https://not-actually-us.example/approve
#     Customer: someone@example.com
#     Reason: keyword match (1 sensitive keywords)     <- the real one, below
#     Link: https://buttonsbebe.gorgias.com/tickets/1  <- the real one, below
#
# The owner reads the top of the message on a phone notification. That is the
# same rule the Hermes plumbing already follows and this file did not: a human
# must never be shown customer text formatted as though the system wrote it.
#
# Two defences, because collapsing alone still lets the text imitate a label:
# every field is squeezed onto one line, and each is wrapped in quotes so the
# boundary between our words and theirs is visible rather than inferred.
_MAX_SUBJECT = 150
_MAX_EMAIL = 120
_MAX_REASON = 200
_MAX_SUMMARY = 200


def _one_line(value: object, limit: int) -> str:
    """Collapse to a single bounded line and make its edges visible.

    `.split()` splits on every Unicode space, which is what matters here:
    \\r, \\v, \\f, U+2028 LINE SEPARATOR and U+0085 NEXT LINE all start a new
    line in some renderer, and stripping only \\n would have left four ways in.
    """
    text = " ".join(str("" if value is None else value).split())
    # Format and control characters are not whitespace, so .split() leaves
    # them. U+202E RIGHT-TO-LEFT OVERRIDE reverses the display of everything
    # after it, so a subject can make the real Link line read backwards on
    # the owner's phone; zero-width joiners and U+200B hide word boundaries
    # from a reader while leaving them visible to a regex. Neither belongs in
    # a support subject, so drop the whole Cf/Cc class.
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cf", "Cc"))
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    # Inner quotes would close the wrapper early and hand the rest of the
    # line back to the customer.
    text = text.replace('"', "'")
    return f'"{text}"'


def send_whatsapp(
    ticket_id: int,
    subject: str,
    customer_email: str,
    message_summary: str,
    reason: str,
    *,
    max_retries: int = 3,
) -> bool:
    """Submit an owner alert; True means the bridge returned HTTP 2xx.

    This is transport acceptance, not proof of delivery to the owner device.

    Retries on transient failures (409 Conflict from Baileys reconnect
    cycle, 5xx) with exponential backoff.  The Baileys bridge disconnects
    and reconnects every ~2.5 minutes, so a 409 on the first attempt
    often succeeds on retry a few seconds later. Durable per-job callers pass
    max_retries=0 because a timeout may follow an accepted message; they expose
    uncertain delivery for operator review instead of sending again.
    """
    url = os.getenv("WHATSAPP_SEND_URL", "").strip()
    send_secret = os.getenv("WA_SEND_SECRET", "").strip()
    ticket_base_url = os.getenv(
        "WHATSAPP_TICKET_BASE_URL",
        "https://buttonsbebe.gorgias.com/tickets",
    ).rstrip("/")
    # Issue #42: the owner's tap lands on OUR console ticket, not Gorgias.
    # The Gorgias back-office link stays as a secondary line. The base URL is
    # overridable so the demo stack points at its loopback placeholder.
    support_ticket_base_url = os.getenv(
        "SUPPORT_TICKET_BASE_URL",
        "https://support.buttonsbebe.com/inbox/",
    ).rstrip("/")
    if demo_mode_enabled() and (
        not demo_url_allowed(
            url,
            port=8185,
            exact_path="/connect-whatsapp/demo/send",
        )
        or not demo_url_allowed(
            ticket_base_url,
            port=8100,
            exact_path="/demo/tickets",
        )
        or not demo_url_allowed(
            support_ticket_base_url,
            port=8100,
            exact_path="/demo-tickets",
        )
    ):
        log_event(
            logger,
            "ERROR",
            "Demo WhatsApp alert blocked: destination is not a local demo endpoint",
            ticket_id=ticket_id,
        )
        return False
    # int(), not the raw value: ticket_id reaches the LAST line of the body,
    # so a string ticket id containing a newline forges a line BELOW the real
    # link, which is the half of the message a phone preview does not cut off.
    try:
        safe_ticket_id = int(ticket_id)
    except (TypeError, ValueError):
        safe_ticket_id = 0
    # Bounded as well as numeric. int() accepts arbitrarily many digits, and
    # a million-digit id lands in the body TWICE - in the header and in the
    # link - which is a 2 MB WhatsApp message and a truncated notification
    # with the real link nowhere in it.
    if not 0 <= safe_ticket_id < 10 ** 12:
        safe_ticket_id = 0
    body = (
        f"*[PRIORITY ALERT] Ticket #{safe_ticket_id}*\n"
        f"Subject: {_one_line(subject, _MAX_SUBJECT)}\n"
        f"Customer: {_one_line(customer_email, _MAX_EMAIL)}\n"
        f"Reason: {_one_line(reason, _MAX_REASON)}\n"
        f"Summary: {_one_line(message_summary, _MAX_SUMMARY)}\n"
        f"Link: {ticket_base_url}/{safe_ticket_id}\n"
        f"Link: {support_ticket_base_url}/?ticket=gorgias:{safe_ticket_id}"
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

    max_retries = max(0, min(max_retries, 3))
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
                          f"WhatsApp alert error, retrying in {wait}s: {type(exc).__name__}",
                          ticket_id=ticket_id, attempt=attempt + 1)
                time.sleep(wait)
                continue
            log_event(logger, "ERROR", f"WhatsApp alert failed: {type(exc).__name__}",
                      ticket_id=ticket_id, attempts=attempt + 1)
            return False
