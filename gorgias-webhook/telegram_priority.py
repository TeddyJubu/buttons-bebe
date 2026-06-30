#!/usr/bin/env python3
"""
telegram_priority.py — Send priority notifications and handle KB-gap Q&A
via a DEDICATED Telegram bot (separate from the main notification bot).

This module uses a different bot token and chat IDs from telegram_notify.py,
configured in config.json under:
  priority_telegram_bot_token   — Bot token for the priority/Q&A bot
  priority_telegram_chat_ids     — List of allowed user chat IDs

Two main functions:

1. send_priority_notification(ticket_id, priority, customer_message, draft_text,
   conversation, gorgias_url)
   — Sends a priority alert to the owner with the priority level, customer
     message, full conversation, and (optionally) the draft reply.

2. send_kb_gap_question(ticket_id, customer_message, question)
   — When the KB has no answer, asks the owner how to respond. Polls for
     the reply via getUpdates (long-polling with a timeout). When the owner
     responds, the answer is returned so the caller can store it in the KB
     and generate a draft.

Uses the Telegram Bot API directly (no external dependencies, just urllib).
"""

import json
import os
import re
import sys
import time
import logging
import urllib.request
import urllib.error
from datetime import datetime

logger = logging.getLogger("gorgias-priority-telegram")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
TELEGRAM_API_BASE = "https://api.telegram.org/bot"

# PII scrubbing (same patterns as the main bot)
_PII_RE = [
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '[email]'),
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[phone]'),
    (re.compile(r'\b(?:#?\d{4,}\b)'), '[order#]'),
    (re.compile(r'\b\d{5}(?:-\d{4})?\b'), '[zip]'),
]

def _scrub_pii(text):
    if not text or not isinstance(text, str):
        return text or ""
    for pat, repl in _PII_RE:
        text = pat.sub(repl, text)
    return text


def _truncate_for_telegram(text, max_len=4000):
    text = text or ""
    if len(text) <= max_len:
        return text
    return text[:max_len - 20] + "\n…(truncated)"


