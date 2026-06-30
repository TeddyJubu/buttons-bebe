#!/usr/bin/env python3
"""
Gorgias Webhook Receiver
========================
A standalone HTTP server that receives webhook events from Gorgias HTTP Integrations
(ticket created, ticket updated, message created) and can call back to the Gorgias REST API
to create customers, create tickets, and send replies.

Based on the Gorgias tutorial:
https://developers.gorgias.com/docs/receive-and-respond-to-tickets-from-a-third-party-app

NO external dependencies — uses only Python 3 standard library.

Setup:
  1. Edit config.json with your Gorgias domain, credentials, and secret token
  2. Run:  python3 server.py
  3. In Gorgias: Settings -> Integrations -> HTTP Integrations -> Create integration
     - URL:  http://YOUR-VPS-IP:8080/webhook
     - Headers (optional): X-Webhook-Secret: <your secret_token from config.json>
     - Triggers: Ticket created, Ticket message created

Endpoints:
  GET  /health      -> health check (returns 200 OK)
  POST /webhook     -> receives Gorgias webhook events
  GET  /test        -> simple browser-friendly test page
"""

import json
import os
import sys
import base64
import difflib
import hashlib
import hmac
import logging
import re
import time
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone

# Load .env before any project module reads config or env vars
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dotenv_loader; dotenv_loader.load()

# Pipeline for data fetching (ticket, messages, customer, orders)
import pipeline

# Workflow A (Stage 2, Task 8): classify -> draft -> post an INTERNAL note for a
# human to review. These modules NEVER message a customer:
#   * classifier.classify(ctx)         — pure, offline, no network/LLM key needed
#   * draft_engine.generate_draft(ctx) — returns data only; never posts/sends
#   * gorgias_api.post_internal_note   — the ONLY write, and it is an INTERNAL
#                                        note (channel="internal-note",
#                                        public=False). DRY-RUN by default.
#   * gorgias_api.add_tags/set_priority — internal ticket metadata only.
#   * feedback_db.record_draft         — local SQLite metrics row.
import classifier
import draft_engine
import gorgias_api
import feedback_db
import kb_review

# ---------------------------------------------------------------------------
# PII scrubbing — applied before persisting customer text to feedback.db or logs
# ---------------------------------------------------------------------------
_PII_PATTERNS = [
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '[email]'),
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[phone]'),
    (re.compile(r'\b(?:#?\d{4,}\b)'), '[order#]'),
    (re.compile(r'\b\d{5}(?:-\d{4})?\b'), '[zip]'),
]

def _scrub_pii(text):
    """Replace common PII patterns with safe placeholders. Returns scrubbed string."""
    if not text or not isinstance(text, str):
        return text
    for pat, repl in _PII_PATTERNS:
        text = pat.sub(repl, text)
    return text

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

def load_config():
    with open(CONFIG_PATH, "r") as f:
        cfg = json.load(f)
    # Env overrides — .env (or shell env) wins over config.json
    _ENV_MAP = {
        "GORGIAS_BASE_URL":     "gorgias_base_url",
        "GORGIAS_USERNAME":     "gorgias_username",
        "GORGIAS_API_KEY":      "gorgias_api_key",
        "WEBHOOK_SECRET_TOKEN": "secret_token",
    }
    for env_key, cfg_key in _ENV_MAP.items():
        val = os.environ.get(env_key, "").strip()
        if val:
            cfg[cfg_key] = val
    # Decrypt API key if it starts with "enc:" (config.json path; env override is always plaintext)
    api_key = cfg.get("gorgias_api_key", "")
    if api_key.startswith("enc:"):
        cfg["gorgias_api_key"] = _decrypt_api_key(api_key[4:])
    return cfg


def _decrypt_api_key(encrypted_token):
    """Decrypt an API key using the machine key."""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.error("cryptography library not installed but API key is encrypted.")
        raise
    key_file = "/etc/gorgias-wh-key"
    if not os.path.exists(key_file):
        logger.error("Machine key file not found. Run: python3 crypto_util.py setup")
        raise FileNotFoundError(key_file)
    with open(key_file, "rb") as f:
        machine_key = f.read().strip()
    derived = base64.b64encode(hashlib.sha256(machine_key).digest())
    cipher = Fernet(derived)
    return cipher.decrypt(encrypted_token.encode()).decode()

CONFIG = load_config()

# ---------------------------------------------------------------------------
# Workflow A safety gate (Stage 2, Task 8)
# ---------------------------------------------------------------------------
# Workflow A posts ONLY internal notes (channel="internal-note", public=False)
# and internal ticket metadata (tags / priority). It NEVER messages a customer
# and NEVER calls GorgiasClient.add_message_to_ticket() or any public path.
#
# SAFE BY DEFAULT — every Gorgias write is DRY-RUN unless BOTH are true:
#   1. WORKFLOW_A_CONFIRM is set (config "workflow_a_confirm": true, or the env
#      var WORKFLOW_A_CONFIRM=1) — this is the per-feature intent flag, and
#   2. HERMES_ALLOW_WRITE=1 in the environment — gorgias_api's own hard gate.
# With either missing, gorgias_api returns {"dry_run": True, ...} and makes NO
# network call. The default (both unset) is dry-run: nothing leaves the box.
#
# The Gorgias user id the internal note is posted AS (sender). Same resolution
# as gorgias_api's CLI: GORGIAS_AGENT_USER_ID env, else the verified default.
WORKFLOW_A_CONFIRM = (
    os.environ.get("WORKFLOW_A_CONFIRM", "").strip() in ("1", "true", "yes")
    or bool(CONFIG.get("workflow_a_confirm", False))
)
WORKFLOW_A_AGENT_USER_ID = int(
    os.environ.get("GORGIAS_AGENT_USER_ID", "") or
    CONFIG.get("gorgias_agent_user_id", "") or
    gorgias_api.DEFAULT_AGENT_USER_ID
)

