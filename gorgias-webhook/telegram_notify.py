#!/usr/bin/env python3
"""
telegram_notify.py — Send notifications to Telegram from the Gorgias pipeline.

Uses the Telegram Bot API directly (no external dependencies, just urllib).
Reads bot token and chat ID from config.json or environment variables.

Config (in /root/gorgias-webhook/config.json):
    telegram_bot_token   — Bot token from @BotFather
    telegram_chat_id     — Numeric chat ID to send messages to

Environment overrides:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

Usage (CLI):
    python3 telegram_notify.py "Hello, world!"
    python3 telegram_notify.py --test
    python3 telegram_notify.py --ticket-json ticket_contexts/ticket_123.json

Usage (imported by pipeline/server):
    from telegram_notify import send_message, send_ticket_notification
    send_message("Simple text message")
    send_ticket_notification(ticket_context_dict)
"""

import json
import os
import re
import sys
import logging
import urllib.request
import urllib.error
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
try:
    import dotenv_loader; dotenv_loader.load()
except ImportError:
    pass

logger = logging.getLogger("gorgias-telegram")
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

TELEGRAM_API_BASE = "https://api.telegram.org/bot"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_telegram_config():
    """Load Telegram bot token and chat IDs from env or config.json.

    Returns:
        (bot_token, list_of_chat_ids) — chat_ids is a list of integers.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()

    # Collect all chat IDs from env and config
    chat_ids = []

    # Env: TELEGRAM_CHAT_IDS (comma-separated) takes priority
    env_ids = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
    if env_ids:
        try:
            chat_ids = [int(x.strip()) for x in env_ids.split(",") if x.strip()]
        except ValueError:
            logger.error("TELEGRAM_CHAT_IDS contains a non-integer token; ignoring env var.")
            chat_ids = []

    # Fall back to single TELEGRAM_CHAT_ID env var
    single_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if single_id and not chat_ids:
        try:
            chat_ids = [int(single_id)]
        except ValueError:
            logger.error("TELEGRAM_CHAT_ID is not an integer; ignoring env var.")
            chat_ids = []

    if not token or not chat_ids:
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            token = token or cfg.get("telegram_bot_token", "")
            # Prefer the list of chat IDs, fall back to single
            cfg_ids = cfg.get("telegram_chat_ids", [])
            if not chat_ids and cfg_ids:
                if isinstance(cfg_ids, (int, float)):
                    chat_ids = [int(cfg_ids)]
                else:
                    chat_ids = [int(x) for x in cfg_ids]
            elif not chat_ids:
                single = cfg.get("telegram_chat_id")
                if single is not None:
                    try:
                        chat_ids = [int(single)]
                    except ValueError:
                        chat_ids = []
        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Failed to load Telegram config: {e}")

    if not token:
        raise ValueError("Telegram bot token not configured. Set TELEGRAM_BOT_TOKEN env var or telegram_bot_token in config.json")
    if not chat_ids:
        raise ValueError("Telegram chat ID not configured. Set TELEGRAM_CHAT_IDS env var or telegram_chat_ids in config.json")

    return token, chat_ids


# ---------------------------------------------------------------------------
# Core send function
# ---------------------------------------------------------------------------

def _send_to_chat(token, chat_id, text, parse_mode=None, disable_web_page_preview=True):
    """Send a message to a single chat ID. Returns the API response dict."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    body = json.dumps(payload).encode("utf-8")
    url = f"{TELEGRAM_API_BASE}{token}/sendMessage"

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                logger.debug(f"Telegram message sent to {chat_id}: id={result['result']['message_id']}")
                return {"ok": True, "chat_id": chat_id, "message_id": result["result"]["message_id"]}
            else:
                logger.error(f"Telegram API error for {chat_id}: {result}")
                return {"ok": False, "chat_id": chat_id, "error": str(result.get("description", "unknown"))}
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8")
        logger.error(f"Telegram send to {chat_id} failed (HTTP {e.code}): {error_body}")
        return {"ok": False, "chat_id": chat_id, "error": f"HTTP {e.code}: {error_body}"}
    except Exception as e:
        logger.error(f"Telegram send to {chat_id} failed: {e}")
        return {"ok": False, "chat_id": chat_id, "error": str(e)}