def _load_priority_bot_config():
    """Load the priority bot token and chat IDs from config.json.

    Returns:
        (bot_token, list_of_chat_ids)
    """
    token = os.environ.get("PRIORITY_TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_env = os.environ.get("PRIORITY_TELEGRAM_CHAT_IDS", "").strip()

    chat_ids = []
    if chat_ids_env:
        try:
            chat_ids = [int(x.strip()) for x in chat_ids_env.split(",") if x.strip()]
        except ValueError:
            logger.error("PRIORITY_TELEGRAM_CHAT_IDS contains non-integer; ignoring.")
            chat_ids = []

    if not token or not chat_ids:
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            if not token:
                token = cfg.get("priority_telegram_bot_token", "")
            if not chat_ids:
                cfg_ids = cfg.get("priority_telegram_chat_ids", [])
                if isinstance(cfg_ids, list):
                    chat_ids = [int(x) for x in cfg_ids]
                elif isinstance(cfg_ids, (int, float)):
                    chat_ids = [int(cfg_ids)]
        except (OSError, ValueError, TypeError) as e:
            logger.error(f"Failed to load priority bot config: {e}")

    if not token:
        raise ValueError("Priority bot token not configured. Set priority_telegram_bot_token in config.json")
    if not chat_ids:
        raise ValueError("Priority bot chat IDs not configured. Set priority_telegram_chat_ids in config.json")

    return token, chat_ids


def _send_to_chat(token, chat_id, text, parse_mode=None, reply_markup=None):
    """Send a message to a single chat ID. Returns the API response dict."""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    body = json.dumps(payload).encode("utf-8")
    url = f"{TELEGRAM_API_BASE}{token}/sendMessage"

    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                msg_id = result["result"]["message_id"]
                logger.debug(f"Priority message sent to {chat_id}: id={msg_id}")
                return {"ok": True, "chat_id": chat_id, "message_id": msg_id}
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


def _send_all(token, chat_ids, text):
    """Send text to all configured chat IDs. Returns list of results."""
    results = []
    for chat_id in chat_ids:
        result = _send_to_chat(token, chat_id, text)
        results.append(result)
    return results


def _get_updates(token, offset=None, timeout=30):
    """Poll for updates (messages) from the bot's getUpdates endpoint.

    Uses long-polling: blocks up to `timeout` seconds waiting for messages.
    Returns a list of update objects, or [] on error.
    """
    url = f"{TELEGRAM_API_BASE}{token}/getUpdates"
    payload = {"timeout": timeout}
    if offset:
        payload["offset"] = offset
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                return result.get("result", [])
            else:
                logger.error(f"getUpdates error: {result}")
                return []
    except Exception as e:
        logger.error(f"getUpdates failed: {e}")
        return []


def _extract_reply_text(updates, allowed_chat_ids):
    """Extract the text of the first text message from a list of updates.

    Returns the message text, or None if no text message found.
    """
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        chat_id = message.get("chat", {}).get("id")
        if chat_id not in allowed_chat_ids:
            logger.debug(f"Ignoring message from unauthorized chat_id={chat_id}")
            continue
        text = message.get("text", "").strip()
        if text:
            return text, chat_id
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_priority_notification(ticket_id, priority, customer_message, *,
                                draft_text=None, conversation=None,
                                gorgias_ticket_url=None):
    """Send a priority-level notification to the owner via the priority bot.

    Args:
        ticket_id            — the Gorgias ticket ID
        priority             — priority level (urgent/high/normal/low)
        customer_message     — the customer's message (will be PII-scrubbed)
        draft_text           — optional draft reply text (if draft was created)
        conversation         — optional list of message dicts (conversation history)
        gorgias_ticket_url   — optional direct link to the ticket in Gorgias

    Returns:
        list of results from _send_to_chat for each chat ID.
    """
    token, chat_ids = _load_priority_bot_config()

    # Priority banner
    banners = {
        "urgent": "🚨 CRITICAL — ACT WITHIN MINUTES",
        "high": "⚠️ HIGH — ACT WITHIN A FEW HOURS",
        "normal": "📋 NORMAL — QUEUE OR AUTO-DRAFT",
        "low": "📝 LOW — QUEUE OR AUTO-DRAFT",
    }
    banner = banners.get(priority, f"📋 {priority.upper()}")

    lines = []
    lines.append(f"{banner}")
    lines.append(f"Ticket #{ticket_id}")
    lines.append("")

    # Customer message
    lines.append("💬 CUSTOMER MESSAGE:")
    lines.append(_scrub_pii(customer_message or "(no message)"))
    lines.append("")

    # Conversation (if provided)
    if conversation:
        lines.append(f"📜 CONVERSATION ({len(conversation)} messages):")
        for msg in conversation[-5:]:  # last 5 messages
            sender = "Agent" if msg.get("from_agent") else "Customer"
            body = _scrub_pii((msg.get("body_text") or "")[:200])
            lines.append(f"  [{sender}]: {body}")
        lines.append("")

    # Draft reply (if provided)
    if draft_text:
        lines.append("📝 DRAFT REPLY:")
        lines.append("-" * 40)
        lines.append(_scrub_pii(draft_text))
        lines.append("-" * 40)
        lines.append("")

    # Gorgias link
    if gorgias_ticket_url:
        lines.append(f"🔗 {gorgias_ticket_url}")
    else:
        lines.append(f"🔗 https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}")

    text = "\n".join(lines)
    text = _truncate_for_telegram(text)

    return _send_all(token, chat_ids, text)


def send_kb_gap_question(ticket_id, customer_message, question=None):
    """Ask the owner a KB-gap question and wait for their reply.

    When the KB has no answer for a customer's question, we ask the owner
    via the priority bot. We poll for the owner's reply (long-polling with
    a timeout). When they respond, the answer is returned so the caller
    can:
      1. Store it in the KB (via kb_writeback.record_owner_answer)
      2. Generate a draft reply using the new answer

    Args:
        ticket_id         — the Gorgias ticket ID (for context in the message)
        customer_message  — the customer's original question (PII-scrubbed)
        question          — optional specific question to ask the owner;
                            if not provided, uses the customer message

    Returns:
        The owner's reply text (str), or None if no reply received within
        the timeout period.

    Raises:
        ValueError if the bot is not configured.
    """
    token, chat_ids = _load_priority_bot_config()

    ask_text = question or customer_message or "(no question text)"

    # Send the question to the owner
    lines = [
        f"❓ KB GAP — ticket #{ticket_id}",
        "",
        "I don't have a KB answer for this customer question. How should I respond?",
        "",
        f"Customer asked: \"{_scrub_pii(ask_text)}\"",
        "",
        f"🔗 https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
        "",
        "Reply here with your answer and I'll use it to draft a reply.",
    ]
    text = "\n".join(lines)
    results = _send_all(token, chat_ids, text)

    sent_ok = any(r.get("ok") for r in results)
    if not sent_ok:
        logger.error("Failed to send KB gap question to owner.")
        return None

    logger.info("KB gap question sent for ticket #%s, waiting for reply...", ticket_id)

    # Poll for the owner's reply (up to 5 minutes, in 30-second long-poll cycles)
    max_wait_cycles = 10  # 10 cycles × 30s = 5 minutes
    offset = None

    for cycle in range(max_wait_cycles):
        updates = _get_updates(token, offset=offset, timeout=30)
        if not updates:
            continue

        # Update offset to acknowledge these updates
        for u in updates:
            update_id = u.get("update_id")
            if update_id:
                offset = max(offset or 0, update_id + 1)

        # Look for a text reply from an allowed chat
        reply_text, reply_chat = _extract_reply_text(updates, chat_ids)
        if reply_text:
            logger.info("Received owner reply for ticket #%s: %s", ticket_id, reply_text[:100])
            return reply_text

    logger.warning("No owner reply received for ticket #%s after 5 minutes.", ticket_id)
    return None


# ---------------------------------------------------------------------------
# Async (non-blocking) KB gap Q&A — background thread handles the polling
# ---------------------------------------------------------------------------

# Track which ticket IDs have an active KB-gap poll so we don't start
# duplicate background threads for the same ticket (e.g. on webhook retries).
_active_kb_gap_polls = set()
_active_kb_gap_lock = __import__("threading").Lock()


def send_kb_gap_question_async(ticket_id, customer_message, question=None):
    """Send a KB-gap question to the owner and start a background polling thread.

    This is NON-BLOCKING: it sends the Telegram question immediately and starts
    a daemon thread that polls for the owner's reply. When the reply arrives,
    the background thread:
      1. Stores the answer in the KB (via kb_writeback.record_owner_answer)
      2. Regenerates the draft (via draft_engine.generate_draft)
      3. Posts the updated draft as a second internal note in Gorgias
      4. Sends a follow-up Telegram notification with the new draft

    The webhook handler does NOT wait for any of this — it returns 200 to
    Gorgias within seconds, and the background thread handles the rest.

    Args:
        ticket_id         — the Gorgias ticket ID
        customer_message  — the customer's original question (PII-scrubbed)
        question          — optional specific question to ask the owner

    Returns:
        True if the question was sent successfully, False otherwise.
    """
    import threading

    # Prevent duplicate background polls for the same ticket
    with _active_kb_gap_lock:
        if ticket_id in _active_kb_gap_polls:
            logger.info("KB gap poll already active for ticket #%s — skipping duplicate.", ticket_id)
            return True
        _active_kb_gap_polls.add(ticket_id)

    token, chat_ids = _load_priority_bot_config()
    ask_text = question or customer_message or "(no question text)"

    # Send the question to the owner (synchronous, but fast — one HTTP call)
    lines = [
        f"❓ KB GAP — ticket #{ticket_id}",
        "",
        "I don't have a KB answer for this customer question. How should I respond?",
        "",
        f"Customer asked: \"{_scrub_pii(ask_text)}\"",
        "",
        f"🔗 https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
        "",
        "Reply here with your answer and I'll use it to draft a reply.",
    ]
    text = "\n".join(lines)
    results = _send_all(token, chat_ids, text)
    sent_ok = any(r.get("ok") for r in results)

    if not sent_ok:
        logger.error("Failed to send KB gap question to owner for ticket #%s.", ticket_id)
        with _active_kb_gap_lock:
            _active_kb_gap_polls.discard(ticket_id)
        return False

    logger.info("KB gap question sent for ticket #%s — background polling started.", ticket_id)

    # Start the background polling thread (daemon so it never blocks shutdown)
    def _poll_and_handle():
        """Background thread: poll for owner reply, store in KB, regenerate draft."""
        try:
            max_wait_cycles = 20  # 20 cycles × 30s = 10 minutes
            offset = None
            owner_answer = None

            for cycle in range(max_wait_cycles):
                updates = _get_updates(token, offset=offset, timeout=30)
                if not updates:
                    continue

                for u in updates:
                    update_id = u.get("update_id")
                    if update_id:
                        offset = max(offset or 0, update_id + 1)

                reply_text, reply_chat = _extract_reply_text(updates, chat_ids)
                if reply_text:
                    owner_answer = reply_text
                    break

            if not owner_answer:
                logger.warning("No owner reply for KB gap ticket #%s after 10 minutes.", ticket_id)
                # Send a timeout notice to the owner
                timeout_lines = [
                    f"⏰ KB GAP TIMEOUT — ticket #{ticket_id}",
                    "",
                    "I didn't receive an answer within 10 minutes.",
                    "The ticket still has the KB gap note. You can reply manually in Gorgias:",
                    f"🔗 https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
                ]
                _send_all(token, chat_ids, "\n".join(timeout_lines))
                return

            logger.info("Owner replied to KB gap for ticket #%s: %s", ticket_id, owner_answer[:100])

            # 1. Store the answer in the KB
            try:
                import kb_writeback
                kb_path = kb_writeback.record_owner_answer(
                    question=(customer_message or "")[:200],
                    answer=owner_answer,
                    source_ticket_id=ticket_id,
                    commit=True,
                    ingest=True,
                )
                if kb_path:
                    logger.info("Stored owner answer in KB: %s", kb_path)
            except Exception as e:
                logger.error("kb_writeback failed for ticket #%s: %s", ticket_id, e)

            # 2. Regenerate the draft with the new KB knowledge
            try:
                import draft_engine
                import pipeline
                import json as _json

                # Re-fetch the ticket context (the original ctx may be stale)
                config_path = os.path.join(SCRIPT_DIR, "config.json")
                with open(config_path, "r") as f:
                    cfg = _json.load(f)

                # Decrypt API key if needed
                import base64 as _b64
                import hashlib as _hl
                api_key = cfg.get("gorgias_api_key", "")
                if api_key.startswith("enc:"):
                    from cryptography.fernet import Fernet
                    with open("/etc/gorgias-wh-key", "rb") as kf:
                        mk = kf.read().strip()
                    derived = _b64.b64encode(_hl.sha256(mk).digest())
                    api_key = Fernet(derived).decrypt(api_key[4:].encode()).decode()

                ctx = pipeline.fetch_ticket_context(
                    {"trigger": "ticket-message-created", "ticket": {"id": str(ticket_id)}},
                    cfg["gorgias_base_url"],
                    cfg["gorgias_username"],
                    api_key,
                )

                import classifier as _cls
                classification = _cls.classify(ctx)

                result = draft_engine.generate_draft(ctx, classification)
                logger.info("Draft regenerated for ticket #%s: should_post=%s kb_gap=%s",
                            ticket_id, result.should_post, result.kb_gap)

                # 3. Post the updated draft as a second internal note
                if result.draft_text and result.should_post:
                    import gorgias_api

                    header = (
                        f"🤖 Hermes draft (UPDATED after owner KB answer) | "
                        f"category={result.category} | "
                        f"confidence={result.confidence} | "
                        f"kb_sources={result.kb_sources or '[]'}"
                    )
                    note_body = header + "\n" + ("-" * 60) + "\n" + result.draft_text

                    # Check if we're allowed to actually write
                    confirm = (
                        os.environ.get("WORKFLOW_A_CONFIRM", "").strip() in ("1", "true", "yes")
                        or bool(cfg.get("workflow_a_confirm", False))
                    )

                    gorgias_api.post_internal_note(
                        cfg["gorgias_base_url"],
                        cfg["gorgias_username"],
                        api_key,
                        ticket_id,
                        note_body,
                        int(cfg.get("gorgias_agent_user_id", "") or gorgias_api.DEFAULT_AGENT_USER_ID),
                        confirm=confirm,
                    )
                    logger.info("Updated internal note posted for ticket #%s", ticket_id)

                # 4. Send a follow-up Telegram notification with the new draft
                send_priority_notification(
                    ticket_id=ticket_id,
                    priority="normal",
                    customer_message=customer_message,
                    draft_text=result.draft_text,
                    gorgias_ticket_url=f"https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
                )
                logger.info("Follow-up notification sent for ticket #%s", ticket_id)

            except Exception as e:
                logger.error("Draft regeneration failed for ticket #%s: %s", ticket_id, e)

        except Exception as e:
            logger.error("Background KB gap handler failed for ticket #%s: %s", ticket_id, e)
        finally:
            with _active_kb_gap_lock:
                _active_kb_gap_polls.discard(ticket_id)

    thread = threading.Thread(target=_poll_and_handle, daemon=True, name=f"kb-gap-{ticket_id}")
    thread.start()

    return True


def send_simple_message(text):
    """Send a simple text message via the priority bot (no formatting).

    Args:
        text — the message text

    Returns:
        list of results from _send_to_chat for each chat ID.
    """
    token, chat_ids = _load_priority_bot_config()
    return _send_all(token, chat_ids, _truncate_for_telegram(text))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Priority Telegram bot for Gorgias pipeline")
    parser.add_argument("--test", action="store_true", help="Send a test message")
    parser.add_argument("--test-priority", help="Send a test priority notification (urgent/high/normal/low)")
    parser.add_argument("--test-qa", action="store_true", help="Test the KB gap Q&A flow")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    if args.test:
        result = send_simple_message("✅ Priority bot test — notifications are working!")
        print(json.dumps(result, indent=2))
        return

    if args.test_priority:
        result = send_priority_notification(
            ticket_id=99999,
            priority=args.test_priority,
            customer_message="Hi, I need to change my shipping address before the order ships!",
            draft_text="hi! we can help with that — please send us the correct address and we'll update it right away.",
            gorgias_ticket_url="https://buttonsbebe.gorgias.com/app/ticket/99999",
        )
        print(json.dumps(result, indent=2))
        return

    if args.test_qa:
        print("Sending KB gap question and waiting for reply (up to 5 minutes)...")
        reply = send_kb_gap_question(
            ticket_id=99999,
            customer_message="Can you tell me about your return policy?",
        )
        if reply:
            print(f"\nOwner replied: {reply}")
        else:
            print("\nNo reply received within timeout.")
        return

    parser.error("Provide --test, --test-priority <level>, or --test-qa")


if __name__ == "__main__":
    main()