# ---------------------------------------------------------------------------
# Owner-alert gate (Stage 5, Task 15) — focused Telegram alerts to the OWNER.
# ---------------------------------------------------------------------------
# In ADDITION to the generic per-ticket Telegram notification (always sent
# above in _handle_webhook), Workflow A fires FOCUSED owner alerts:
#   * an ESCALATION alert when a ticket is sensitive/escalate, and
#   * a KB-GAP "how should I answer?" ask when there is no KB answer.
# These go to the OWNER chat(s) only — NEVER to a customer.
#
# Enabled by DEFAULT (the owner wants these) but cheap to MUTE so test/staging
# runs never spam the live chat: set WORKFLOW_A_TELEGRAM_ALERTS=0 (env) or
# "workflow_a_telegram_alerts": false (config.json) to disable. Each call is
# additionally wrapped in try/except and can never break the always-200 path.
def _alerts_enabled():
    env = os.environ.get("WORKFLOW_A_TELEGRAM_ALERTS", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    # Unset -> fall back to config (default True).
    return bool(CONFIG.get("workflow_a_telegram_alerts", True))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("gorgias-webhook")
logger.setLevel(logging.DEBUG)

# Console handler
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(ch)

# File handler
log_file = CONFIG.get("log_file", os.path.join(SCRIPT_DIR, "webhooks.log"))
fh = logging.FileHandler(log_file)
fh.setLevel(logging.DEBUG)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(fh)

# ---------------------------------------------------------------------------
# Gorgias API Client
# ---------------------------------------------------------------------------
class GorgiasClient:
    """Minimal Gorgias REST API client using urllib (no external deps)."""

    def __init__(self, base_url, username, api_key):
        self.base_url = base_url.rstrip("/")
        # Gorgias uses Basic Auth: base64(username:api_key)
        credentials = f"{username}:{api_key}"
        encoded = base64.b64encode(credentials.encode()).decode()
        self.auth_header = f"Basic {encoded}"

    def _request(self, method, path, data=None):
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": self.auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        body = json.dumps(data).encode() if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                response_data = resp.read().decode()
                return json.loads(response_data) if response_data else {}
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            logger.error(f"Gorgias API error {e.code} for {method} {path}: {error_body}")
            raise
        except urllib.error.URLError as e:
            logger.error(f"Network error for {method} {path}: {e.reason}")
            raise

    # -- Customers ----------------------------------------------------------
    def list_customers(self, page=1, per_page=30):
        """Step 3: Retrieve a paginated list of customers."""
        return self._request("GET", f"/api/customers/?page={page}&per_page={per_page}")

    def create_customer(self, name, phone=None, email=None, note=""):
        """Step 4: Create a new customer with channel info."""
        channels = []
        if phone:
            channels.append({"address": phone, "type": "phone"})
        if email:
            channels.append({"address": email, "type": "email"})
        payload = {
            "name": name,
            "note": note,
            "channels": channels,
        }
        return self._request("POST", "/api/customers/", payload)

    def update_customer(self, customer_id, channels):
        """Step 4: Update an existing customer's channels."""
        return self._request("PUT", f"/api/customers/{customer_id}", {"channels": channels})

    # -- Tickets ------------------------------------------------------------
    def create_ticket(self, channel, sender_email, receiver_email,
                      subject, body_text, body_html=None, from_agent=False):
        """
        Step 5: Create a ticket with the first message.
        For SMS integrations, sender email should be like:
          smsintegration+{phonenumber}@externalservice.com
        """
        payload = {
            "messages": [
                {
                    "channel": channel,       # "phone", "email", etc.
                    "via": "api",
                    "from_agent": from_agent,
                    "sender": {"email": sender_email},
                    "receiver": {"email": receiver_email},
                    "subject": subject,
                    "body_text": body_text,
                    "body_html": body_html or f"<div>{body_text}</div>",
                }
            ]
        }
        return self._request("POST", "/api/tickets/", payload)

    def get_ticket(self, ticket_id):
        """Retrieve a single ticket by ID."""
        return self._request("GET", f"/api/tickets/{ticket_id}")

    def add_message_to_ticket(self, ticket_id, body_text, from_agent=True,
                              channel="email", sender_email=None, receiver_email=None):
        """Add a new message to an existing ticket (Step 6 reply flow)."""
        payload = {
            "channel": channel,
            "via": "api",
            "from_agent": from_agent,
            "sender": {"email": sender_email or "support@gorgias.com"},
            "receiver": {"email": receiver_email or "customer@example.com"},
            "body_text": body_text,
            "body_html": f"<div>{body_text}</div>",
        }
        return self._request("POST", f"/api/tickets/{ticket_id}/messages", payload)

    # -- Webhook event handlers ---------------------------------------------
    def handle_ticket_created(self, payload):
        """Called when Gorgias sends a 'ticket.created' webhook."""
        ticket = payload.get("data", payload)
        ticket_id = ticket.get("id")
        subject = ticket.get("subject", "(no subject)")
        customer_email = ticket.get("customer", {}).get("email", "unknown")
        logger.info(f"Ticket created: #{ticket_id} - {subject} (from {customer_email})")

        # Log the full event
        logger.debug(f"Ticket created payload: {json.dumps(payload, indent=2)[:2000]}")

        # --- Your custom logic here ---
        # Example: send a welcome auto-reply
        # self.add_message_to_ticket(
        #     ticket_id,
        #     "Thanks for reaching out! We'll get back to you shortly.",
        #     from_agent=True,
        # )

    def handle_ticket_updated(self, payload):
        """Called when a ticket is updated."""
        ticket = payload.get("data", payload)
        ticket_id = ticket.get("id")
        logger.info(f"Ticket updated: #{ticket_id}")
        logger.debug(f"Ticket updated payload: {json.dumps(payload, indent=2)[:2000]}")

    def handle_message_created(self, payload):
        """
        Called when a new message is added to a ticket (e.g. agent replies).
        Step 6: This is where you'd forward the reply to your external channel (SMS, etc.)
        """
        message = payload.get("data", payload)
        ticket_id = message.get("ticket_id")
        from_agent = message.get("from_agent", False)
        body_text = message.get("body_text", "")
        sender_email = message.get("sender", {}).get("email", "")

        logger.info(
            f"Message created on ticket #{ticket_id} "
            f"(from_agent={from_agent}, sender={sender_email})"
        )
        logger.debug(f"Message body: {body_text[:500]}")
        logger.debug(f"Message created payload: {json.dumps(payload, indent=2)[:2000]}")

        # --- Your custom logic for Step 6 ---
        # If an agent replied, extract the phone number from the sender email
        # (e.g. smsintegration+13061111111@externalservice.com -> +13061111111)
        # and send an SMS via your third-party SMS provider's API.
        #
        # if from_agent and "smsintegration+" in sender_email:
        #     phone = sender_email.split("smsintegration+")[1].split("@")[0]
        #     send_sms(phone, body_text)  # your SMS provider's API


# ---------------------------------------------------------------------------
# Routing decision (Stage 2, Task 8) — pure, testable, no side effects.
# ---------------------------------------------------------------------------
def route_for_event(event_type, from_agent):
    """Decide which workflow a webhook event routes to.

    Returns:
      "B" — an agent message (from_agent=True) -> Workflow B (feedback loop).
            Draft pipeline is SKIPPED for agent messages.
      "A" — a customer message or indeterminate sender
            (from_agent=False OR from_agent=None).
            -> Workflow A (classify + draft an internal note).
            We only skip draft when we are CERTAIN it's an agent.
            Unknown senders are treated as customers (safe default).
      None — event type not recognized for drafting.

    The event string is normalized (hyphens -> dots) so the DOTTED Gorgias
    events that the live webhook actually sends — "ticket.created" and
    "ticket.message.created" — match alongside the hyphenated template
    variants ("ticket-created" / "ticket-message-created").

    POLICY: Draft is skipped ONLY when from_agent=True (confirmed agent).
    If from_agent is None (indeterminate), we default to drafting because
    ticket-created events are almost always from a customer, and skipping
    a real customer message is worse than drafting for an unknown sender.
    """
    # Only skip draft when we are CERTAIN the sender is an agent.
    # from_agent=True  -> agent reply, skip draft
    # from_agent=None  -> unknown sender, still draft (safe default)
    # from_agent=False -> confirmed customer, draft
    if from_agent:
        return "B"

    evt = (event_type or "").replace("-", ".")
    if "ticket.created" in evt or "message.created" in evt:
        return "A"
    return None


# ---------------------------------------------------------------------------
# Workflow A — classify -> draft -> INTERNAL note (Stage 2, Task 8)
# ---------------------------------------------------------------------------
def run_workflow_a(ctx, suppress_generic_notify=False):
    """Classify a customer ticket, determine priority, take priority-based
    actions, generate a SAFE draft, and post it as an INTERNAL note.

    PRIORITY-BASED FLOW (per owner spec):

      URGENT (act within minutes):
        - Set ticket priority to "urgent" via Gorgias API
        - Notify owner IMMEDIATELY via priority bot with priority + customer
          message + ticket link
        - Draft reply and post as internal note
        - Notify owner AFTER draft with draft text included

      HIGH (act within a few hours):
        - Set ticket priority to "high" via Gorgias API
        - Draft reply and post as internal note
        - Notify owner AFTER draft with priority + customer message + draft

      NORMAL (queue or auto-draft):
        - Set ticket priority to "normal" via Gorgias API
        - Draft reply and post as internal note only

      LOW (queue or auto-draft):
        - Set ticket priority to "low" via Gorgias API
        - Draft reply and post as internal note only

    KB GAP Q&A: If the KB has no answer, the system asks the owner via the
    priority bot, waits for the reply, stores it in the KB, then generates
    a draft using the new answer.

    SAFETY: this NEVER messages the customer. The only Gorgias write is an
    internal note + internal metadata (tags/priority). All writes are DRY-RUN
    unless WORKFLOW_A_CONFIRM AND HERMES_ALLOW_WRITE=1.

    The whole body is error-isolated: a failure in any step is logged and never
    propagates, so the webhook's always-200 response can never be broken.
    """
    ticket_id = getattr(ctx, "ticket_id", None)
    try:
        # -- (a) classify (offline, no network/LLM key) --------------------- #
        try:
            classification = classifier.classify(ctx)
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: classify failed: {e}", exc_info=True)
            classification = classifier.Classification(
                category="unknown",
                urgency=classifier.URGENCY_HIGH,
                escalate=True,
                sensitive=False,
                reasons=["classifier error — conservatively escalated"],
            )
            classification.recompute_auto_draft()

        # -- (a2) extract customer message for priority detection ----------- #
        customer_message, _subject, _order = \
            draft_engine._extract_message_subject_order(ctx)

        # -- (a3) determine priority using AI analysis --------------------- #
        # The AI analyzes the customer message against the owner's criteria
        # to determine urgency. Falls back to keyword patterns if LLM unavailable.
        # The analysis is also used for KB query and draft generation.
        import priority_logic
        _messages = getattr(ctx, "messages", None) or []
        priority, _ai_analysis = priority_logic.determine_priority(
            classification,
            customer_message,
            subject=_subject,
            conversation=_messages,
            order_context=getattr(ctx, "order_context", None),
        )
        # Store analysis in context for draft engine to reuse
        ctx.ai_analysis = _ai_analysis
        actions = priority_logic.get_actions(priority)
        logger.info(f"WF-A ticket #{ticket_id}: priority={priority} category={classification.category} "
                     f"escalate={classification.escalate} urgency={classification.urgency}")

        # -- (a4) set ticket priority via Gorgias API ----------------------- #
        try:
            gorgias_api.set_priority(
                CONFIG["gorgias_base_url"],
                CONFIG["gorgias_username"],
                CONFIG["gorgias_api_key"],
                ticket_id,
                priority_logic.GORGIAS_PRIORITY_MAP.get(priority, priority),
                confirm=WORKFLOW_A_CONFIRM,
            )
        except SystemExit as e:
            logger.error(f"WF-A ticket #{ticket_id}: set_priority crashed (SystemExit): {e}", exc_info=True)
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: set_priority failed: {e}", exc_info=True)

        # -- (a5) URGENT: notify owner IMMEDIATELY via priority bot ---------- #
        # For URGENT tickets, the owner needs to know RIGHT NOW — before the
        # draft is even created. This sends the priority, customer message,
        # and Gorgias ticket link so the owner can jump on it.
        if actions.notify_owner_immediately:
            try:
                import telegram_priority
                telegram_priority.send_priority_notification(
                    ticket_id=ticket_id,
                    priority=priority,
                    customer_message=customer_message,
                    gorgias_ticket_url=f"https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
                )
                logger.info(f"WF-A ticket #{ticket_id}: URGENT owner notification sent")
            except Exception as e:
                logger.error(f"WF-A ticket #{ticket_id}: URGENT notification failed: {e}", exc_info=True)

        # -- (b) generate the draft ------------------------------------------- #
        try:
            result = draft_engine.generate_draft(ctx, classification)
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: generate_draft failed: {e}", exc_info=True)
            result = draft_engine.DraftResult(
                draft_text="",
                should_post=False,
                reason=f"generate_draft exception: {e}",
                model_used="none (exception)",
                priority=priority,
                category=getattr(classification, "category", "unknown"),
            )

        # -- (b2) KB GAP Q&A: ask owner asynchronously (non-blocking) -------- #
        # If the KB had no answer (kb_gap=True), send the question to the owner
        # via the priority bot. This is NON-BLOCKING: we send the question and
        # immediately continue posting the gap note as an internal note. A
        # background thread polls for the owner's reply; when it arrives, the
        # answer is stored in the KB and an updated draft is posted as a second
        # internal note. This way Gorgias gets its 200 response within seconds,
        # not minutes, and the webhook server is never blocked.
        if result.kb_gap:
            try:
                import telegram_priority
                import threading

                # Send the KB gap question to the owner (returns immediately
                # after sending — the polling happens in the background thread).
                sent = telegram_priority.send_kb_gap_question_async(
                    ticket_id=ticket_id,
                    customer_message=customer_message,
                )
                if sent:
                    logger.info(f"WF-A ticket #{ticket_id}: KB gap question sent to owner "
                                f"(background polling started)")
                else:
                    logger.warning(f"WF-A ticket #{ticket_id}: failed to send KB gap question")
            except Exception as e:
                logger.error(f"WF-A ticket #{ticket_id}: KB gap Q&A failed: {e}", exc_info=True)

        # -- (b3) send Telegram notifications -------------------------------- #
        # TWO separate Telegram channels:
        #
        # 1. OLD BOT (telegram_notify) — TESTING channel. Sends draft
        #    notification for ALL tickets (every priority level) so the team
        #    can monitor the full pipeline. Uses send_draft_notification().
        #
        # 2. PRIORITY BOT (telegram_priority) — PRODUCTION channel. Sends
        #    priority alerts ONLY for URGENT and HIGH tickets (per owner spec).
        #    URGENT gets an immediate alert before drafting + a draft alert
        #    after. HIGH gets a draft alert after drafting. NORMAL/LOW get
        #    nothing on this channel.

        # (b3a) OLD BOT: draft notification for ALL tickets (testing channel)
        try:
            if _alerts_enabled():
                import telegram_notify
                telegram_notify.send_draft_notification(
                    ctx.to_dict(),
                    result.as_dict(),
                )
                logger.info(f"WF-A ticket #{ticket_id}: draft notification sent (old bot / testing)")
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: old bot draft notification failed: {e}", exc_info=True)

        # (b3b) PRIORITY BOT: priority notification only for URGENT/HIGH
        if actions.notify_owner_after_draft and _alerts_enabled():
            try:
                import telegram_priority
                telegram_priority.send_priority_notification(
                    ticket_id=ticket_id,
                    priority=priority,
                    customer_message=customer_message,
                    draft_text=result.draft_text if actions.include_draft_in_notification else None,
                    conversation=getattr(ctx, "messages", None),
                    gorgias_ticket_url=f"https://buttonsbebe.gorgias.com/app/ticket/{ticket_id}",
                )
                logger.info(f"WF-A ticket #{ticket_id}: priority notification sent (priority={priority})")
            except Exception as e:
                logger.error(f"WF-A ticket #{ticket_id}: priority notification failed: {e}", exc_info=True)

        # -- (c) compose the INTERNAL note: draft reply only ---------------- #
        if result.is_escalation:
            status = "escalated"
        elif result.kb_gap:
            status = "kb_gap"
        else:
            status = "drafted"

        note_body = result.draft_text or ""

        # -- (d) post the INTERNAL note (DRY-RUN unless the gate is open) ---- #
        # Skip posting when the draft is empty (LLM unavailable / empty response).
        # Escalations and KB gaps always have text, so they are not affected.
        post_res = {}
        if not note_body.strip():
            logger.warning(
                f"WF-A ticket #{ticket_id}: SKIPPING internal note post — "
                f"draft_text is empty (LLM unavailable or returned nothing). "
                f"model_used={result.model_used}"
            )
            post_res = {"dry_run": True, "skipped": True, "reason": "empty draft"}
        else:
            try:
                post_res = gorgias_api.post_internal_note(
                    CONFIG["gorgias_base_url"],
                    CONFIG["gorgias_username"],
                    CONFIG["gorgias_api_key"],
                    ticket_id,
                    note_body,
                    WORKFLOW_A_AGENT_USER_ID,
                    confirm=WORKFLOW_A_CONFIRM,
                )
            except SystemExit as e:
                # gorgias_api.die() calls sys.exit(1) — catch it so the server
                # doesn't crash on a Gorgias API error.
                logger.error(f"WF-A ticket #{ticket_id}: post_internal_note crashed (SystemExit): {e}", exc_info=True)
                post_res = {"dry_run": True, "error": str(e)}
            except Exception as e:
                logger.error(f"WF-A ticket #{ticket_id}: post_internal_note failed: {e}", exc_info=True)
                post_res = {"dry_run": True, "error": str(e)}

        dry_run = bool(post_res.get("dry_run", True))
        posted_note_id = post_res.get("message_id") if not dry_run else None

        # -- (e) tag (internal metadata; dry-run gated) --------------------- #
        try:
            if result.is_escalation:
                tags = ["escalate"]
            elif result.kb_gap:
                tags = ["kb-gap"]
            elif result.should_post:
                tags = ["ai-drafted"]
            else:
                tags = []
            # Add priority as a tag for easy filtering in Gorgias.
            # Use the Gorgias API value (e.g. "priority-critical" not "priority-urgent").
            import priority_logic as _pl
            tags.append(f"priority-{_pl.GORGIAS_PRIORITY_MAP.get(priority, priority)}")
            if tags:
                try:
                    gorgias_api.add_tags(
                        CONFIG["gorgias_base_url"],
                        CONFIG["gorgias_username"],
                        CONFIG["gorgias_api_key"],
                        ticket_id,
                        tags,
                        confirm=WORKFLOW_A_CONFIRM,
                    )
                except SystemExit as e:
                    logger.error(f"WF-A ticket #{ticket_id}: add_tags crashed (SystemExit): {e}", exc_info=True)
                except Exception as e:
                    logger.error(f"WF-A ticket #{ticket_id}: add_tags failed: {e}", exc_info=True)
        except SystemExit as e:
            logger.error(f"WF-A ticket #{ticket_id}: tag step crashed (SystemExit): {e}", exc_info=True)
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: tag failed: {e}", exc_info=True)

        # -- (f) persist a metrics row (regardless of dry-run) -------------- #
        try:
            customer_email = (
                ctx.customer.get("email")
                if isinstance(getattr(ctx, "customer", None), dict)
                else None
            )
            _draft_kwargs = result.record_draft_kwargs()
            _draft_kwargs["draft_text"] = _scrub_pii(_draft_kwargs.get("draft_text") or "")
            feedback_db.record_draft(
                ticket_id=ticket_id,
                customer_message=_scrub_pii(customer_message),
                customer_email=_scrub_pii(customer_email),
                order_context=_scrub_pii(str(getattr(ctx, "order_context", None) or "")),
                dry_run=1 if dry_run else 0,
                posted_note_id=posted_note_id,
                status=status,
                **_draft_kwargs,
            )
        except Exception as e:
            logger.error(f"WF-A ticket #{ticket_id}: record_draft failed: {e}", exc_info=True)

        # -- (g) one-line summary ------------------------------------------- #
        note_id_str = posted_note_id if posted_note_id else "DRYRUN"
        logger.info(
            f"WF-A ticket #{ticket_id}: category={result.category} "
            f"status={status} should_post={result.should_post} "
            f"dry_run={dry_run} note={note_id_str}"
        )
    except Exception as e:
        # Final safety net: Workflow A must NEVER break the 200 response.
        logger.error(f"WF-A ticket #{ticket_id}: unexpected error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Workflow B — capture a human agent reply & compare it to our AI draft
# (Stage 3, Task 10). CAPTURE-ONLY: this builds training data in feedback.db.
# ---------------------------------------------------------------------------
# SAFETY: Workflow B makes NO Gorgias API calls of any kind and NEVER messages
# a customer. It only reads ctx (already fetched, read-only) and writes to the
# local feedback.db. The whole body is error-isolated so it can never break the
# webhook's always-200 response.

# Treated as "our own internal note" (NEVER captured as a human reply): a
# message on the internal-note channel and/or marked non-public. Comparing our
# draft against our own internal note would be garbage training data.
_INTERNAL_NOTE_CHANNEL = gorgias_api.INTERNAL_NOTE_CHANNEL  # "internal-note"
# Our bot's Gorgias user id — any agent message from this sender is our own
# output (e.g. a posted internal note) and must never be captured. Defensive.
_BOT_AGENT_USER_ID = gorgias_api.DEFAULT_AGENT_USER_ID      # 777419526


def _normalize_text(text):
    """Normalize a reply/draft for similarity: strip, collapse whitespace,
    lowercase. Used for both the SequenceMatcher ratio and the exact-match
    check so trivial formatting/casing differences don't depress the score."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _message_is_our_internal_note(msg):
    """True if this agent message is one of OUR OWN internal notes (exclude).

    Excludes when ANY of:
      * channel == "internal-note", or
      * public is explicitly False (internal notes are private), or
      * the sender id is our bot's user id (defensive: never capture our output).
    """
    if not isinstance(msg, dict):
        return True  # be conservative: anything malformed is not a human reply
    if msg.get("channel") == _INTERNAL_NOTE_CHANNEL:
        return True
    if msg.get("public") is False:
        return True
    sender = msg.get("sender") or {}
    if isinstance(sender, dict) and sender.get("id") == _BOT_AGENT_USER_ID:
        return True
    return False


def _pick_human_public_reply(messages):
    """Return the most recent message that is a HUMAN agent's PUBLIC reply.

    A qualifying message has from_agent True, is NOT one of our own internal
    notes (see _message_is_our_internal_note), and is public. ctx.messages is
    oldest-first (the pipeline lists with created_datetime:asc), so we walk it
    in reverse and return the first match. Returns None if there is none.
    """
    for msg in reversed(messages or []):
        if not isinstance(msg, dict):
            continue
        if not msg.get("from_agent"):
            continue
        if _message_is_our_internal_note(msg):
            continue
        return msg
    return None


def _message_body(msg):
    """The reply text: prefer body_text, fall back to stripped_text."""
    return (msg.get("body_text") or msg.get("stripped_text") or "").strip()


def _pick_draft_to_compare(rows):
    """From a ticket's drafts (newest first), pick the most recent REAL
    customer draft to compare against — status == "drafted" with draft_text.
    NOT an escalation/kb_gap note. Returns the Row, or None if none suitable."""
    for row in rows or []:
        if row["status"] == "drafted" and (row["draft_text"] or "").strip():
            return row
    return None


def _parse_iso(ts):
    """Parse an ISO8601 timestamp (tolerating a trailing 'Z'). None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def run_workflow_b(ctx):
    """Capture a human agent's PUBLIC reply and compare it to our AI draft.

    CAPTURE-ONLY (Stage 3, Task 10):
      1. Find the most recent human PUBLIC agent reply in ctx.messages,
         EXCLUDING our own internal notes (channel="internal-note" / public
         False / sender == our bot id). If none, log and return.
      2. Dedup on the Gorgias message_id (reply_exists) — webhook retries must
         not double-count.
      3. Record the reply in feedback.db.
      4. Find the most recent real customer draft for this ticket
         (status="drafted"). If none, keep the reply but SKIP the comparison.
      5. Compute difflib.SequenceMatcher ratio on the normalized texts, plus
         exact_match, a compact edit_ops summary, and response_time_sec, and
         record the comparison.

    NO Gorgias writes, NO customer messaging — reads ctx, writes feedback.db
    only. Fully error-isolated so it can never break the always-200 response.
    """
    ticket_id = getattr(ctx, "ticket_id", None)
    try:
        # -- (1) identify the human PUBLIC reply (excl. our own internal notes) #
        reply_msg = _pick_human_public_reply(getattr(ctx, "messages", None))
        if reply_msg is None:
            logger.info(
                f"WF-B ticket #{ticket_id}: no human public agent reply to "
                f"capture (only our own internal note / no agent message) — "
                f"nothing recorded."
            )
            return

        message_id = reply_msg.get("id")
        reply_text = _message_body(reply_msg)
        sender = reply_msg.get("sender") or {}
        agent_user_id = sender.get("id") if isinstance(sender, dict) else None
        sender_email = sender.get("email") if isinstance(sender, dict) else None
        channel = reply_msg.get("channel")
        created_at = reply_msg.get("created_datetime")

        # -- (2) dedup on message_id (webhook retries / duplicate deliveries) -- #
        if message_id is not None and feedback_db.reply_exists(message_id):
            logger.info(
                f"WF-B ticket #{ticket_id}: reply msg #{message_id} already "
                f"captured — skipping (dedup)."
            )
            return

        # -- (3) record the reply (training data) — scrub PII before DB write #
        reply_id = feedback_db.record_reply(
            ticket_id=ticket_id,
            reply_text=_scrub_pii(reply_text),
            message_id=message_id,
            agent_user_id=agent_user_id,
            sender_email=_scrub_pii(sender_email),
            channel=channel,
            created_at=created_at,
        )

        # -- (4) find the matching real customer draft ---------------------- #
        try:
            draft_rows = feedback_db.drafts_for_ticket(ticket_id)
        except Exception as e:
            logger.error(
                f"WF-B ticket #{ticket_id}: drafts_for_ticket failed: {e}",
                exc_info=True,
            )
            draft_rows = []
        draft = _pick_draft_to_compare(draft_rows)

        if draft is None:
            # Keep the captured reply (training data); the FK on comparisons
            # requires a real draft_id, so we do NOT insert a comparison.
            logger.info(
                f"WF-B ticket #{ticket_id}: captured reply msg #{message_id} "
                f"(reply_id={reply_id}) — no draft to compare."
            )
            return

        # -- (5) compute similarity & record the comparison ----------------- #
        draft_text = draft["draft_text"] or ""
        norm_draft = _normalize_text(draft_text)
        norm_reply = _normalize_text(reply_text)
        ratio = difflib.SequenceMatcher(None, norm_draft, norm_reply).ratio()
        exact_match = 1 if (norm_draft == norm_reply or ratio == 1.0) else 0

        # Compact opcodes summary: how much the agent kept vs. changed.
        edit_ops = {"equal": 0, "replace": 0, "insert": 0, "delete": 0}
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, norm_draft, norm_reply
        ).get_opcodes():
            if tag == "equal":
                edit_ops["equal"] += (i2 - i1)
            elif tag == "replace":
                edit_ops["replace"] += (j2 - j1)
            elif tag == "insert":
                edit_ops["insert"] += (j2 - j1)
            elif tag == "delete":
                edit_ops["delete"] += (i2 - i1)

        # response_time_sec = reply.created_at − draft.created_at (if both parse).
        response_time_sec = None
        draft_dt = _parse_iso(draft["created_at"])
        reply_dt = _parse_iso(created_at)
        if draft_dt is not None and reply_dt is not None:
            response_time_sec = int((reply_dt - draft_dt).total_seconds())

        feedback_db.record_comparison(
            ticket_id=ticket_id,
            draft_id=draft["id"],
            reply_id=reply_id,
            similarity_score=ratio,
            exact_match=exact_match,
            edit_ops=edit_ops,
            response_time_sec=response_time_sec,
            notes="workflow_b difflib capture",
        )

        # -- (6) one-line summary ------------------------------------------- #
        logger.info(
            f"WF-B ticket #{ticket_id}: captured reply msg #{message_id} "
            f"(reply_id={reply_id}) vs draft id={draft['id']} "
            f"similarity={ratio:.3f} exact_match={exact_match}"
        )

        # -- (7) Retain the interaction into Hindsight (agent memory) -------- #
        # This is the hindsight loop: the human agent's actual reply becomes
        # a learnable experience. Hindsight extracts facts and patterns over
        # time. PII-scrubbed inside hindsight_integration. Never crashes.
        try:
            from hindsight_integration import retain_ticket_experience
            # Find the original customer question for this ticket
            customer_q = ""
            for msg in (getattr(ctx, "messages", None) or []):
                if msg.get("speaker") == "Customer":
                    customer_q = (msg.get("body_clean") or msg.get("body_text") or "").strip()
                    break
            retain_ticket_experience(
                ticket_id=str(ticket_id),
                customer_message=_scrub_pii(customer_q or draft_text[:500]),
                agent_reply=_scrub_pii(reply_text),
                category=getattr(ctx, "classification", {}).get("category", "") if hasattr(ctx, "classification") else "",
                ticket_tags=",".join(getattr(ctx, "ticket_tags", []) or []) if hasattr(ctx, "ticket_tags") else "",
                similarity_to_draft=ratio,
            )
        except Exception:
            pass  # Hindsight is optional — never break the webhook

    except Exception as e:
        # Final safety net: Workflow B must NEVER break the 200 response.
        logger.error(f"WF-B ticket #{ticket_id}: unexpected error: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
class GorgiasWebhookHandler(BaseHTTPRequestHandler):
    """
    HTTP request handler for Gorgias webhooks.
    Routes:
      GET  /health   -> health check
      GET  /test     -> test page
      POST /webhook  -> Gorgias webhook receiver
    """

    # Dedup tracking for Telegram notifications: {ticket_id_str: timestamp}
    _recent_tickets = {}

    # Dedup tracking for Workflow A (draft pipeline): {ticket_id_str: timestamp}
    # Prevents duplicate internal notes when Gorgias retries the same webhook
    # event (ticket-created + ticket-message-created for the same message).
    # A new draft is only created when the dedup window (90s) has expired.
    _recent_drafts = {}
    _DRAFT_DEDUP_WINDOW = 90  # seconds — cover Gorgias retries (~50-60s apart)

    def log_message(self, format, *args):
        # Redact ?token=... from the request line before it reaches the log.
        msg = format % args
        msg = re.sub(r'(\?|&)token=[^&\s"]+', r'\1token=[REDACTED]', msg)
        logger.info(f"{self.client_address[0]} - {msg}")

    # -- GET routes ---------------------------------------------------------
    _STATIC_HTML = {
        "/kb-architecture-diagram.html": "kb-architecture-diagram.html",
        "/workflow-diagram.html": "workflow-diagram.html",
        "/kb-review.html": "kb-review.html",
        "/kb-review": "kb-review.html",
    }

    def do_GET(self):
        clean_path = self.path.split("?")[0]
        # /health and /test are intentionally public (no auth required).
        if clean_path == "/health":
            self._json_response(200, {
                "status": "ok",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "service": "gorgias-webhook-receiver",
            })
            return
        elif clean_path in self._STATIC_HTML:
            self._serve_static_html(self._STATIC_HTML[clean_path])
            return
        elif clean_path == "/test":
            html = """<!DOCTYPE html>
<html><head><title>Gorgias Webhook Receiver</title></head>
<body style="font-family: sans-serif; max-width: 600px; margin: 50px auto;">
<h1>Gorgias Webhook Receiver</h1>
<p>Status: <strong>Running</strong></p>
<p>Webhook URL: <code>POST http://YOUR-VPS-IP:8080/webhook</code></p>
<p>Health check: <code>GET /health</code></p>
<hr>
<p>Configure your Gorgias HTTP Integration to point to <code>/webhook</code> on this server.</p>
</body></html>"""
            self._html_response(200, html)
            return

        # All other GET routes (including /api/kb-review) require auth.
        secret = CONFIG.get("secret_token", "")
        if secret:
            provided = self.headers.get("X-Webhook-Secret", "")
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for pair in query.split("&"):
                    if pair.startswith("token="):
                        provided = provided or pair[6:]
            if not hmac.compare_digest(provided, secret):
                self._json_response(401, {"error": "Invalid secret token"})
                return

        if clean_path.startswith("/api/kb-review"):
            self._handle_kb_review_get(clean_path)
        else:
            self._json_response(404, {"error": "Not found"})

    # -- POST routes --------------------------------------------------------
    _MAX_BODY = 10 * 1024 * 1024  # 10 MB hard cap

    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self._json_response(400, {"error": "Invalid Content-Length"})
            return
        if content_length > self._MAX_BODY:
            self._json_response(413, {"error": "Payload too large"})
            return
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""

        clean_path = self.path.split("?")[0]

        # Verify secret token if configured (header OR query param)
        # Skip verification if secret_token is empty (allows Gorgias to deliver without token)
        secret = CONFIG.get("secret_token", "")
        if secret:
            provided_secret = self.headers.get("X-Webhook-Secret", "")
            # Also check ?token= query parameter (Gorgias API doesn't support
            # custom headers, so we allow the token in the URL instead)
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for pair in query.split("&"):
                    if pair.startswith("token="):
                        provided_secret = provided_secret or pair[6:]
            if not hmac.compare_digest(provided_secret, secret):
                logger.warning(f"Rejected request: invalid secret token from {self.client_address[0]}")
                self._json_response(401, {"error": "Invalid secret token"})
                return

        if clean_path.startswith("/api/kb-review"):
            self._handle_kb_review_post(clean_path, raw_body)
        elif clean_path == "/webhook":
            self._handle_webhook(raw_body)
        else:
            self._json_response(404, {"error": "Not found"})

    # -- Webhook processing -------------------------------------------------
    def _handle_webhook(self, raw_body):
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.error(f"Failed to parse webhook body: {e}")
            self._json_response(400, {"error": "Invalid JSON"})
            return

        if not isinstance(payload, dict):
            self._json_response(400, {"error": "Expected JSON object"})
            return

        # Gorgias sends the event type in different fields depending on configuration.
        # Our HTTP Integration form sends: { "trigger": "{{event.type}}", "ticket": {...} }
        # Other patterns:
        #   - payload["event"] = "ticket.created" (string)
        #   - payload["event"] = {"type": "ticket-created"} (dict)
        #   - payload["type"]  = same
        #   - payload["trigger"] = "ticket-created" (our current form)
        trigger_raw = payload.get("trigger")
        event_raw = payload.get("event") or payload.get("type")
        
        if trigger_raw and isinstance(trigger_raw, str):
            event_type = trigger_raw
        elif isinstance(event_raw, dict):
            event_type = event_raw.get("type", "unknown")
        elif event_raw:
            event_type = str(event_raw)
        else:
            event_type = "unknown"

        logger.info(f"Received webhook event: {event_type}")
        safe_headers = {k: v for k, v in self.headers.items()
                        if k.lower() not in ("x-webhook-secret", "authorization")}
        logger.debug(f"Webhook headers: {safe_headers}")
        logger.debug(f"Webhook payload keys: {list(payload.keys()) if isinstance(payload, dict) else type(payload)}")

        # --- Deduplication of Telegram notifications only ---
        # We process EVERY webhook through the pipeline — no webhooks are
        # skipped, so no ticket or message is ever missed. We only deduplicate
        # the Telegram notification: if we've already sent a notification for
        # this ticket_id within the last 30 seconds, we skip sending another
        # one. The pipeline still runs, context is still saved, routing still
        # happens — only the Telegram message is suppressed to avoid duplicates.
        _dedup_ticket_id = None
        ticket_block = payload.get("ticket") or {}
        if isinstance(ticket_block, dict):
            _dedup_ticket_id = ticket_block.get("id")
        # Check if we should suppress the Telegram notification (dedup)
        _suppress_telegram = False
        if _dedup_ticket_id and hasattr(GorgiasWebhookHandler, '_recent_tickets'):
            _now = time.time()
            _key = str(_dedup_ticket_id)
            _is_create_event = ("ticket-created" in event_type or "ticket.created" in event_type)
            _last = GorgiasWebhookHandler._recent_tickets.get(_key)
            if _last:
                _last_ts, _last_was_create = _last
                _within_window = (_now - _last_ts) < 30
                # Only suppress when one of the pair is a ticket-created event.
                # ticket-created + ticket-message-created for a new ticket are
                # redundant (same data, arrive ~1s apart). But two
                # ticket-message-created events are different messages and
                # should both notify.
                if _within_window and (_is_create_event or _last_was_create):
                    _suppress_telegram = True
                    logger.info(
                        f"Suppressing duplicate Telegram notification for ticket #{_dedup_ticket_id} "
                        f"(seen {(_now - _last_ts):.1f}s ago, event={event_type}) — pipeline still runs"
                    )
            GorgiasWebhookHandler._recent_tickets[_key] = (_now, _is_create_event)
            # Clean old entries (>60s) to prevent unbounded growth
            GorgiasWebhookHandler._recent_tickets = {
                k: v for k, v in GorgiasWebhookHandler._recent_tickets.items()
                if (_now - v[0]) < 60
            }

        # --- Run the data-fetch pipeline ---
        # This fetches the full ticket, conversation, customer, and order
        # context from the Gorgias API and assembles a TicketContext.
        try:
            ctx = pipeline.fetch_ticket_context(
                payload,
                base_url=CONFIG["gorgias_base_url"],
                username=CONFIG["gorgias_username"],
                api_key=CONFIG["gorgias_api_key"],
            )

            # Log summary
            logger.info(f"Pipeline result: {ctx.summary()}")

            # Save context to JSON file only in debug mode — raw Gorgias API
            # objects contain unscrubbed PII (customer email/phone, ticket
            # subject, message bodies) and must not be written to disk in
            # production (safety invariant #5).
            if os.environ.get("DEBUG_SAVE_TICKET_CONTEXTS"):
                context_dir = os.path.join(SCRIPT_DIR, "ticket_contexts")
                os.makedirs(context_dir, exist_ok=True)
                context_file = os.path.join(context_dir, f"ticket_{ctx.ticket_id}.json")
                with open(context_file, "w") as f:
                    f.write(ctx.to_json())

            # Determine route based on event type and from_agent flag (pure
            # decision in route_for_event; agent messages -> B is checked first).
            # SAFETY: route_for_event requires from_agent=False (explicit
            # customer) to route to Workflow A. If from_agent is None
            # (indeterminate), we skip draft processing entirely.
            route = route_for_event(event_type, ctx.from_agent)

            # --- Customer sender check log ---
            # Explicit audit trail: who sent the message, and did we approve
            # it for draft processing? Draft is skipped ONLY for confirmed
            # agent messages (from_agent=True).
            if route == "A":
                sender_status = (
                    "confirmed customer (from_agent=False)"
                    if ctx.from_agent is False
                    else "indeterminate sender (from_agent=None) — treating as customer"
                )
                logger.info(
                    f"Ticket #{ctx.ticket_id}: SENDER CHECK PASSED — "
                    f"{sender_status}, event={event_type} "
                    f"-> processing for draft reply"
                )
            elif route == "B":
                logger.info(
                    f"Ticket #{ctx.ticket_id}: SENDER CHECK PASSED — "
                    f"agent reply detected (from_agent=True), event={event_type} "
                    f"-> routing to feedback loop (Workflow B)"
                )
            elif ctx.from_agent is None:
                logger.info(
                    f"Ticket #{ctx.ticket_id}: SENDER CHECK — "
                    f"from_agent is indeterminate (None), event={event_type} "
                    f"-> context saved only (unrecognized event pattern)"
                )
            else:
                logger.info(
                    f"Ticket #{ctx.ticket_id}: SENDER CHECK — "
                    f"from_agent={ctx.from_agent}, event={event_type} "
                    f"-> context saved only (unrecognized event pattern)"
                )

            # --- Telegram notification flow ---
            # For Workflow A (customer messages): we DON'T send the generic
            # notification here. Instead, we send a richer DRAFT notification
            # AFTER the draft is created (inside run_workflow_a), which includes
            # the priority, full conversation, and the draft reply text.
            # For Workflow B (agent replies) and unhandled events: send the
            # generic notification here as before.
            if route == "B":
                logger.info(f"Ticket #{ctx.ticket_id}: agent reply detected (Workflow B — feedback loop)")
                # Workflow B: send the generic notification (as before).
                if not _suppress_telegram:
                    try:
                        from telegram_notify import send_ticket_notification
                        tg_results = send_ticket_notification(ctx.to_dict())
                        for r in tg_results:
                            if r.get("ok"):
                                logger.info(f"Telegram notification sent to {r['chat_id']}: message_id={r['message_id']}")
                            else:
                                logger.warning(f"Telegram notification to {r.get('chat_id')} failed: {r.get('error')}")
                    except Exception as tg_err:
                        logger.warning(f"Telegram notification error: {tg_err}")
                # CAPTURE-ONLY — no Gorgias writes, no customer messaging. Fully
                # error-isolated; can never break the 200 below.
                run_workflow_b(ctx)
            elif route == "A":
                # --- Workflow A dedup: skip if we already drafted for this ticket recently ---
                # Gorgias retries the same webhook event multiple times (~50-60s apart),
                # causing duplicate internal notes. We track the last draft time per
                # ticket_id and skip if within the dedup window (90s).
                _ticket_key = str(ctx.ticket_id)
                _now = time.time()
                _last_draft = GorgiasWebhookHandler._recent_drafts.get(_ticket_key)
                if _last_draft and (_now - _last_draft) < GorgiasWebhookHandler._DRAFT_DEDUP_WINDOW:
                    logger.info(
                        f"Ticket #{ctx.ticket_id}: SKIPPING duplicate draft — "
                        f"already drafted {(_now - _last_draft):.0f}s ago "
                        f"(dedup window={GorgiasWebhookHandler._DRAFT_DEDUP_WINDOW}s)"
                    )
                else:
                    logger.info(f"Ticket #{ctx.ticket_id}: customer message detected (Workflow A — drafting)")
                    # Workflow A: classify -> draft -> INTERNAL note (dry-run by
                    # default). The Telegram DRAFT notification is sent from inside
                    # run_workflow_a AFTER the draft is created (so the message
                    # includes priority + conversation + draft reply). The generic
                    # early notification is suppressed for this route.
                    # Fully error-isolated; can never break the 200 below.
                    GorgiasWebhookHandler._recent_drafts[_ticket_key] = _now
                    run_workflow_a(ctx, suppress_generic_notify=_suppress_telegram)
                    # Clean old entries (>10 min) to prevent unbounded growth
                    GorgiasWebhookHandler._recent_drafts = {
                        k: v for k, v in GorgiasWebhookHandler._recent_drafts.items()
                        if (_now - v) < 600
                    }
            else:
                logger.info(f"Ticket #{ctx.ticket_id}: unhandled event type '{event_type}' — context saved only")
                # For unhandled events, send the generic notification as before.
                if not _suppress_telegram:
                    try:
                        from telegram_notify import send_ticket_notification
                        tg_results = send_ticket_notification(ctx.to_dict())
                        for r in tg_results:
                            if r.get("ok"):
                                logger.info(f"Telegram notification sent to {r['chat_id']}: message_id={r['message_id']}")
                            else:
                                logger.warning(f"Telegram notification to {r.get('chat_id')} failed: {r.get('error')}")
                    except Exception as tg_err:
                        logger.warning(f"Telegram notification error: {tg_err}")

        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)

        # Always log raw payload to file for debugging
        self._log_raw_webhook(event_type, payload)

        self._json_response(200, {
            "status": "received",
            "event": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    def _log_raw_webhook(self, event_type, payload):
        """Append a PII-reduced webhook summary to the JSONL audit log."""
        log_path = os.path.join(SCRIPT_DIR, "webhook_events.jsonl")
        ticket_id = None
        if isinstance(payload, dict):
            t = payload.get("ticket") or payload.get("data") or {}
            if isinstance(t, dict):
                ticket_id = t.get("id")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event_type,
            "ticket_id": ticket_id,
        }
        try:
            with open(log_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write JSONL log: {e}")

    # -- KB review (isolated from live KB / webhook) ----------------------
    def _parse_query(self):
        params = {}
        if "?" not in self.path:
            return params
        for pair in self.path.split("?", 1)[1].split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                params[k] = v
        return params

    def _read_json_body(self, raw_body):
        if not raw_body:
            return {}
        try:
            return json.loads(raw_body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None

    def _handle_kb_review_get(self, clean_path):
        try:
            if clean_path == "/api/kb-review/approved":
                self._json_response(200, kb_review.get_approved())
            elif clean_path == "/api/kb-review/rejected":
                self._json_response(200, kb_review.get_rejected())
            elif clean_path == "/api/kb-review/stats":
                self._json_response(200, kb_review.get_stats())
            elif clean_path == "/api/kb-review":
                q = self._parse_query()
                try:
                    page = max(1, int(q.get("page", "1") or "1"))
                    per_page = min(200, max(1, int(q.get("per_page", "20") or "20")))
                except ValueError:
                    self._json_response(400, {"error": "page and per_page must be integers"})
                    return
                filter_status = q.get("filter") or None
                if filter_status == "":
                    filter_status = None
                self._json_response(200, kb_review.get_page(page, per_page, filter_status))
            else:
                self._json_response(404, {"error": "Not found"})
        except Exception as e:
            logger.error(f"KB review GET error: {e}", exc_info=True)
            self._json_response(500, {"error": str(e)})

    def _handle_kb_review_post(self, clean_path, raw_body):
        body = self._read_json_body(raw_body)
        if body is None:
            self._json_response(400, {"error": "Invalid JSON"})
            return
        try:
            if clean_path == "/api/kb-review/vote":
                vote = body.get("vote")
                if vote == "null":
                    vote = None
                result = kb_review.set_vote(body.get("cluster_id"), vote)
                self._json_response(200, result)
            elif clean_path == "/api/kb-review/comment":
                result = kb_review.set_comment(
                    body.get("cluster_id"), body.get("comment", ""),
                )
                self._json_response(200, result)
            else:
                self._json_response(404, {"error": "Not found"})
        except ValueError as e:
            self._json_response(400, {"error": str(e)})
        except Exception as e:
            logger.error(f"KB review POST error: {e}", exc_info=True)
            self._json_response(500, {"error": str(e)})

    # -- Response helpers ---------------------------------------------------
    def _serve_static_html(self, filename):
        """Serve a whitelisted HTML file from SCRIPT_DIR (diagrams only)."""
        path = os.path.join(SCRIPT_DIR, filename)
        if not os.path.isfile(path):
            self._json_response(404, {"error": "Not found"})
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                html = f.read()
        except OSError as e:
            logger.error(f"Failed to read static HTML {filename}: {e}")
            self._json_response(500, {"error": "Read failed"})
            return
        self._html_response(200, html)

    def _json_response(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html_response(self, status, html):
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    host = CONFIG.get("host", "0.0.0.0")
    port = CONFIG.get("port", 8080)

    server = HTTPServer((host, port), GorgiasWebhookHandler)
    logger.info(f"Gorgias Webhook Receiver starting on {host}:{port}")
    logger.info(f"  Webhook URL:  POST http://<your-ip>:{port}/webhook")
    logger.info(f"  Health check: GET  http://<your-ip>:{port}/health")
    logger.info(f"  Test page:    GET  http://<your-ip>:{port}/test")
    logger.info(f"  Config:       {CONFIG_PATH}")
    logger.info(f"  Log file:     {CONFIG.get('log_file', 'webhooks.log')}")
    logger.info(f"  JSONL log:    {os.path.join(SCRIPT_DIR, 'webhook_events.jsonl')}")
    if CONFIG.get("secret_token", "").startswith("change-this"):
        logger.warning("  WARNING: secret_token is still the default. Change it in config.json!")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.server_close()


if __name__ == "__main__":
    main()