def send_message(text, parse_mode=None, disable_web_page_preview=True):
    """Send a text message to all configured Telegram chats.

    Args:
        text                           — the message text
        parse_mode                     — "HTML" or "Markdown" (optional)
        disable_web_page_preview       — if True, disables link previews

    Returns:
        list of dicts, one per chat ID: {"ok": True/False, "chat_id": N, ...}
    """
    token, chat_ids = _load_telegram_config()
    results = []
    for chat_id in chat_ids:
        result = _send_to_chat(token, chat_id, text, parse_mode, disable_web_page_preview)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# PII scrubber — applied to customer text before it enters Telegram messages.
# Same patterns as server._scrub_pii; duplicated here to avoid circular import.
# ---------------------------------------------------------------------------
_PII_RE = [
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '[email]'),
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[phone]'),
    (re.compile(r'\b(?:#?\d{4,}\b)'), '[order#]'),
    (re.compile(r'\b\d{5}(?:-\d{4})?\b'), '[zip]'),
]

def _scrub_pii_telegram(text):
    if not text or not isinstance(text, str):
        return text or ""
    for pat, repl in _PII_RE:
        text = pat.sub(repl, text)
    return text


# ---------------------------------------------------------------------------
# Ticket notification formatter
# ---------------------------------------------------------------------------

def _truncate(text, max_len=300):
    if not text:
        return "(empty)"
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _format_orders_summary(order_context):
    """Format the Shopify order context into a readable summary."""
    if not order_context or not order_context.get("orders"):
        return "No orders found"

    lines = []
    for order in order_context["orders"][:5]:  # max 5 orders
        name = order.get("name", "?")
        fin = order.get("financial_status", "?")
        ful = order.get("fulfillment_status", "unfulfilled") or "unfulfilled"
        items = order.get("line_items", [])
        item_count = len(items)
        item_summary = ", ".join(
            f"{li.get('quantity', 1)}x {li.get('title', '?')[:30]}"
            for li in items[:3]
        )
        if item_count > 3:
            item_summary += f" (+{item_count - 3} more)"
        lines.append(f"  #{name} | {fin} | {ful} | {item_summary}")

    total = order_context.get("orders_count", len(order_context.get("orders", [])))
    header = f"Orders ({total} total, showing {min(len(order_context.get('orders', [])), 5)}):"
    return header + "\n" + "\n".join(lines)


def _format_messages_summary(messages, max_msgs=5):
    """Format the conversation messages into a readable summary."""
    if not messages:
        return "(no messages)"

    lines = []
    # Show the most recent messages
    recent = messages[-max_msgs:] if len(messages) > max_msgs else messages
    for msg in recent:
        from_agent = msg.get("from_agent", False)
        sender = "Agent" if from_agent else "Customer"
        raw_body = msg.get("body_text", "") or ""
        body = _truncate(_scrub_pii_telegram(raw_body), 200)
        channel = msg.get("channel", "?")
        lines.append(f"  [{sender} via {channel}]: {body}")

    if len(messages) > max_msgs:
        header = f"Conversation ({len(messages)} messages, showing last {max_msgs}):"
    else:
        header = f"Conversation ({len(messages)} messages):"
    return header + "\n" + "\n".join(lines)


