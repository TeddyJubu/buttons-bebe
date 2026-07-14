#!/usr/bin/env python3
"""
demo_store.py — In-memory state for the Demo Dashboard System.

Holds tickets, messages, customers (with Shopify integration blocks),
Telegram inboxes, and an owner-reply queue for KB-gap polling.
"""

from __future__ import annotations

import re
import sys
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# qa_v3 fixtures for MOCK_ORDERS
sys.path.insert(0, "/root")
try:
    from qa_v3.fixtures_v3 import MOCK_ORDERS
except ImportError:
    MOCK_ORDERS = {}

DEFAULT_AGENT_USER_ID = 777419526


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mock_orders_for_email(email: str) -> List[dict]:
    """Return Gorgias-shaped order dicts for a customer email."""
    raw_orders = MOCK_ORDERS.get((email or "").strip().lower(), [])
    out = []
    for order in raw_orders:
        line_items = []
        for li in order.get("line_items") or []:
            if isinstance(li, dict):
                line_items.append({
                    "sku": li.get("sku") or "",
                    "title": li.get("title", ""),
                    "quantity": li.get("quantity", 1),
                })
            else:
                line_items.append({"sku": "", "title": str(li), "quantity": 1})
        out.append({
            "name": order.get("name"),
            "created_at": order.get("created_at"),
            "financial_status": order.get("financial_status"),
            "fulfillment_status": order.get("fulfillment_status"),
            "line_items": line_items,
            "shipping_address": order.get("shipping_address"),
            "billing_address": order.get("billing_address"),
        })
    return out


def _build_shopify_integration(email: str) -> dict:
    orders = _mock_orders_for_email(email)
    if not orders:
        return {}
    return {
        "999": {
            "__integration_type__": "shopify",
            "customer": {"orders_count": len(orders)},
            "orders": orders,
        },
    }


