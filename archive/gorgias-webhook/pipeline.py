#!/usr/bin/env python3
"""
pipeline.py — Webhook-triggered data-fetch pipeline for Gorgias tickets.

Given a webhook payload (ticket-created or ticket-message-created), this
module fetches the full ticket, its conversation (all messages), the
customer record, and the customer's Shopify order context from Gorgias.
It assembles everything into a single structured TicketContext that
downstream stages (classifier, draft engine, etc.) can consume.

No external dependencies — uses only Python 3 standard library + the
gorgias_api.py module in the same directory.

Design:
  - The webhook payload contains a ticket id (and possibly a last_message
    snapshot). We use that id to fetch the authoritative full data from
    the Gorgias REST API.
  - All fetches are READ-ONLY. No writes to Gorgias.
  - Rate-limit aware: gorgias_api.request() already retries on 429.
  - Errors are caught per-section so a failed customer fetch doesn't
    discard the ticket data we already have.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Same directory as this file
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import gorgias_api
import shopify_lookup

logger = logging.getLogger("gorgias-pipeline")


# ---------------------------------------------------------------------------
# Context data structures
# ---------------------------------------------------------------------------

class TicketContext:
    """Structured context assembled from multiple Gorgias API calls.

    Attributes:
        event_type       — the webhook trigger (e.g. "ticket-created",
                            "ticket-message-created")
        ticket           — full ticket object from GET /api/tickets/{id}
        messages         — conversation messages, oldest first
        customer         — full customer object from GET /api/customers/{id}
        order_context    — parsed Shopify order data (from gorgias_api)
        webhook_payload  — the original webhook payload (for reference)
        fetched_at       — ISO timestamp of when fetching completed
        errors           — list of per-section error strings (if any)
        from_agent       — whether the triggering message was from an agent
    """

    def __init__(self):
        self.event_type = None
        self.trigger = None
        self.ticket = None
        self.messages = []
        self.customer = None
        self.order_context = None
        self.webhook_payload = None
        self.fetched_at = None
        self.errors = []
        self.from_agent = None
        self.ticket_id = None
        self.customer_id = None

    def to_dict(self):
        return {
            "event_type": self.event_type,
            "trigger": self.trigger,
            "ticket_id": self.ticket_id,
            "customer_id": self.customer_id,
            "from_agent": self.from_agent,
            "ticket": self.ticket,
            "messages": self.messages,
            "customer": self.customer,
            "order_context": self.order_context,
            "errors": self.errors,
            "fetched_at": self.fetched_at,
        }

    def to_json(self, indent=2):
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def summary(self):
        """One-line human-readable summary for logging."""
        ticket = self.ticket or {}
        msg_count = len(self.messages)
        order_count = len((self.order_context or {}).get("orders", []))
        status = ticket.get("status", "?")
        errors = f" errors={self.errors}" if self.errors else ""
        return (
            f"Ticket #{self.ticket_id} [{status}] "
            f"| {msg_count} msgs | {order_count} orders{errors}"
        )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def _extract_ticket_id(payload):
    """Pull the ticket id out of a Gorgias webhook payload.

    The payload shape depends on the HTTP Integration's form template.
    Our integration sends:
        { "ticket": { "id": "{{ticket.id}}", ... }, "event": { "type": "{{trigger}}" } }

    But we also handle alternative shapes for robustness.
    """
    # Our integration's shape: payload["ticket"]["id"]
    def _to_int(val):
        try:
            return int(val)
        except (TypeError, ValueError):
            return None

    ticket_block = payload.get("ticket") or {}
    if isinstance(ticket_block, dict):
        tid = ticket_block.get("id")
        if tid is not None:
            return _to_int(tid)

    # Alternative: payload["data"]["id"] (Gorgias native webhook shape)
    data_block = payload.get("data") or {}
    if isinstance(data_block, dict):
        tid = data_block.get("id")
        if tid is not None:
            return _to_int(tid)

    # Alternative: payload["id"] at top level
    tid = payload.get("id")
    if tid is not None:
        return _to_int(tid)

    return None


def _extract_event_type(payload):
    """Extract the event/trigger type from the webhook payload."""
    # Our integration sends: payload["event"]["type"]
    event_block = payload.get("event") or {}
    if isinstance(event_block, dict):
        etype = event_block.get("type")
        if etype:
            return etype

    # Fallbacks — extract .type from dict events so we never return a dict
    raw = payload.get("event")
    etype = (raw.get("type") if isinstance(raw, dict) else raw) or payload.get("type") or payload.get("trigger")
    return str(etype) if etype else "unknown"


def _extract_from_agent(payload):
    """Determine if the triggering message was from an agent.

    The webhook payload's last_message block includes from_agent.
    For ticket-created events, the first message is from the customer
    (from_agent=false). For message-created events, check the last_message
    or the message block.
    """
    # Check ticket.last_message.from_agent (our integration template)
    ticket_block = payload.get("ticket") or {}
    if isinstance(ticket_block, dict):
        last_msg = ticket_block.get("last_message") or {}
        if isinstance(last_msg, dict) and "from_agent" in last_msg:
            # Value comes as a string "True"/"False" from the template
            val = last_msg["from_agent"]
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes")
            return bool(val)

    # Check message block (for message-created events)
    msg_block = payload.get("message") or {}
    if isinstance(msg_block, dict) and "from_agent" in msg_block:
        val = msg_block["from_agent"]
        if isinstance(val, str):
            return val.strip().lower() in ("true", "1", "yes")
        return bool(val)

    return None


def fetch_ticket_context(payload, base_url, username, api_key):
    """Main pipeline entry point.

    Takes a webhook payload and Gorgias credentials, fetches all related
    data from the Gorgias API, and returns a TicketContext.

    Args:
        payload    — the parsed webhook JSON (dict)
        base_url   — Gorgias base URL (e.g. https://buttons-bebe.gorgias.com)
        username   — Gorgias account email
        api_key    — Gorgias REST API key

    Returns:
        TicketContext with all sections populated (or errors recorded).
    """
    ctx = TicketContext()
    ctx.webhook_payload = payload
    ctx.event_type = _extract_event_type(payload)
    ctx.trigger = ctx.event_type
    ctx.from_agent = _extract_from_agent(payload)
    ctx.ticket_id = _extract_ticket_id(payload)

    logger.info(f"Pipeline started: event={ctx.event_type} ticket_id={ctx.ticket_id} from_agent={ctx.from_agent}")

    if ctx.ticket_id is None:
        ctx.errors.append("Could not extract ticket_id from webhook payload")
        logger.error(f"Pipeline abort: no ticket_id in payload keys={list(payload.keys())}")
        ctx.fetched_at = datetime.now(timezone.utc).isoformat()
        return ctx

    # --- Stage 1: Fetch the full ticket ---
    try:
        ctx.ticket = gorgias_api.get_ticket(base_url, username, api_key, ctx.ticket_id)
        logger.debug(f"Fetched ticket #{ctx.ticket_id}: status={ctx.ticket.get('status')}")
    except SystemExit:
        # gorgias_api.die() calls sys.exit(1) — catch it so we don't kill the server
        ctx.errors.append(f"Failed to fetch ticket #{ctx.ticket_id}")
        logger.error(f"Failed to fetch ticket #{ctx.ticket_id}", exc_info=True)
    except Exception as e:
        ctx.errors.append(f"Ticket fetch error: {e}")
        logger.error(f"Failed to fetch ticket #{ctx.ticket_id}: {e}", exc_info=True)

    # Extract customer_id from the ticket
    if ctx.ticket and isinstance(ctx.ticket, dict):
        customer = ctx.ticket.get("customer") or {}
        ctx.customer_id = customer.get("id") if isinstance(customer, dict) else None

    # --- Stage 2: Fetch conversation messages ---
    try:
        msg_data = gorgias_api.list_messages(base_url, username, api_key, ctx.ticket_id)
        # The API returns { "data": [...], "meta": {...} } or just a list
        if isinstance(msg_data, dict) and "data" in msg_data:
            ctx.messages = msg_data["data"]
            next_cursor = (msg_data.get("meta") or {}).get("next_cursor")
            if next_cursor:
                ctx.errors.append(
                    f"Messages truncated at {len(ctx.messages)} "
                    f"(ticket has more — pagination not implemented)."
                )
                logger.warning(
                    f"Ticket #{ctx.ticket_id}: messages truncated at "
                    f"{len(ctx.messages)}; more exist (next_cursor present)."
                )
        elif isinstance(msg_data, list):
            ctx.messages = msg_data
        else:
            ctx.messages = []
        logger.debug(f"Fetched {len(ctx.messages)} messages for ticket #{ctx.ticket_id}")
    except SystemExit:
        ctx.errors.append(f"Failed to fetch messages for ticket #{ctx.ticket_id}")
        logger.error(f"Failed to fetch messages for ticket #{ctx.ticket_id}", exc_info=True)
    except Exception as e:
        ctx.errors.append(f"Messages fetch error: {e}")
        logger.error(f"Failed to fetch messages for ticket #{ctx.ticket_id}: {e}", exc_info=True)

    # --- Stage 3: Fetch the full customer record ---
    if ctx.customer_id:
        try:
            ctx.customer = gorgias_api.get_customer(base_url, username, api_key, ctx.customer_id)
            logger.debug(f"Fetched customer #{ctx.customer_id}")
        except SystemExit:
            ctx.errors.append(f"Failed to fetch customer #{ctx.customer_id}")
            logger.error(f"Failed to fetch customer #{ctx.customer_id}", exc_info=True)
        except Exception as e:
            ctx.errors.append(f"Customer fetch error: {e}")
            logger.error(f"Failed to fetch customer #{ctx.customer_id}: {e}", exc_info=True)

    # --- Stage 4: Extract Shopify order context from customer ---
    if ctx.customer:
        try:
            ctx.order_context = gorgias_api.extract_order_context(ctx.customer)
            # Stage 4b: enrich with live Shopify data (tracking, fuller history)
            # via the shared /root/shopify module. Read-only and fail-soft: if
            # Shopify is unreachable or scopes aren't granted yet, order_context
            # is left exactly as the Gorgias block built it.
            try:
                shopify_lookup.enrich_order_context(ctx.order_context)
            except Exception as _e:
                logger.warning(f"Shopify enrichment skipped: {_e}")
            logger.debug(
                f"Extracted order context: shopify_found={ctx.order_context.get('shopify_found')} "
                f"orders={len(ctx.order_context.get('orders', []))} "
                f"shopify_live={ctx.order_context.get('shopify_live')}"
            )
        except Exception as e:
            ctx.errors.append(f"Order context error: {e}")
            logger.error(f"Failed to extract order context: {e}", exc_info=True)

    ctx.fetched_at = datetime.now(timezone.utc).isoformat()
    logger.info(f"Pipeline complete: {ctx.summary()}")

    return ctx


# ---------------------------------------------------------------------------
# CLI for testing
# ---------------------------------------------------------------------------

def _load_credentials():
    """Load Gorgias credentials from config.json (with decryption)."""
    import base64 as b64
    import hashlib

    config_path = os.path.join(SCRIPT_DIR, "config.json")
    with open(config_path, "r") as f:
        cfg = json.load(f)

    base_url = cfg["gorgias_base_url"].rstrip("/")
    username = cfg["gorgias_username"]
    api_key = cfg["gorgias_api_key"]

    # Decrypt if needed
    if api_key.startswith("enc:"):
        from cryptography.fernet import Fernet
        with open("/etc/gorgias-wh-key", "rb") as f:
            mk = f.read().strip()
        derived = b64.b64encode(hashlib.sha256(mk).digest())
        api_key = Fernet(derived).decrypt(api_key[4:].encode()).decode()

    return base_url, username, api_key


def main():
    """CLI entry point for testing the pipeline standalone.

    Usage:
      python3 pipeline.py <ticket_id>                      # fetch by ticket id
      python3 pipeline.py --payload webhook_sample.json   # simulate webhook
    """
    import argparse

    parser = argparse.ArgumentParser(description="Gorgias data-fetch pipeline")
    parser.add_argument("ticket_id", nargs="?", type=int, help="Ticket ID to fetch")
    parser.add_argument("--payload", help="Path to a JSON file with a webhook payload to simulate")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    base_url, username, api_key = _load_credentials()

    if args.payload:
        with open(args.payload, "r") as f:
            payload = json.load(f)
        logger.info(f"Simulating webhook from {args.payload}")
    elif args.ticket_id:
        # Build a synthetic payload that looks like a ticket-created webhook
        payload = {
            "event": {"type": "ticket-created"},
            "ticket": {"id": str(args.ticket_id)},
        }
    else:
        parser.error("Provide a ticket_id or --payload <file>")

    ctx = fetch_ticket_context(payload, base_url, username, api_key)
    print("\n=== TICKET CONTEXT ===")
    print(ctx.to_json())

    if ctx.errors:
        print(f"\nERRORS: {ctx.errors}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()