def send_ticket_notification(ctx_dict):
    """Send a formatted ticket context notification to Telegram.

    Args:
        ctx_dict — a TicketContext.to_dict() output (or similar dict with
                   ticket, messages, customer, order_context keys)

    Returns:
        Same as send_message().
    """
    ticket = ctx_dict.get("ticket") or {}
    customer = ctx_dict.get("customer") or {}
    order_ctx = ctx_dict.get("order_context") or {}
    messages = ctx_dict.get("messages") or []

    ticket_id = ctx_dict.get("ticket_id", ticket.get("id", "?"))
    subject = _scrub_pii_telegram(ticket.get("subject", "(no subject)"))
    status = ticket.get("status", "?")
    channel = ticket.get("channel", "?")
    priority = ticket.get("priority", "?")

    cust_name = customer.get("name", "?")
    cust_email = customer.get("email", "?")
    cust_phone = customer.get("channels", [])
    phone = ""
    if isinstance(cust_phone, list):
        for ch in cust_phone:
            if isinstance(ch, dict) and ch.get("type") == "phone":
                phone = ch.get("address", "")
                break

    from_agent = ctx_dict.get("from_agent")
    event_type = ctx_dict.get("event_type", "?")
    errors = ctx_dict.get("errors", [])
    fetched_at = ctx_dict.get("fetched_at", "?")

    # Build the message
    lines = []
    lines.append("🔔 GORGIAS WEBHOOK TRIGGERED")
    lines.append(f"Event: {event_type}")
    lines.append(f"From agent: {from_agent}")
    lines.append("")
    lines.append("📋 TICKET")
    lines.append(f"  ID: #{ticket_id}")
    lines.append(f"  Subject: {subject}")
    lines.append(f"  Status: {status}")
    lines.append(f"  Channel: {channel}")
    lines.append(f"  Priority: {priority}")
    lines.append("")
    def _mask_email(e):
        if not e or e == "?":
            return e
        parts = str(e).split("@", 1)
        return (parts[0][:1] + "***@" + parts[1]) if len(parts) == 2 else "***"

    def _mask_phone(p):
        digits = "".join(c for c in str(p) if c.isdigit())
        return ("*" * max(0, len(digits) - 4) + digits[-4:]) if digits else "***"

    lines.append("👤 CUSTOMER")
    lines.append(f"  Name: {cust_name[:1]}***" if cust_name and cust_name != "?" else "  Name: ?")
    lines.append(f"  Email: {_mask_email(cust_email)}")
    if phone:
        lines.append(f"  Phone: {_mask_phone(phone)}")
    lines.append(f"  Customer ID: {customer.get('id', '?')}")
    lines.append("")
    lines.append("💬 " + _format_messages_summary(messages))
    lines.append("")
    lines.append("📦 " + _format_orders_summary(order_ctx))
    lines.append("")

    if errors:
        lines.append(f"⚠️ ERRORS ({len(errors)}):")
        for err in errors:
            lines.append(f"  - {err}")
        lines.append("")

    lines.append(f"🔗 View: https://buttonsbebe.gorgias.com/tickets/{ticket_id}")
    lines.append(f"🕐 Fetched: {fetched_at}")

    text = "\n".join(lines)

    # Telegram message limit is 4096 chars
    if len(text) > 4000:
        text = text[:3990] + "\n...(truncated)"

    return send_message(text)


def send_draft_notification(ctx_dict, draft_result):
    """Send a post-draft Telegram notification for customer-message tickets.

    This is sent AFTER Workflow A has classified + drafted. It contains ONLY
    the draft reply text — no analysis data, no conversation history, no
    metadata. The user explicitly requested: only the reply, nothing else.

    Args:
        ctx_dict     — a TicketContext.to_dict() output (ticket, messages,
                       customer, order_context, etc.)
        draft_result — a DraftResult.as_dict() (draft_text, priority, category,
                       confidence, kb_sources, should_post, is_escalation,
                       kb_gap, reason, model_used)

    Returns:
        Same as send_message().
    """
    ticket = ctx_dict.get("ticket") or {}
    ticket_id = ctx_dict.get("ticket_id", ticket.get("id", "?"))
    draft_text = draft_result.get("draft_text", "")

    # Only the draft reply — no analysis, no conversation, no metadata
    draft_clean = _scrub_pii_telegram(draft_text or "").strip()
    if not draft_clean:
        draft_clean = "(no draft text generated)"

    text = f"📝 Draft for ticket #{ticket_id}:\n\n{draft_clean}"

    # Telegram message limit is 4096 chars
    if len(text) <= 4000:
        return send_message(text)
    else:
        return send_message(text[:3990] + "\n…(truncated)")


