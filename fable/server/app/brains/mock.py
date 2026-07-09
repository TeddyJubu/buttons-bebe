"""MockBrain — deterministic, template-based drafting.

Same DraftContext in => same DraftResult out (tests rely on this). Never makes a
promise on sensitive tickets; always signs off "— Buttons Bebe Care Team".
"""
from __future__ import annotations

import re

from .base import DraftContext, DraftResult

SIGNOFF = "— Buttons Bebe Care Team"

_ORDER_KEYWORDS = ("order", "where", "track", "shipping", "shipped",
                   "deliver", "arrive", "package", "parcel", "status")
_SHIP_COUNTRY_MARKERS = ("do you ship", "ship to", "shipping to", "deliver to",
                         "shipped to", "ship internationally", "international shipping")


def _firstname(customer: dict) -> str:
    fn = (customer or {}).get("firstname")
    if fn:
        return fn.strip()
    name = (customer or {}).get("name")
    if name:
        return name.strip().split()[0]
    return "there"


def _pick_order(orders, text: str):
    """Most relevant order: one whose name is referenced in the text, else the
    first (most recent) order."""
    t = (text or "").lower().replace("#", "")
    for o in orders:
        nm = str(o.get("name", "")).lower().replace("#", "").strip()
        if nm and nm in t:
            return o
    return orders[0]


class MockBrain:
    name = "mock"

    def draft(self, ctx: DraftContext) -> DraftResult:
        first = _firstname(ctx.customer)
        text = (ctx.last_customer_text or "").lower()

        # 1. Sensitive: acknowledge, no promises, personal follow-up.
        if ctx.risk == "sensitive":
            body = (
                f"Hi {first},\n\n"
                "Thank you so much for reaching out, and I'm really sorry for the "
                "trouble you've run into — I completely understand your concern. "
                "I've flagged your message for our care team, and someone is looking "
                "into it personally right now. We'll review the details of your order "
                "and follow up with you very shortly to make sure this is handled "
                "properly.\n\n"
                "Thank you for your patience while we take a careful look.\n\n"
                f"{SIGNOFF}"
            )
            return DraftResult(body_text=body, kb_refs=["policy:escalation"],
                               notes="sensitive: no commitments made")

        mentions_order = any(k in text for k in _ORDER_KEYWORDS)
        mentions_ship_country = any(m in text for m in _SHIP_COUNTRY_MARKERS)

        # 2. Order-status with real context.
        if ctx.orders and mentions_order:
            order = _pick_order(ctx.orders, ctx.last_customer_text)
            body = self._order_reply(first, order)
            return DraftResult(body_text=body,
                               kb_refs=[f"order:{order.get('name', '')}", "faq:order-status"],
                               notes="order-status reply from live context")

        # 3. Shipping-to-country / "do you ship".
        if mentions_ship_country:
            body = self._shipping_reply(first)
            return DraftResult(body_text=body, kb_refs=["policy:shipping"],
                               notes="generic shipping answer")

        # 4. Polite fallback (ask for an order number when none was found).
        body = self._fallback_reply(first, asked_order=mentions_order)
        return DraftResult(body_text=body, kb_refs=["faq:general"],
                           notes="fallback acknowledgment")

    # -- reply builders ----------------------------------------------------
    def _order_reply(self, first: str, order: dict) -> str:
        name = order.get("name") or "your order"
        fs = (order.get("fulfillment_status") or "").lower()
        tn = order.get("tracking_number")
        tu = order.get("tracking_url")

        if fs == "fulfilled":
            status_line = f"Good news — {name} has shipped and is on its way to you!"
        elif fs in ("partial", "partially_fulfilled"):
            status_line = f"Part of {name} has shipped, and the rest is on its way shortly."
        else:
            status_line = (
                f"{name} is confirmed and our team is getting it packed up now. "
                "You'll receive tracking details by email the moment it ships."
            )

        track_line = ""
        if tn or tu:
            bits = []
            if tu:
                bits.append(f"track it here: {tu}")
            if tn:
                bits.append(f"tracking number {tn}")
            track_line = "\n\nYou can " + " — ".join(bits) + "."

        return (
            f"Hi {first},\n\n"
            f"Thanks so much for reaching out! I looked up {name} for you. "
            f"{status_line}"
            f"{track_line}\n\n"
            "If there's anything else I can help you with, just let me know.\n\n"
            f"{SIGNOFF}"
        )

    def _shipping_reply(self, first: str) -> str:
        return (
            f"Hi {first},\n\n"
            "Thanks for reaching out! We'd love to get our little Buttons Bebe pieces "
            "to you. We ship within the United States as standard, and we do offer "
            "international shipping to many countries — shipping options and delivery "
            "times are shown at checkout once you enter your address. If you let me know "
            "the country you'd like to ship to, I'm happy to confirm the details and "
            "estimated delivery time for you.\n\n"
            f"{SIGNOFF}"
        )

    def _fallback_reply(self, first: str, asked_order: bool) -> str:
        if asked_order:
            ask = (
                "So I can look into this right away, could you share your order number "
                "(it starts with #) or the email address used at checkout? Once I have "
                "that I can pull up the details and help you straight away."
            )
        else:
            ask = (
                "So I can point you in the right direction, could you share a little more "
                "detail about what you need? If it's about an order, your order number "
                "(it starts with #) would help me look it up quickly."
            )
        return (
            f"Hi {first},\n\n"
            "Thank you for reaching out to Buttons Bebe! "
            f"{ask}\n\n"
            "I'm here and happy to help.\n\n"
            f"{SIGNOFF}"
        )

    # -- rewrite -----------------------------------------------------------
    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult:
        instr = (instruction or "").strip()
        low = instr.lower()
        body = current_draft or ""

        if any(k in low for k in ("shorter", "short", "concise", "brief", "trim")):
            core = body.replace(SIGNOFF, "").strip()
            sentences = re.split(r"(?<=[.!?])\s+", core)
            kept = " ".join(s.strip() for s in sentences[:2] if s.strip()).strip()
            new_body = f"{kept}\n\n{SIGNOFF}"
            return DraftResult(body_text=new_body, kb_refs=[], notes="rewrite: shortened")

        if any(k in low for k in ("friendl", "warm", "nicer", "warmer")):
            lines = body.split("\n")
            warm = ("It's so lovely to hear from you, and thank you so much for your "
                    "patience!")
            if lines and lines[0].lower().startswith("hi"):
                # insert warm opener right after the greeting line + blank line
                insert_at = 1
                while insert_at < len(lines) and lines[insert_at].strip() == "":
                    insert_at += 1
                lines.insert(insert_at, warm)
                lines.insert(insert_at + 1, "")
                new_body = "\n".join(lines)
            else:
                new_body = warm + "\n\n" + body
            return DraftResult(body_text=new_body, kb_refs=[], notes="rewrite: friendlier")

        # translate / other → tag and keep body.
        new_body = f"[rewritten per: {instr}]\n{body}"
        return DraftResult(body_text=new_body, kb_refs=[], notes="rewrite: passthrough")