class DemoStore:
    """Thread-safe in-memory store for demo tickets and Telegram inboxes."""

    def __init__(self):
        self._lock = threading.RLock()
        self._ticket_id = 0
        self._message_id = 0
        self._customer_id = 0
        self._update_id = 0
        self.tickets: Dict[int, dict] = {}
        self.messages: Dict[int, List[dict]] = {}
        self.customers: Dict[int, dict] = {}
        self.telegram_notify: List[dict] = []
        self.telegram_priority: List[dict] = []
        self.owner_reply_queue: List[dict] = []
        self.last_run: Optional[dict] = None
        self.last_run_by_ticket: Dict[int, dict] = {}

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        with self._lock:
            self._ticket_id = 0
            self._message_id = 0
            self._customer_id = 0
            self._update_id = 0
            self.tickets.clear()
            self.messages.clear()
            self.customers.clear()
            self.telegram_notify.clear()
            self.telegram_priority.clear()
            self.owner_reply_queue.clear()
            self.last_run = None
            self.last_run_by_ticket.clear()

    def stats(self) -> dict:
        with self._lock:
            return {
                "ticket_count": len(self.tickets),
                "telegram_notify_count": len(self.telegram_notify),
                "telegram_priority_count": len(self.telegram_priority),
                "owner_queue_count": len(self.owner_reply_queue),
                "last_run": self.last_run,
            }

    # ------------------------------------------------------------------ #
    # ID helpers
    # ------------------------------------------------------------------ #
    def _next_ticket_id(self) -> int:
        self._ticket_id += 1
        return self._ticket_id

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    def _next_customer_id(self) -> int:
        self._customer_id += 1
        return self._customer_id

    def _next_update_id(self) -> int:
        self._update_id += 1
        return self._update_id

    # ------------------------------------------------------------------ #
    # Customers
    # ------------------------------------------------------------------ #
    def get_or_create_customer(self, email: str, name: Optional[str] = None) -> dict:
        email = (email or "").strip().lower()
        with self._lock:
            for cust in self.customers.values():
                if (cust.get("email") or "").lower() == email:
                    return deepcopy(cust)
            cid = self._next_customer_id()
            display_name = name or email.split("@")[0].replace(".", " ").title()
            customer = {
                "id": cid,
                "name": display_name,
                "email": email,
                "integrations": _build_shopify_integration(email),
            }
            self.customers[cid] = customer
            return deepcopy(customer)

    def get_customer(self, customer_id: int) -> Optional[dict]:
        with self._lock:
            cust = self.customers.get(int(customer_id))
            return deepcopy(cust) if cust else None

    # ------------------------------------------------------------------ #
    # Tickets
    # ------------------------------------------------------------------ #
    def create_ticket(
        self,
        email: str,
        subject: str,
        initial_message: str,
        *,
        name: Optional[str] = None,
        priority: str = "normal",
    ) -> dict:
        with self._lock:
            customer = self.get_or_create_customer(email, name=name)
            tid = self._next_ticket_id()
            now = _utc_now()
            ticket = {
                "id": tid,
                "subject": subject or "Support request",
                "status": "open",
                "priority": priority,
                "tags": [],
                "customer_id": customer["id"],
                "customer": {"id": customer["id"], "email": customer["email"], "name": customer["name"]},
                "created_datetime": now,
                "updated_datetime": now,
                "last_message": None,
            }
            self.tickets[tid] = ticket
            self.messages[tid] = []
            if initial_message:
                self._append_message_unlocked(
                    tid,
                    body_text=initial_message,
                    from_agent=False,
                    public=True,
                    channel="email",
                )
            return deepcopy(self.tickets[tid])

    def get_ticket(self, ticket_id: int) -> Optional[dict]:
        with self._lock:
            ticket = self.tickets.get(int(ticket_id))
            if not ticket:
                return None
            out = deepcopy(ticket)
            msgs = self.messages.get(int(ticket_id), [])
            if msgs:
                out["last_message"] = deepcopy(msgs[-1])
            return out

    def list_tickets(self) -> List[dict]:
        with self._lock:
            summaries = []
            for tid in sorted(self.tickets.keys(), reverse=True):
                t = self.tickets[tid]
                msgs = self.messages.get(tid, [])
                summaries.append({
                    "id": tid,
                    "subject": t.get("subject"),
                    "status": t.get("status"),
                    "priority": t.get("priority"),
                    "tags": list(t.get("tags") or []),
                    "customer_email": (t.get("customer") or {}).get("email"),
                    "message_count": len(msgs),
                    "updated_datetime": t.get("updated_datetime"),
                })
            return summaries

    def set_priority(self, ticket_id: int, priority: str) -> Optional[dict]:
        with self._lock:
            ticket = self.tickets.get(int(ticket_id))
            if not ticket:
                return None
            ticket["priority"] = str(priority).lower()
            ticket["updated_datetime"] = _utc_now()
            return deepcopy(ticket)

    def add_tags(self, ticket_id: int, tag_names: List[str]) -> Optional[dict]:
        with self._lock:
            ticket = self.tickets.get(int(ticket_id))
            if not ticket:
                return None
            existing = list(ticket.get("tags") or [])
            seen = set(existing)
            for tag in tag_names:
                name = str(tag).strip()
                if name and name not in seen:
                    existing.append(name)
                    seen.add(name)
            ticket["tags"] = existing
            ticket["updated_datetime"] = _utc_now()
            return deepcopy(ticket)

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def _append_message_unlocked(
        self,
        ticket_id: int,
        *,
        body_text: str,
        from_agent: bool,
        public: bool,
        channel: str,
        sender_id: Optional[int] = None,
    ) -> dict:
        mid = self._next_message_id()
        now = _utc_now()
        sender = {"id": sender_id or (DEFAULT_AGENT_USER_ID if from_agent else None)}
        msg = {
            "id": mid,
            "ticket_id": int(ticket_id),
            "body_text": body_text or "",
            "stripped_text": body_text or "",
            "from_agent": from_agent,
            "public": public,
            "channel": channel,
            "via": channel,
            "sender": sender,
            "created_datetime": now,
        }
        self.messages.setdefault(int(ticket_id), []).append(msg)
        ticket = self.tickets.get(int(ticket_id))
        if ticket:
            ticket["last_message"] = deepcopy(msg)
            ticket["updated_datetime"] = now
        return deepcopy(msg)

    def add_customer_message(self, ticket_id: int, body_text: str) -> Optional[dict]:
        with self._lock:
            if int(ticket_id) not in self.tickets:
                return None
            return self._append_message_unlocked(
                int(ticket_id),
                body_text=body_text,
                from_agent=False,
                public=True,
                channel="email",
            )

    def add_agent_public_reply(self, ticket_id: int, body_text: str) -> Optional[dict]:
        with self._lock:
            if int(ticket_id) not in self.tickets:
                return None
            return self._append_message_unlocked(
                int(ticket_id),
                body_text=body_text,
                from_agent=True,
                public=True,
                channel="email",
                sender_id=123456,
            )

    def add_internal_note(self, ticket_id: int, body_text: str, sender_id: int) -> Optional[dict]:
        with self._lock:
            if int(ticket_id) not in self.tickets:
                return None
            return self._append_message_unlocked(
                int(ticket_id),
                body_text=body_text,
                from_agent=True,
                public=False,
                channel="internal-note",
                sender_id=sender_id,
            )

    def post_message_from_payload(self, ticket_id: int, payload: dict) -> Optional[dict]:
        """Create a message from a Gorgias API POST body."""
        with self._lock:
            if int(ticket_id) not in self.tickets:
                return None
            channel = payload.get("channel") or "email"
            from_agent = bool(payload.get("from_agent", True))
            public = payload.get("public", channel != "internal-note")
            sender = payload.get("sender") or {}
            sender_id = sender.get("id") if isinstance(sender, dict) else None
            return self._append_message_unlocked(
                int(ticket_id),
                body_text=payload.get("body_text") or "",
                from_agent=from_agent,
                public=public,
                channel=channel,
                sender_id=sender_id,
            )

    def list_messages(self, ticket_id: int, limit: int = 100) -> List[dict]:
        with self._lock:
            msgs = self.messages.get(int(ticket_id), [])
            return deepcopy(msgs[: int(limit)])

    def get_ticket_detail(self, ticket_id: int) -> Optional[dict]:
        with self._lock:
            ticket = self.get_ticket(ticket_id)
            if not ticket:
                return None
            return {
                "ticket": ticket,
                "messages": self.list_messages(ticket_id),
                "last_run": self.last_run_by_ticket.get(int(ticket_id)),
            }

    # ------------------------------------------------------------------ #
    # Telegram
    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_ticket_id(text: str) -> Optional[int]:
        if not text:
            return None
        m = re.search(r"ticket\s*#(\d+)", text, re.I)
        if m:
            return int(m.group(1))
        m = re.search(r"#(\d{2,})", text)
        if m:
            return int(m.group(1))
        return None

    def append_telegram(
        self,
        bot: str,
        text: str,
        *,
        ticket_id: Optional[int] = None,
        metadata: Optional[dict] = None,
    ) -> dict:
        tid = ticket_id or self._extract_ticket_id(text)
        entry = {
            "text": text,
            "ticket_id": tid,
            "timestamp": _utc_now(),
            "metadata": metadata or {},
        }
        with self._lock:
            if bot == "priority":
                self.telegram_priority.append(entry)
            else:
                self.telegram_notify.append(entry)
        return entry

    def get_telegram(self, bot: str) -> List[dict]:
        with self._lock:
            inbox = self.telegram_priority if bot == "priority" else self.telegram_notify
            return deepcopy(inbox)

    def enqueue_owner_reply(self, text: str, chat_id: int = 1) -> dict:
        update_id = self._next_update_id()
        entry = {
            "update_id": update_id,
            "text": text,
            "chat_id": chat_id,
        }
        with self._lock:
            self.owner_reply_queue.append(entry)
        return entry

    def poll_replies(self, offset: Optional[int] = None) -> List[dict]:
        """Return Telegram getUpdates-shaped update objects."""
        with self._lock:
            pending = [
                e for e in self.owner_reply_queue
                if e["update_id"] > (offset or 0)
            ]
            if not pending:
                return []
            updates = []
            for entry in pending:
                updates.append({
                    "update_id": entry["update_id"],
                    "message": {
                        "message_id": entry["update_id"],
                        "chat": {"id": entry.get("chat_id", 1)},
                        "text": entry["text"],
                    },
                })
            return updates

    def record_run(self, ticket_id: int, result: dict) -> None:
        with self._lock:
            self.last_run = result
            self.last_run_by_ticket[int(ticket_id)] = result


# Module-level singleton
_store = DemoStore()


def get_store() -> DemoStore:
    return _store