# ---------------------------------------------------------------------------
# Owner alerts (Stage 5, Task 15) — escalations, KB-gap asks, weekly report.
# ---------------------------------------------------------------------------
# These go to the OWNER chat(s) ONLY (the same telegram_chat_ids used by
# send_ticket_notification). They are NEVER customer-facing — there is no code
# path here that messages a Gorgias customer; we only ever call the Telegram
# Bot API for the configured owner chat ids.
#
# Everything routes through the single low-level _send() below, which reuses the
# existing token/chat-id plumbing (_load_telegram_config + _send_to_chat). It:
#   * honors dry_run (returns the payload WITHOUT touching the network/config),
#   * truncates to Telegram's ~4096-char limit,
#   * sends PLAIN TEXT (no parse_mode) so there is no Markdown/HTML injection
#     risk from customer-controlled snippets, and
#   * is fully resilient: a config/network error is logged and returned, never
#     raised into the caller.

TELEGRAM_MAX_CHARS = 4096
# Leave headroom under the hard 4096 limit for any trailing truncation marker.
_TELEGRAM_SAFE_CHARS = 4000
_TRUNC_MARKER = "\n…(truncated)"

# Gorgias ticket deep-link. Configurable via env / config.json
# (gorgias_ticket_url_template, with a "{id}" placeholder) so the owner can tap
# straight through to the ticket. Defaults to the Buttons Bebe Gorgias app URL.
DEFAULT_TICKET_URL_TEMPLATE = "https://buttonsbebe.gorgias.com/app/ticket/{id}"


def _ticket_url(ticket_id):
    """Build the owner-facing Gorgias ticket deep-link, or "" if unknown.

    Resolution order: GORGIAS_TICKET_URL_TEMPLATE env, then config.json
    "gorgias_ticket_url_template", then the Buttons Bebe default. The template
    must contain "{id}". Never raises — returns "" if we can't build a URL.
    """
    if ticket_id in (None, "", "?"):
        return ""
    template = os.environ.get("GORGIAS_TICKET_URL_TEMPLATE", "").strip()
    if not template:
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            template = cfg.get("gorgias_ticket_url_template", "") or ""
        except (OSError, ValueError):
            template = ""
    if not template:
        template = DEFAULT_TICKET_URL_TEMPLATE
    try:
        return template.format(id=ticket_id)
    except (KeyError, IndexError, ValueError):
        return ""


def _truncate_for_telegram(text, max_len=_TELEGRAM_SAFE_CHARS):
    """Clamp text to Telegram's message limit, appending a marker if cut."""
    text = text or ""
    if len(text) <= max_len:
        return text
    keep = max_len - len(_TRUNC_MARKER)
    if keep < 0:
        keep = 0
    return text[:keep] + _TRUNC_MARKER


def _send(text, dry_run=False, disable_web_page_preview=True):
    """Low-level owner-chat sender used by every Task-15 alert.

    Reuses the EXISTING plumbing (_load_telegram_config + _send_to_chat) so
    there is one place that knows how to reach the owner chats. Always sends
    PLAIN TEXT (parse_mode=None) to avoid Markdown/HTML injection from
    customer-controlled snippets, and truncates to the Telegram limit.

    Args:
        text                       — the message body (will be truncated).
        dry_run                    — if True, build + return the payload(s) and
                                     make NO network call and NO config load.
        disable_web_page_preview   — passed through to _send_to_chat.

    Returns:
        A dict {"ok": bool, "dry_run": bool, "results": [...], "text": str}.
        results is a per-chat list of payloads (dry-run) or API results (live).
        NEVER raises — config/network failures are caught, logged, and reported.
    """
    text = _truncate_for_telegram(text)

    if dry_run:
        # Resolve the owner chat ids if we can (so the payload is faithful), but
        # do NOT fail the dry-run just because config is missing — that is the
        # whole point of dry-run (used by tests with no live config).
        try:
            _token, chat_ids = _load_telegram_config()
        except Exception as e:  # noqa: BLE001 — dry-run must never raise
            logger.debug(f"_send dry_run: config unavailable ({e}); using placeholder chat id")
            chat_ids = ["<owner-chat>"]
        results = [
            {
                "ok": True,
                "dry_run": True,
                "chat_id": chat_id,
                "payload": {
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": disable_web_page_preview,
                },
            }
            for chat_id in chat_ids
        ]
        return {"ok": True, "dry_run": True, "results": results, "text": text}

    # Live send — resilient: never let a config/network error reach the caller.
    try:
        token, chat_ids = _load_telegram_config()
    except Exception as e:  # noqa: BLE001
        logger.error(f"Telegram owner alert not sent (config error): {e}")
        return {"ok": False, "dry_run": False, "error": str(e), "results": [], "text": text}

    results = []
    all_ok = True
    for chat_id in chat_ids:
        try:
            res = _send_to_chat(
                token, chat_id, text,
                parse_mode=None,
                disable_web_page_preview=disable_web_page_preview,
            )
        except Exception as e:  # noqa: BLE001 — _send_to_chat already guards, belt+braces
            logger.error(f"Telegram owner alert to {chat_id} failed: {e}")
            res = {"ok": False, "chat_id": chat_id, "error": str(e)}
        all_ok = all_ok and bool(res.get("ok"))
        results.append(res)
    return {"ok": all_ok, "dry_run": False, "results": results, "text": text}


def send_escalation_alert(ticket_id, *, category, priority, reason,
                          customer_message=None, dry_run=False):
    """Owner alert for an URGENT/sensitive escalation. OWNER chat(s) only.

    Concise, plain-text alert so Chaim can jump on a sensitive ticket
    (refund/chargeback/dispute/legal/etc.). Includes a short, sanitized snippet
    of the customer's message and a deep-link to the Gorgias ticket if known.

    Args:
        ticket_id        — the Gorgias ticket id.
        category         — classifier category (e.g. "refund").
        priority         — priority/urgency string (e.g. "high", "urgent").
        reason           — short why-escalated string.
        customer_message — optional customer text; truncated + newline-collapsed.
        dry_run          — build/return the payload WITHOUT sending.

    Returns:
        The dict from _send(). NEVER raises.
    """
    lines = [
        f"⚠️ ESCALATION — ticket #{ticket_id} "
        f"({_clean(category)}/{_clean(priority)})",
        f"Reason: {_clean(reason) or '(none given)'}",
    ]
    snippet = _snippet(customer_message)
    if snippet:
        lines.append(f"Customer said: \"{snippet}\"")
    url = _ticket_url(ticket_id)
    if url:
        lines.append(f"🔗 {url}")
    lines.append("(owner alert — a human should review and respond)")
    return _send("\n".join(lines), dry_run=dry_run)


def send_kb_gap_question(ticket_id, *, customer_message, dry_run=False):
    """Owner ask for a KB-gap ticket: "how should I answer?". OWNER chat(s) only.

    The agent had no confident KB answer, so it asks the owner how to respond.
    The owner's reply can later grow the KB (kb_writeback — not wired here).

    Args:
        ticket_id        — the Gorgias ticket id.
        customer_message — the customer's question (truncated + sanitized).
        dry_run          — build/return the payload WITHOUT sending.

    Returns:
        The dict from _send(). NEVER raises.
    """
    question = _snippet(customer_message, max_len=1500) or "(no question text)"
    lines = [
        f"❓ KB GAP — ticket #{ticket_id}",
        "I don't have a KB answer for this. How should I answer?",
        "",
        f"Customer asked: \"{question}\"",
    ]
    url = _ticket_url(ticket_id)
    if url:
        lines.append(f"🔗 {url}")
    lines.append("(reply here so I can learn it for next time)")
    return _send("\n".join(lines), dry_run=dry_run)


def send_weekly_report(report_text_or_dict, dry_run=False):
    """Send a weekly metrics summary to the OWNER chat(s).

    Task 16's weekly_review.py builds the metrics; this is just the sender.
    Accepts either a pre-formatted string or a dict of metrics (which we render
    into a readable plain-text block).

    Args:
        report_text_or_dict — a str (sent as-is, with a header) or a dict of
                              {metric: value} pairs to format.
        dry_run             — build/return the payload WITHOUT sending.

    Returns:
        The dict from _send(). NEVER raises.
    """
    if isinstance(report_text_or_dict, dict):
        body = _format_weekly_report_dict(report_text_or_dict)
    else:
        body = str(report_text_or_dict or "").strip() or "(empty report)"
    header = "📊 WEEKLY REPORT — Buttons Bebe AI support agent"
    text = header + "\n" + ("=" * 40) + "\n" + body
    return _send(text, dry_run=dry_run)


# -- small formatting helpers for the owner alerts -------------------------- #
def _clean(value):
    """Collapse a short scalar to a single safe line (no newlines/leading ws)."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _snippet(text, max_len=240):
    """Sanitize a customer message for inclusion in an OWNER alert.

    Collapses newlines/whitespace, scrubs PII, and truncates. Plain text only
    — _send sends with no parse_mode, so there is no Markdown/HTML to escape,
    but we still strip the message to one tidy line so it can't break the
    alert layout.
    """
    s = _clean(text)
    if not s:
        return ""
    s = _scrub_pii_telegram(s)
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s


def _format_weekly_report_dict(d):
    """Render a metrics dict into an aligned plain-text block."""
    lines = []
    for key, value in d.items():
        label = str(key).replace("_", " ")
        if isinstance(value, dict):
            lines.append(f"{label}:")
            for k2, v2 in value.items():
                lines.append(f"  - {str(k2).replace('_', ' ')}: {v2}")
        elif isinstance(value, (list, tuple)):
            lines.append(f"{label}:")
            for item in value:
                lines.append(f"  - {item}")
        else:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) if lines else "(no metrics)"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Send Telegram notifications from the Gorgias pipeline")
    parser.add_argument("text", nargs="?", help="Simple text message to send")
    parser.add_argument("--test", action="store_true", help="Send a test message")
    parser.add_argument("--ticket-json", help="Path to a ticket context JSON file to send as notification")
    parser.add_argument("--demo-alerts", action="store_true",
                        help="DRY-RUN the Task-15 owner alerts (escalation / KB-gap / weekly). Sends nothing.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.demo_alerts:
        # DRY-RUN ONLY — proves the alert builders without touching the live chat.
        out = {
            "escalation": send_escalation_alert(
                12345, category="refund", priority="high",
                reason="customer requested a refund",
                customer_message="hi, i'd like a refund for my order please",
                dry_run=True,
            ),
            "kb_gap": send_kb_gap_question(
                12345, customer_message="do you restock the floral romper in 2T?",
                dry_run=True,
            ),
            "weekly": send_weekly_report(
                {"tickets": 42, "escalations": 3, "kb_gaps": 5}, dry_run=True,
            ),
        }
        print(json.dumps(out, indent=2))
        return

    if args.test:
        result = send_message("✅ Telegram gateway test from Gorgias pipeline. Webhook notifications are working!")
        print(json.dumps(result, indent=2))
        return

    if args.ticket_json:
        with open(args.ticket_json, "r") as f:
            ctx = json.load(f)
        result = send_ticket_notification(ctx)
        print(json.dumps(result, indent=2))
        return

    if args.text:
        result = send_message(args.text)
        print(json.dumps(result, indent=2))
        return

    parser.error("Provide text, --test, or --ticket-json <file>")


if __name__ == "__main__":
    main()