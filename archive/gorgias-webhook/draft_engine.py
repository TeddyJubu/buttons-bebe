#!/usr/bin/env python3
"""
draft_engine.py — Stage 2, Task 7: turn a ticket + KB snippets into a SAFE,
human-reviewed DRAFT reply for the Buttons Bebe AI support agent.

WHAT THIS DOES (and what it deliberately does NOT do):

  generate_draft(ctx, classification=None, *, top_k=5) -> DraftResult

  It RETURNS data only. It NEVER sends anything to a customer and NEVER posts to
  Gorgias. Workflow A (Task 8) takes the DraftResult, decides whether to post an
  INTERNAL note for a human agent to review, and persists it via
  feedback_db.record_draft(...). Nothing here is auto-sent.

THE SAFETY MODEL (SYSTEM_WORKFLOW.md "Safety Model"; this module enforces it):

  1. Sensitive tickets are ESCALATED, not drafted. If the classifier says
     auto_draft_allowed is False (refund / chargeback / dispute / cancellation /
     not-delivered / legal / fraud / "speak to a manager" / empty-garbled), this
     engine produces NO customer-ready reply. It returns should_post=False and a
     clearly-labeled internal "⚠️ ESCALATE — do not auto-reply" note for the
     human. We classify *ourselves* if the caller doesn't pass a classification,
     so the engine is SAFE BY DEFAULT.

  2. We answer ONLY from the KB. The retrieval seam (kb_client.search) returning
     [] is a KB GAP — a meaningful, valid result meaning "no confident match".
     On a gap we do NOT let the LLM free-style policy. We return kb_gap=True with
     a safe internal holding note that asks the owner, and never invent a return
     window, price, date, or refund/discount promise.

  3. Grounded prompt. The system prompt pins the model to the supplied KB
     snippets + ticket/order context, forbids inventing policy/prices/dates/
     promises, matches the store's warm-brief-lowercase tone, and tells it to
     say it will "check with the team" rather than guess when the KB is silent.

  4. Resilient. If model_gateway raises LLMConfigError/LLMError (e.g. a live
     provider with no key), we degrade gracefully: should_post=False, reason
     "LLM unavailable". We never crash Workflow A. The whole module runs fully
     under the offline mock provider (the project default).

Stdlib only, plus the project modules (kb_client, model_gateway, classifier,
pipeline indirectly via duck-typing). No pip deps.

Run directly for the offline self-test (mock provider):
    python3 draft_engine.py
"""

import logging
import re
from dataclasses import dataclass, field

import kb_client
import model_gateway
import classifier

log = logging.getLogger("draft_engine")

# Minimal PII scrubber — applied to Hindsight memory text before it enters the
# LLM prompt. Keeps the same patterns as server._scrub_pii.
_PII_PATTERNS = [
    (re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b'), '[email]'),
    (re.compile(r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'), '[phone]'),
    (re.compile(r'\b(?:#?\d{4,}\b)'), '[order#]'),
    (re.compile(r'\b\d{5}(?:-\d{4})?\b'), '[zip]'),
]

def _scrub_pii(text):
    if not text or not isinstance(text, str):
        return text
    for pat, repl in _PII_PATTERNS:
        text = pat.sub(repl, text)
    return text


# --------------------------------------------------------------------------- #
# Result type — shaped so Workflow A can call feedback_db.record_draft easily.
# --------------------------------------------------------------------------- #
@dataclass
class DraftResult:
    """The outcome of one draft attempt for one ticket.

    Workflow A reads this to decide whether to post an INTERNAL note and how to
    persist it. Field-for-field it lines up with feedback_db.record_draft kwargs:
    draft_text -> draft_text, kb_sources -> kb_sources, kb_gap -> kb_gap,
    model_used -> model_used, confidence -> confidence, category -> (priority/
    classification), reason -> classification_reason.

    Fields:
      draft_text  — the text to put in the INTERNAL note. For a draftable ticket
                    this is a customer-style reply body (still reviewed by a
                    human, never auto-sent). For an escalation or KB gap it is a
                    clearly-labeled internal note for the agent — NOT a
                    customer-ready reply.
      kb_sources  — de-duped list of repo-relative KB source paths used (e.g.
                    "kb/policies/shipping-policy.md"). [] on a gap/escalation.
      kb_chunks   — the retrieved KBChunk objects' dict views (for audit/debug).
      confidence  — coarse confidence in the draft: "high" | "medium" | "low" |
                    "none". A string so it survives JSON and feedback_db storage.
      kb_gap      — True if KB retrieval returned nothing (no confident answer).
      category    — the classifier category (e.g. "shipping_status", "refund").
      model_used  — provider/model string from the gateway (e.g. "mock"), or a
                    sentinel like "none (escalated)" / "none (llm-unavailable)".
      should_post — True ONLY when the engine produced a customer-style DRAFT it
                    is safe to surface for human review. False for escalations,
                    LLM-unavailable, and (by default) KB gaps.
      reason      — short human-readable explanation of the decision (audit
                    trail; maps to drafts.classification_reason).
      is_escalation — True when the ticket is sensitive/escalate and was NOT
                    drafted (the note is an internal escalation summary).
      priority    — the classifier urgency (alias surfaced for record_draft).
    """

    draft_text: str = ""
    kb_sources: list = field(default_factory=list)
    kb_chunks: list = field(default_factory=list)
    confidence: str = "none"
    kb_gap: bool = False
    category: str = "unknown"
    model_used: str = "none"
    should_post: bool = False
    reason: str = ""
    is_escalation: bool = False
    priority: str = "normal"

    def as_dict(self):
        return {
            "draft_text": self.draft_text,
            "kb_sources": list(self.kb_sources),
            "kb_chunks": list(self.kb_chunks),
            "confidence": self.confidence,
            "kb_gap": self.kb_gap,
            "category": self.category,
            "model_used": self.model_used,
            "should_post": self.should_post,
            "reason": self.reason,
            "is_escalation": self.is_escalation,
            "priority": self.priority,
        }

    def record_draft_kwargs(self):
        """The subset Workflow A can splat into feedback_db.record_draft(...).

        Note: record_draft also needs ticket_id/customer_message/etc. that live
        on the TicketContext, not here — Workflow A supplies those. This just
        hands over the engine-owned fields in record_draft's vocabulary.
        """
        return {
            "draft_text": self.draft_text,
            "priority": self.priority,
            "classification_reason": _scrub_pii(self.reason or ""),
            "kb_sources": list(self.kb_sources),
            "kb_gap": 1 if self.kb_gap else 0,
            "model_used": self.model_used,
            "confidence": self.confidence,
        }


# --------------------------------------------------------------------------- #
# Grounded prompt — the safety instructions live here.
# --------------------------------------------------------------------------- #
# This is the heart of the "never invent policy" guarantee on the LLM side. It is
# quoted verbatim in the task report. Kept blunt and explicit on purpose.
SYSTEM_PROMPT = (
    "You are a support assistant for Buttons Bebe, a small children's clothing "
    "boutique. You are writing a DRAFT reply that a human agent will review "
    "before anything is sent. You are NOT talking to the customer directly.\n"
    "\n"
    "VOICE — this is the most important part:\n"
    "Write like a real person at a small boutique helping a customer. Think warm, "
    "friendly, and human — like you're chatting with a friend who needs help. "
    "Use contractions (we'll, it's, can't, don't). Keep it concise — 2-3 short "
    "sentences is plenty. No corporate boilerplate, no over-explaining, no "
    "formal language. Lowercase is fine. Emojis are fine when they fit naturally. "
    "The goal is to sound like a helpful human, not a script.\n"
    "\n"
    "REPLY STRUCTURE (follow this for most replies):\n"
    "1. One sentence acknowledging the customer's situation (sorry! / happy to help!)\n"
    "2. The answer or information — ONLY what they asked, from the KB snippets\n"
    "3. The next step — what you need from them, or what happens next\n"
    "4. Brief warm close (optional — let us know if you need anything else!)\n"
    "Keep the whole reply to 2-3 sentences. Answer ONLY what they asked — do NOT\n"
    "dump everything the KB says about the topic. If they asked about shipping,\n"
    "don't also tell them about returns.\n"
    "\n"
    "Examples of the right tone and length:\n"
    "  - \"hi! we switched the size for you — your order is now size 6.\"\n"
    "  - \"so sorry about that! please send us a photo of the damage and we'll "
    "check what we can do.\"\n"
    "  - \"we don't have exact measurements on this one, but we'll check and get "
    "right back to you!\"\n"
    "  - \"orders usually ship within 24-48 hours. after that, delivery time "
    "depends on the carrier you picked at checkout.\"\n"
    "\n"
    "HARD RULES — follow every one:\n"
    "1. Answer ONLY using the KNOWLEDGE BASE snippets and the ticket/order "
    "context provided below. Do not use outside knowledge.\n"
    "2. NEVER invent or guess policies, prices, dates, shipping times, return "
    "windows, tracking numbers, or order details. If a fact is not in the "
    "provided context, do not state it.\n"
    "3. NEVER promise, offer, confirm, or deny a refund, discount, store credit, "
    "compensation, or any money movement. Those are decided by a human only.\n"
    "4. If the knowledge base does not cover the question, do NOT guess — say "
    "warmly that you will check with the team and follow up.\n"
    "5. Match Buttons Bebe's voice: warm, friendly, concise, and lowercase "
    "(casual lowercase, like a quick friendly note). A couple of short "
    "sentences is plenty. No corporate boilerplate, no over-explaining.\n"
    "6. Do not make commitments on the carrier's behalf (you cannot guarantee a "
    "delivery date, only when an order leaves the warehouse).\n"
    "7. Output ONLY the reply body. No subject line, no preamble, no 'Draft:' "
    "label, no notes to the agent, no markdown headers.\n"
    "8. ORDER IDENTIFICATION: if the ORDER CONTEXT below already identifies the "
    "order (an order name/number or line items are shown), do NOT ask the "
    "customer for their order number — you already have it. Only ask for an "
    "order number when no order is identified in the context.\n"
    "9. DO NOT GUESS PRODUCT INFORMATION. For sizing, how an item runs/fit, "
    "measurements, fabric/material, sleeve length, or launch/restock dates, "
    "answer ONLY if that exact detail is in the KB snippets or order context "
    "above. If it is not there, do NOT estimate or generalize — warmly say "
    "you'll check (e.g. \"we'll check on that and get right back to you\"). "
    "Sizing and fit vary by brand and style, so never guess.\n"
    "9a. NEVER output a bracketed placeholder. The KB snippets contain TEMPLATE "
    "slots in square brackets — e.g. [true to size / small / roomy], "
    "[recommendation], [fabric/material], [new size], [date/time], [item name], "
    "[brand]. Those are blanks, NOT answers. If you cannot fill a slot with a "
    "real value taken from the KB snippets or order context above, do NOT copy "
    "the 'if known' template and do NOT leave the bracket in — instead use the "
    "template's 'if unknown' / 'we'll check' wording (warmly say you'll check "
    "and get back to them). If a snippet offers both an 'if known' and an "
    "'if unknown' version, pick the 'if unknown' one whenever the specific fact "
    "(this item's fit/fabric/measurement/sleeve/launch date) is not present in "
    "the context. Your reply must contain NO '[' or ']' characters.\n"
    "10. NEVER CLAIM AN ACTION WAS TAKEN unless the context clearly shows it was "
    "actually done. Do not say things like \"we changed your size\", \"we "
    "switched your order to pickup\", \"we updated your address\", or \"we "
    "removed the package protection\" unless the order context confirms it. If "
    "an action is only requested or pending, say it's being looked into / will "
    "be taken care of — describe intent, not a completed change you cannot "
    "verify.\n"
)


def _build_user_prompt(customer_message, subject, kb_chunks, order_context,
                       ai_analysis=None, conversation=None):
    """Assemble the grounded user prompt: KB snippets + ticket + order context.

    Every KB snippet is labeled with its source path so the model (and any human
    auditing the prompt) can see exactly what it was allowed to draw from.

    Args:
        ai_analysis  — dict from priority_logic with "reason" and "priority"
                       that explains the AI's understanding of the customer's
                       intent. Included in the prompt to help the LLM generate
                       a more targeted and contextually appropriate reply.
        conversation — optional list of message dicts (oldest-first). The last
                       5 messages are included so the model can see what was
                       already discussed and avoid repeating questions.
    """
    parts = []

    # If AI analysis is available, include it to guide the draft
    if ai_analysis and ai_analysis.get("reason"):
        parts.append("SITUATION ANALYSIS (from AI priority analysis):")
        parts.append("=" * 60)
        parts.append(f"Customer's issue: {ai_analysis['reason']}")
        parts.append(f"Priority: {ai_analysis.get('priority', 'unknown')}")
        parts.append("Use this context to understand what the customer really needs.")
        parts.append("")

    # Conversation history (before KB so the model reads it as context)
    if conversation:
        recent = conversation[-5:] if len(conversation) > 5 else conversation
        parts.append("CONVERSATION HISTORY (what has already been said — do NOT repeat):")
        parts.append("=" * 60)
        for msg in recent:
            if not isinstance(msg, dict):
                continue
            sender = "Agent" if msg.get("from_agent") else "Customer"
            body = _strip_email_quotes(msg.get("body_text") or msg.get("stripped_text") or "")
            body = _scrub_pii(body).strip()
            if body:
                parts.append(f"  [{sender}]: {body[:300]}")
        parts.append("")
        parts.append("Do NOT ask for information the customer already provided in the history above.")
        parts.append("")

    parts.append("KNOWLEDGE BASE SNIPPETS (the ONLY policy you may rely on):")
    parts.append("=" * 60)
    for i, ch in enumerate(kb_chunks, 1):
        heading = ch.get("heading") or "(intro)"
        src = ch.get("source", "?")
        status = ch.get("status") or ""
        status_note = f"  [status: {status}]" if status else ""
        parts.append(f"[{i}] source: {src}  ##{heading}{status_note}")
        parts.append((ch.get("text") or "").strip())
        parts.append("-" * 60)
    parts.append("")

    parts.append("TICKET")
    parts.append("=" * 60)
    if subject:
        parts.append(f"Subject: {subject}")
    parts.append("Customer's latest message:")
    parts.append((customer_message or "").strip() or "(no message text)")
    parts.append("")

    order_summary = _summarize_order_context(order_context)
    if order_summary:
        parts.append("ORDER CONTEXT (facts you MAY use; do not invent beyond this)")
        parts.append("=" * 60)
        parts.append(order_summary)
        parts.append("")

    product_summary = _summarize_product_context(customer_message, subject, order_context)
    if product_summary:
        parts.append("LIVE PRODUCT INFO (current catalog data you MAY use; do not invent beyond this)")
        parts.append("=" * 60)
        parts.append(product_summary)
        parts.append("")

    parts.append(
        "Write the draft reply now, following every HARD RULE. Remember: only "
        "facts from the snippets and order context above; never invent policy, "
        "prices, dates, or refund/discount promises; don't guess sizing/fit/"
        "measurements/fabric/sleeve/launch dates — say you'll check instead; "
        "NEVER output a bracketed [placeholder] from a template — if you can't "
        "fill it with a real value from the context, use the 'we'll check' "
        "wording; don't ask for an order number if the order is already "
        "identified above; never claim an action was completed unless the "
        "context confirms it; warm, brief, lowercase; output only the reply body."
    )
    return "\n".join(parts)


def _summarize_order_context(order_context):
    """Render the order_context dict (from gorgias_api.extract_order_context)
    into a few safe, factual lines — or "" if there's nothing usable.

    We surface ONLY fields that are actually present (order name, statuses, line
    items). We never fabricate tracking links/dates — extract_order_context
    itself reports those as gaps, so the model has no source for them and the
    HARD RULES forbid inventing them.
    """
    if not isinstance(order_context, dict):
        return ""
    orders = order_context.get("orders") or []
    if not orders:
        # Tell the model plainly that we have no order data, so it doesn't guess.
        if order_context.get("shopify_found") is False:
            return "No linked Shopify order data is available for this customer."
        return ""

    lines = []
    for o in orders[:3]:
        if not isinstance(o, dict):
            continue
        name = o.get("name") or "(unnamed order)"
        fin = o.get("financial_status") or "unknown"
        ful = o.get("fulfillment_status") or "unknown"
        lines.append(f"Order {name}: payment={fin}, fulfillment={ful}")
        _tns = o.get("tracking_numbers") or []
        _tus = o.get("tracking_urls") or []
        if _tns or _tus:
            _carrier = o.get("carrier") or ""
            _csp = f"{_carrier} " if _carrier else ""
            lines.append(f"  tracking: {_csp}{(_tus[0] if _tus else _tns[0])}")
        items = o.get("line_items") or []
        for li in items[:6]:
            if not isinstance(li, dict):
                continue
            title = li.get("title") or li.get("sku") or "item"
            qty = li.get("quantity")
            qty_s = f" x{qty}" if qty else ""
            lines.append(f"  - {title}{qty_s}")
    return "\n".join(lines)


# Common words to ignore when guessing a product name from ticket text.
_PRODUCT_STOPWORDS = {
    "the", "and", "for", "you", "your", "are", "was", "with", "this", "that",
    "have", "has", "had", "would", "could", "should", "can", "get", "got",
    "want", "wanted", "need", "needed", "know", "please", "thanks", "thank",
    "hello", "hey", "order", "ordered", "item", "items", "product", "products",
    "size", "sizes", "color", "colors", "colour", "colours", "stock", "instock",
    "available", "availability", "buy", "buying", "purchase", "purchased",
    "carry", "carries", "sell", "sells", "offer", "offers", "restock",
    "restocked", "ship", "shipping", "shipped", "delivery", "deliver",
    "return", "returns", "refund", "exchange", "exchanges", "cancel",
    "tracking", "track", "where", "when", "what", "which", "about", "still",
    "from", "into", "just", "like", "but", "not", "any", "all", "out", "one",
    "two", "see", "did", "does", "wondering", "looking", "interested", "there",
    "their", "they", "will", "been", "some", "send", "sent", "back", "more",
}


def _product_search_terms(text):
    import re
    out = []
    for w in re.split(r"[^0-9A-Za-z]+", (text or "").lower()):
        if len(w) <= 2:
            continue
        if w in _PRODUCT_STOPWORDS:
            continue
        if re.fullmatch(r"\d{1,3}[a-z]{0,2}", w):  # size-like tokens: 12, 12m, 2t, 24m
            continue
        out.append(w)
    return out


def _summarize_product_context(customer_message, subject, order_context, max_products=4):
    # LIVE product info relevant to this ticket:
    #   (0) brands the customer named that we actually carry (strongest signal)
    #   (a) the customer's ordered items
    #   (b) products named in the ticket text
    # Read-only and fail-soft: any problem returns "".
    import os, sys, re
    _d = os.path.dirname(os.path.abspath(__file__))
    if _d not in sys.path:
        sys.path.insert(0, _d)
    try:
        import product_lookup
    except Exception:
        return ""

    seen = {}
    def _add(p):
        key = (p.get("handle") or p.get("title") or "").lower()
        if key and key not in seen:
            seen[key] = p

    text_l = ((subject or "") + " " + (customer_message or "")).lower()

    # (0) brand mention -- most reliable answer to "do you carry X?"
    carried = []
    try:
        for v in (product_lookup.list_vendors() or []):
            if len(v) < 4:
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(v.lower()) + r"(?![a-z0-9])", text_l):
                carried.append(v)
        for v in carried[:2]:
            for p in product_lookup.products_by_vendor(v, limit=3):
                _add(p)
    except Exception:
        pass

    # (a) ordered items
    if isinstance(order_context, dict):
        titles = []
        for o in (order_context.get("orders") or [])[:3]:
            if not isinstance(o, dict):
                continue
            for li in (o.get("line_items") or [])[:5]:
                t = (li.get("title") or "").strip()
                if t and t.lower() not in [x.lower() for x in titles]:
                    titles.append(t)
        for t in titles[:3]:
            try:
                for p in product_lookup.search_products(t, limit=1, active_only=False):
                    _add(p)
            except Exception:
                pass

    # (b) ticket-text mention
    if len(seen) < max_products:
        terms = _product_search_terms(text_l)
        queries = []
        if len(terms) >= 2:
            queries.append(" ".join(terms[:5]))
            queries.append(" ".join(sorted(terms, key=len, reverse=True)[:2]))
        elif len(terms) == 1:
            queries.append(terms[0])
        for q in queries:
            before = len(seen)
            try:
                hits = product_lookup.search_products(q, limit=3, active_only=True)
            except Exception:
                hits = []
            for p in hits:
                _add(p)
                if len(seen) >= max_products:
                    break
            if len(seen) > before or len(seen) >= max_products:
                break

    if not seen and not carried:
        return ""

    lines = []
    if carried:
        lines.append("We carry these brand(s) the customer mentioned: " + ", ".join(carried[:3]) + ".")
    for p in list(seen.values())[:max_products]:
        title = p.get("title") or "(product)"
        vendor = p.get("vendor")
        cur = p.get("currency") or ""
        pmin = p.get("price_min"); pmax = p.get("price_max")
        if pmin and pmax and pmin != pmax:
            price = (str(pmin) + "-" + str(pmax) + " " + cur).strip()
        elif pmin:
            price = (str(pmin) + " " + cur).strip()
        else:
            price = "price n/a"
        sizes = p.get("sizes_in_stock") or []
        stock = ("in stock: " + ", ".join(map(str, sizes))) if sizes else "currently out of stock"
        by = (" (" + vendor + ")") if vendor else ""
        line = title + by + ": " + price + "; " + stock
        url = p.get("url")
        if url:
            line += "; " + url
        lines.append(line)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Input coercion — accept a TicketContext, a dict, or raw text.
# --------------------------------------------------------------------------- #
def _extract_message_subject_order(ctx):
    """Pull (customer_message, subject, order_context) from any accepted input.

    Mirrors the classifier's extraction so the two stages see the same text:
    the latest from_agent=False message body (body_text/stripped_text), the
    ticket subject, and the order_context dict if present.
    """
    # Raw string -> the message itself, no subject/order.
    if isinstance(ctx, str):
        return ctx, None, None

    if ctx is None:
        return "", None, None

    # TicketContext object (duck-typed) ------------------------------------- #
    if hasattr(ctx, "messages") or hasattr(ctx, "ticket"):
        messages = getattr(ctx, "messages", None) or []
        ticket = getattr(ctx, "ticket", None) or {}
        order_context = getattr(ctx, "order_context", None)
        msg, subject = _latest_customer_message(messages, ticket)
        return msg, subject, order_context

    # Dict input ------------------------------------------------------------ #
    if isinstance(ctx, dict):
        if "messages" in ctx:
            messages = ctx.get("messages") or []
            ticket = ctx.get("ticket") or {}
            order_context = ctx.get("order_context")
            msg, subject = _latest_customer_message(messages, ticket)
            return msg, subject, order_context
        # Flat dict like {"text"/"body_text"/"message", "subject", "order_context"}
        msg = (
            ctx.get("text")
            or ctx.get("body_text")
            or ctx.get("stripped_text")
            or ctx.get("message")
            or ""
        )
        return msg, ctx.get("subject"), ctx.get("order_context")

    # Unknown type — stringify defensively (never crash).
    return str(ctx), None, None


def _latest_customer_message(messages, ticket):
    """Latest from_agent=False message body + ticket subject (oldest-first list).

    Falls back to the most recent message of any kind if no customer message has
    text, matching classifier._latest_customer_text_and_subject's behavior.
    """
    subject = ticket.get("subject") if isinstance(ticket, dict) else None

    def _body(m):
        if not isinstance(m, dict):
            return ""
        return m.get("body_text") or m.get("stripped_text") or ""

    def _is_customer(m):
        if not isinstance(m, dict):
            return False
        fa = m.get("from_agent")
        if isinstance(fa, str):
            return fa.strip().lower() not in ("true", "1", "yes")
        return not bool(fa)

    customer = [m for m in messages if _is_customer(m) and _body(m).strip()]
    if customer:
        latest = customer[-1]
        if subject is None:
            subject = latest.get("subject")
        return _body(latest), subject

    any_text = [m for m in messages if _body(m).strip()]
    if any_text:
        latest = any_text[-1]
        if subject is None and isinstance(latest, dict):
            subject = latest.get("subject")
        return _body(latest), subject

    return "", subject


# --------------------------------------------------------------------------- #
# KB query builder — filters out non-descriptive subjects and email quotes
# --------------------------------------------------------------------------- #
import re as _re

# Subjects that are generic email boilerplate and carry no semantic signal.
# Including them in the KB query dilutes the embedding and returns irrelevant
# results. We match case-insensitively against the stripped subject.
_GENERIC_SUBJECT_PATTERNS = [
    _re.compile(r"^re:\s*", _re.IGNORECASE),          # "Re: ..." reply chains
    _re.compile(r"^fwd?:\s*", _re.IGNORECASE),        # "Fwd: ..." forwards
    _re.compile(r"^aw:\s*", _re.IGNORECASE),          # German "Aw: ..." replies
    _re.compile(r"message from buttons bebe", _re.IGNORECASE),
    _re.compile(r"message from .{3,40}$", _re.IGNORECASE),  # "Message from <store>"
    _re.compile(r"^testing\b", _re.IGNORECASE),       # "Testing Draft Reply"
    _re.compile(r"^test\b", _re.IGNORECASE),           # "Test"
    _re.compile(r"^hello\b", _re.IGNORECASE),          # "Hello" / "Hi"
    _re.compile(r"^\s*\d+\s*$"),                      # just a number
]

# Minimum subject length to be considered descriptive. Short subjects like
# "hi" or "?" add no signal. We require at least 8 chars after stripping
# generic prefixes.
_MIN_DESCRIPTIVE_SUBJECT_LEN = 8

# Email quoted-reply patterns that indicate the start of quoted history.
# Everything from the first match onward is the previous email thread, not
# the customer's actual new message. We strip it before sending to the KB.
_EMAIL_QUOTE_PATTERNS = [
    _re.compile(r"\r?\n\s*On\s+.+\s+wrote:\s*", _re.IGNORECASE),   # "On <date> ... wrote:"
    _re.compile(r"\r?\n\s*El\s+.+\s+escribi[oó]:\s*", _re.IGNORECASE),  # Spanish
    _re.compile(r"\r?\n-{2,}\s*Original Message\s*-{2,}", _re.IGNORECASE),  # "--- Original Message ---"
    _re.compile(r"\r?\n\s*From:.*\r?\n\s*(?:Sent|To|Subject):", _re.IGNORECASE),  # "From: ... Sent: ..."
    _re.compile(r"\r?\n>"),                          # line starting with ">" (quoted)
    _re.compile(r"\r?\n\s*\|"),                     # line starting with "|" (some clients)
]


def _is_descriptive_subject(subject):
    """True if the subject line carries semantic signal worth including in
    the KB query.

    Returns False for generic email boilerplate like "Re: Message from
    Buttons Bebe", "Testing Draft Reply", "hello", etc. These dilute the
    semantic search embedding and cause irrelevant KB hits.
    """
    if not subject or not isinstance(subject, str):
        return False
    s = subject.strip()
    if not s or len(s) < _MIN_DESCRIPTIVE_SUBJECT_LEN:
        return False
    for pat in _GENERIC_SUBJECT_PATTERNS:
        if pat.search(s):
            return False
    return True


def _strip_email_quotes(text):
    """Remove quoted email reply history from a message body.

    Email clients append the previous conversation as quoted text (lines
    starting with '>', or 'On <date> ... wrote:' blocks). This quoted
    history is NOT the customer's actual new message — it pollutes the
    KB semantic search with old conversation topics.

    We find the FIRST quote marker and keep only the text before it.
    If no quote marker is found, return the original text unchanged.
    """
    if not text or not isinstance(text, str):
        return text or ""
    earliest = None
    for pat in _EMAIL_QUOTE_PATTERNS:
        m = pat.search(text)
        if m:
            pos = m.start()
            if earliest is None or pos < earliest:
                earliest = pos
    if earliest is not None:
        return text[:earliest].strip()
    return text.strip()


def _build_kb_query(subject, customer_message):
    """Build the KB search query from the customer message, optionally
    enriched with a descriptive subject.

    The customer message is always the primary signal. The subject is only
    prepended when it is descriptive (i.e. carries semantic meaning beyond
    generic email boilerplate). This prevents subjects like "Re: Message
    from Buttons Bebe" or "Testing Draft Reply" from polluting the semantic
    search and returning irrelevant KB chunks.

    Email quoted reply history (the "On <date> ... wrote:" thread) is also
    stripped from the customer message before building the query, because
    quoted history from previous messages dilutes the semantic embedding
    with irrelevant topics.
    """
    msg = _strip_email_quotes(customer_message or "")
    sub = (subject or "").strip()

    if _is_descriptive_subject(sub) and msg:
        return f"{sub} {msg}"
    return msg or sub or ""


# --------------------------------------------------------------------------- #
# Gap note builder
# --------------------------------------------------------------------------- #
def _kb_gap_note(customer_message, subject):
    """Internal holding note for a KB gap. NEVER fabricates policy.

    Short, clear label so the agent knows no draft was generated.
    """
    return "NO KB MATCH — owner input needed, no draft generated"


def _escalation_note(customer_message, subject, classification):
    """Internal, clearly-labeled escalate note for sensitive tickets. No customer
    reply, no policy/price/refund promise — a human handles it."""
    cat = getattr(classification, "category", "unknown")
    urg = getattr(classification, "urgency", "normal")
    reasons = "; ".join(getattr(classification, "reasons", []) or []) or "sensitive/escalation category"
    return (
        "⚠️ ESCALATE — DO NOT AUTO-REPLY\n"
        f"Category: {cat} | Urgency: {urg}\n"
        f"Why: {reasons}\n"
        "This ticket is sensitive (refund / chargeback / dispute / cancellation / "
        "wrong-item / damaged / final-sale-exception) and must be handled by a human. "
        "No customer reply was drafted and nothing (refund/discount/policy) was promised. "
        "Review the order and respond manually."
    )


# --------------------------------------------------------------------------- #
# Confidence heuristic (coarse, deterministic — no extra model call)
# --------------------------------------------------------------------------- #
def _confidence(kb_chunks):
    """Coarse confidence from retrieval strength. String-valued for storage.

    Based only on KB score/coverage — NOT a claim about correctness. A human
    reviews every draft regardless.

    Thresholds are tuned for the pgvector cosine similarity scale (0..1):
      high   — top hit >= 0.75 with at least 2 chunks (strong match)
      medium — top hit >= 0.65 (decent match)
      low    — below 0.65 (weak match, may be marginally relevant)
    """
    if not kb_chunks:
        return "none"
    top = kb_chunks[0].get("score", 0.0) or 0.0
    if top >= 0.75 and len(kb_chunks) >= 2:
        return "high"
    if top >= 0.65:
        return "medium"
    return "low"


# Spell-correction prompt for KB query refinement
_SPELLCHECK_SYSTEM_PROMPT = (
    "You are a spell checker for customer support messages. Fix any spelling "
    "mistakes in the user's message. Rules:\n"
    "1. Fix ONLY spelling mistakes. Do NOT change the meaning, add words, or "
    "remove words.\n"
    "2. Keep the same language (English stays English, etc.).\n"
    "3. Do NOT fix grammar, punctuation, or capitalization — only spelling.\n"
    "4. Output ONLY the corrected text, nothing else. No explanations."
)


def _spellcorrect_query(text):
    """Run a quick LLM spell-correction pass on the KB query.

    This fixes misspellings like "retunr" -> "return" that would otherwise
    cause the KB semantic search to miss relevant results. Resilient: if
    the LLM is unavailable, returns the original text unchanged.
    """
    if not text or not text.strip() or len(text) < 5:
        return text or ""
    try:
        result = model_gateway.complete(
            [
                {"role": "system", "content": _SPELLCHECK_SYSTEM_PROMPT},
                {"role": "user", "content": text[:2000]},  # cap input length
            ],
            temperature=0.0,
        )
        corrected = (result.get("text") or "").strip()
        if not corrected:
            return text
        # Safety: if the corrected text is wildly different in length, keep original
        if len(corrected) < len(text) * 0.5 or len(corrected) > len(text) * 2.0:
            log.warning("Spellcheck output length drift — keeping original query.")
            return text
        return corrected
    except model_gateway.LLMError as exc:
        log.debug("Spellcheck LLM unavailable (%s) — using original query.", exc)
        return text
    except Exception as exc:
        log.debug("Spellcheck unexpected error (%s) — using original query.", exc)
        return text


# --------------------------------------------------------------------------- #
# Context-aware KB query builder — analyzes the full ticket before searching
# --------------------------------------------------------------------------- #
_QUERY_BUILDER_SYSTEM_PROMPT = (
    "You are a search query optimizer for a knowledge base at a children's "
    "clothing boutique called Buttons Bebe.\n\n"
    "You will be given a customer support ticket with:\n"
    "- The customer's latest message (may contain spelling/grammar errors)\n"
    "- The ticket subject\n"
    "- The full conversation history\n"
    "- The customer's order context (if available)\n\n"
    "Your job: analyze ALL of this context and produce the BEST possible search "
    "queries to find relevant knowledge base articles. Rules:\n"
    "1. Correct any spelling or grammar errors in the customer's message before "
    "using it to build queries.\n"
    "2. Identify the customer's ACTUAL intent — what are they really asking?\n"
    "3. Consider the conversation history: if the customer asked about shipping "
    "earlier and now asks about 'retunr policy', they want the RETURN policy.\n"
    "4. Consider the order context: if there's an order with fulfillment issues, "
    "the question might be about that specific order.\n"
    "5. Generate 1-3 search queries, each on its own line. Start with the most "
    "specific query first, then broader fallbacks.\n"
    "6. Each query should be a natural phrase or question, NOT keywords. "
    "Examples: 'return and exchange policy', 'how long does shipping take', "
    "'wrong size exchange before shipping'.\n"
    "7. Keep queries concise (under 100 characters each).\n"
    "8. Do NOT include customer names, email addresses, or order numbers in the "
    "queries.\n"
    "9. Output ONLY the search queries, one per line. No numbering, no labels, "
    "no explanations."
)


def _build_context_aware_queries(customer_message, subject, messages, order_context,
                                  ai_analysis=None):
    """Analyze the full ticket context and generate optimized KB search queries.

    This replaces the old _build_kb_query + _spellcorrect_query two-step with
    a single LLM call that:
      - Corrects spelling/grammar in the customer message
      - Considers the full conversation history (not just the latest message)
      - Considers the order context
      - Uses AI analysis from priority_logic (if available) for better intent understanding
      - Generates 1-3 targeted search queries

    Args:
        ai_analysis — dict from priority_logic with "reason" and "priority"
                      that explains the AI's understanding of the customer's
                      intent. Used to generate more accurate KB queries.

    Returns:
        list of query strings (empty list on failure — caller should fall back
        to the simple _build_kb_query approach).
    """
    # Build the context summary for the LLM
    parts = []

    # If AI analysis is available, include it as context for better queries
    if ai_analysis and ai_analysis.get("reason"):
        parts.append("AI ANALYSIS OF CUSTOMER INTENT:")
        parts.append(f"The customer's issue has been analyzed as: {ai_analysis['reason']}")
        parts.append(f"Priority level: {ai_analysis.get('priority', 'unknown')}")
        parts.append("Use this analysis to generate search queries that match the customer's actual need.")
        parts.append("")

    # Clean the customer message (strip email quotes)
    clean_msg = _strip_email_quotes(customer_message or "")
    parts.append("CUSTOMER'S LATEST MESSAGE:")
    parts.append(clean_msg or "(no message text)")
    parts.append("")

    if subject:
        parts.append(f"TICKET SUBJECT: {subject}")
        parts.append("")

    # Conversation history (last 5 messages, stripped of quotes)
    if messages:
        parts.append("CONVERSATION HISTORY:")
        recent = messages[-5:] if len(messages) > 5 else messages
        for msg in recent:
            sender = "Agent" if msg.get("from_agent") else "Customer"
            body = _strip_email_quotes(msg.get("body_text") or "")
            body = _scrub_pii(body).strip()
            if body:
                parts.append(f"  [{sender}]: {body[:200]}")
        parts.append("")

    # Order context
    if order_context and isinstance(order_context, dict):
        orders = order_context.get("orders") or []
        if orders:
            parts.append("ORDER CONTEXT:")
            for o in orders[:3]:
                if isinstance(o, dict):
                    name = o.get("name", "?")
                    fin = o.get("financial_status", "?")
                    ful = o.get("fulfillment_status", "?") or "?"
                    items = o.get("line_items") or []
                    item_names = ", ".join(
                        (li.get("title") or "?")[:30]
                        for li in items[:3]
                        if isinstance(li, dict)
                    )
                    parts.append(f"  Order {name}: {fin}/{ful}, items: {item_names}")
            parts.append("")

    try:
        result = model_gateway.complete(
            [
                {"role": "system", "content": _QUERY_BUILDER_SYSTEM_PROMPT},
                {"role": "user", "content": "\n".join(parts)},
            ],
            temperature=0.0,
        )
        raw = (result.get("text") or "").strip()
        if not raw:
            return []
        # Parse: one query per line, skip empty lines
        queries = [
            line.strip()
            for line in raw.split("\n")
            if line.strip()
            and not line.strip().startswith("#")
            and not line.strip().startswith("Query")
            and len(line.strip()) > 3
        ]
        # De-duplicate while preserving order
        seen = set()
        unique = []
        for q in queries:
            ql = q.lower()
            if ql not in seen:
                seen.add(ql)
                unique.append(q)
        return unique[:3]  # max 3 queries
    except model_gateway.LLMError as exc:
        log.warning("Query builder LLM unavailable (%s) — will fall back.", exc)
        return []
    except Exception as exc:
        log.warning("Query builder unexpected error (%s) — will fall back.", exc)
        return []


def _search_kb_multi_query(queries, top_k=12, min_score=kb_client.DEFAULT_MIN_SCORE):
    """Search the KB with multiple queries and merge results intelligently.

    For each query, we search the KB and collect hits. Results are merged and
    de-duplicated by source+heading, keeping the highest score per unique chunk.
    This prevents data miss by casting a wider net across different phrasings
    of the same intent.

    Returns:
        list of KBChunk objects, de-duplicated and sorted by score descending.
    """
    all_hits = []
    seen_keys = set()

    for query in queries:
        if not query or not query.strip():
            continue
        try:
            hits = kb_client.search(query, top_k=top_k, min_score=min_score)
        except Exception as exc:
            log.warning("kb_client.search failed for query '%s' (%s)", query[:50], exc)
            continue
        for h in hits:
            d = h.as_dict()
            # De-dup key: source + heading (same chunk from different queries)
            key = (d.get("source", ""), d.get("heading", ""))
            if key not in seen_keys:
                seen_keys.add(key)
                all_hits.append(h)
        if hits:
            log.info(
                "KB query '%s' -> %d hits (%d unique so far)",
                query[:60], len(hits), len(all_hits),
            )

    # Sort by score descending
    all_hits.sort(key=lambda h: h.score if hasattr(h, "score") else 0, reverse=True)
    return all_hits[:top_k]


# --------------------------------------------------------------------------- #
# PUBLIC API
# --------------------------------------------------------------------------- #
def generate_draft(ctx, classification=None, *, top_k=5, min_score=kb_client.DEFAULT_MIN_SCORE):
    """Turn a ticket (+KB) into a SAFE DraftResult for human review.

    Args:
      ctx            — a pipeline.TicketContext, a dict (TicketContext.to_dict(),
                       webhook-ish, or {"text"/"body_text"/"message","subject",
                       "order_context"}), or a raw customer-message string.
                       If ctx has an "ai_analysis" attribute (from priority_logic),
                       it will be used to improve KB queries and draft quality.
      classification — an optional classifier.Classification. If omitted, we
                       call classifier.classify(ctx) ourselves so the engine is
                       SAFE BY DEFAULT (an unclassified sensitive ticket can
                       never slip through to a customer-style draft).
      top_k          — max KB chunks to retrieve / ground on.
      min_score      — KB relevance floor (defaults to kb_client's tuned value).

    Returns:
      DraftResult. NEVER sends/posts anything. should_post=True only for a
      benign, KB-grounded, customer-style draft; False for escalations,
      KB gaps, and LLM-unavailable. See the dataclass docstring for fields.
    """
    customer_message, subject, order_context = _extract_message_subject_order(ctx)

    # Extract AI analysis from context (if available from priority_logic)
    ai_analysis = getattr(ctx, "ai_analysis", None)

    # --- Classify for category/priority + escalation gating. ----------------
    # The classifier result drives SAFETY GATE 1 below: sensitive tickets
    # (auto_draft_allowed=False) get an internal escalate note, never a draft.
    if classification is None:
        try:
            classification = classifier.classify(ctx)
        except Exception as exc:
            log.warning("classifier.classify failed (%s) — using defaults.", exc)
            classification = classifier.Classification(
                category="unknown",
                urgency=classifier.URGENCY_NORMAL,
                escalate=False,
                sensitive=False,
                reasons=["classifier error — using defaults"],
            )
            classification.recompute_auto_draft()

    category = getattr(classification, "category", "unknown")
    priority = getattr(classification, "urgency", "normal")

    # --- SAFETY GATE 1: sensitive / escalation -> NO customer draft. --------
    # A refund/chargeback/dispute/cancellation/wrong-item/damaged/final-sale-
    # exception ticket is routed to a human. We never draft a customer reply or
    # promise money. (Restores the documented invariant; see module docstring.)
    if not getattr(classification, "auto_draft_allowed", False):
        return DraftResult(
            draft_text=_escalation_note(customer_message, subject, classification),
            kb_sources=[],
            kb_chunks=[],
            confidence="none (escalated)",
            kb_gap=False,
            category=category,
            model_used="none (escalated)",
            should_post=False,
            reason="Sensitive/escalation ticket — routed to a human; no customer draft.",
            is_escalation=True,
            priority=priority,
        )

    # --- Retrieve grounding from the KB (context-aware multi-query). -------- #
    # Step 1: Analyze the full ticket context (conversation, order, subject)
    #         and generate optimized search queries with spelling/grammar fixed.
    # Step 2: Search the KB with each query and merge/dedup results.
    # Fallback: if the LLM query builder fails, use the simple _build_kb_query
    #           approach (strip quotes + spellcheck) so we never miss a search.
    # If AI analysis is available from priority_logic, use it to improve queries.
    messages = getattr(ctx, "messages", None) or (ctx.get("messages") if isinstance(ctx, dict) else None) or []
    queries = _build_context_aware_queries(
        customer_message, subject, messages, order_context,
        ai_analysis=ai_analysis,
    )

    if queries:
        log.info("Context-aware KB queries: %s", queries)
        hits = _search_kb_multi_query(queries, top_k=top_k, min_score=min_score)
    else:
        # Fallback: simple query (strips email quotes, filters generic subjects)
        fb_query = _build_kb_query(subject, customer_message)
        fb_query = _spellcorrect_query(fb_query)
        log.info("Fallback KB query: '%s'", fb_query[:100])
        try:
            hits = kb_client.search(fb_query, top_k=top_k, min_score=min_score)
        except Exception as exc:
            log.warning("kb_client.search failed (%s) — treating as KB gap.", exc)
            hits = []

    kb_chunks = [h.as_dict() for h in hits]
    kb_sources = []
    for ch in kb_chunks:
        src = ch.get("source")
        if src and src not in kb_sources:
            kb_sources.append(src)

    # --- Retrieve learned memories from Hindsight (agent memory layer). ------ #
    # Hindsight stores experiences from past agent replies (Workflow B) and
    # owner Q&A. These complement the static KB with learned knowledge.
    hindsight_chunks = []
    try:
        from hindsight_integration import recall_relevant_memories
        memories = recall_relevant_memories(queries[0] if queries else (customer_message or ""), top_k=3)
        for m in memories:
            hindsight_chunks.append({
                "source": f"hindsight/{m.get('type', 'memory')}",
                "title": "Learned memory",
                "heading": m.get("type", ""),
                "text": _scrub_pii(m.get("text", "")),
                "score": 0.0,  # Hindsight doesn't expose a comparable score
                "status": "learned",
                "tags": m.get("tags", []),
            })
            src = f"hindsight/{m.get('type', 'memory')}"
            if src not in kb_sources:
                kb_sources.append(src)
        if hindsight_chunks:
            log.info("Hindsight recalled %d memories", len(hindsight_chunks))
    except ImportError as exc:
        log.debug("Hindsight recall skipped (not installed): %s", exc)
    except Exception as exc:
        log.warning("Hindsight recall failed: %s", exc)

    # Merge: KB chunks first (higher trust), then Hindsight memories
    all_chunks = kb_chunks + hindsight_chunks

    # --- SAFETY GATE 2: KB gap -> do NOT let the LLM free-style policy. -----
    # A gap is defined by the absence of verified KB hits. Hindsight memories
    # alone are NOT a substitute for KB grounding (they may be stale/unverified),
    # so the gate fires whenever KB search returned nothing.
    if not hits:
        return DraftResult(
            draft_text=_kb_gap_note(customer_message, subject),
            kb_sources=[],
            kb_chunks=[],
            confidence="none",
            kb_gap=True,
            category=category,
            model_used="none (kb-gap)",
            should_post=False,  # holding note for the agent; nothing auto-sent
            reason="KB GAP — no confident KB match; owner input needed, no policy fabricated.",
            is_escalation=False,
            priority=priority,
        )

    # --- Grounded LLM draft (mock provider by default; resilient on error). -
    user_prompt = _build_user_prompt(
        customer_message, subject, all_chunks, order_context,
        ai_analysis=ai_analysis,
        conversation=messages,
    )
    try:
        result = model_gateway.complete(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
        )
        draft_text = (result.get("text") or "").strip()
        model_used = result.get("provider") or "?"
        model_name = result.get("model")
        if model_name and model_name != model_used:
            model_used = f"{model_used}/{model_name}"
    except model_gateway.LLMError as exc:
        # Covers LLMConfigError (live provider, no key) and LLMHTTPError/network.
        log.warning("model_gateway unavailable (%s) — degrading, no draft.", exc)
        return DraftResult(
            draft_text="",
            kb_sources=kb_sources,
            kb_chunks=kb_chunks,
            confidence="none",
            kb_gap=False,
            category=category,
            model_used="none (llm-unavailable)",
            should_post=False,
            reason=f"LLM unavailable ({type(exc).__name__}): {exc}",
            is_escalation=False,
            priority=priority,
        )

    if not draft_text:
        # Model returned empty — don't post a blank draft. Log raw response
        # for debugging (especially reasoning models that may return content
        # in reasoning_content instead of content).
        raw_choices = (result.get("raw") or {}).get("choices") or []
        raw_msg = raw_choices[0].get("message") if raw_choices else {}
        log.warning(
            "LLM returned empty draft_text (model=%s). raw content=%r, "
            "reasoning_content=%r",
            model_used,
            (raw_msg.get("content") or "")[:200],
            (raw_msg.get("reasoning_content") or "")[:200],
        )
        return DraftResult(
            draft_text="",
            kb_sources=kb_sources,
            kb_chunks=kb_chunks,
            confidence="low",
            kb_gap=False,
            category=category,
            model_used=model_used,
            should_post=False,
            reason="LLM returned an empty draft — needs a human.",
            is_escalation=False,
            priority=priority,
        )

    # --- Verification pass: check facts, grammar, and tone. ------------------ #
    # Replaces the old proofreader with a single verifier that checks BOTH
    # factual accuracy (vs KB context) AND spelling/grammar/tone.
    # If the verifier requests edits, we re-draft ONCE with the feedback,
    # then post regardless (no re-verification — avoids infinite loops).
    # If the verifier is slow or unavailable, the draft passes through (fail open).
    import verifier as _verifier_mod
    verdict = _verifier_mod.verify(
        draft_text=draft_text,
        kb_chunks=all_chunks,
        customer_message=customer_message,
        subject=subject,
        order_context=order_context,
    )

    if not verdict.approved and verdict.reason:
        # Verifier requested edits — re-draft once with the feedback
        log.info("Verifier requested edit: %s — re-drafting once.", verdict.reason[:120])
        re_draft_prompt = _build_user_prompt(
            customer_message, subject, all_chunks, order_context,
            ai_analysis=ai_analysis,
            conversation=messages,
        )
        # Append the verifier's feedback to the prompt
        re_draft_prompt += (
            "\n\n---\n"
            "FEEDBACK FROM QUALITY CHECK (please address this in your reply):\n"
            f"{verdict.reason}\n"
            "Fix the issue above while keeping the same warm, friendly tone. "
            "Do NOT change anything else."
        )
        try:
            result = model_gateway.complete(
                [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": re_draft_prompt},
                ],
                temperature=0.2,
            )
            re_draft_text = (result.get("text") or "").strip()
            if re_draft_text:
                draft_text = re_draft_text
                log.info("Re-draft completed (length %d chars).", len(draft_text))
            else:
                log.info("Re-draft returned empty — keeping original draft.")
        except model_gateway.LLMError as exc:
            log.warning("Re-draft LLM call failed (%s) — keeping original draft.", exc)
        except Exception as exc:
            log.warning("Re-draft unexpected error (%s) — keeping original draft.", exc)

    return DraftResult(
        draft_text=draft_text,
        kb_sources=kb_sources,
        kb_chunks=all_chunks,  # include Hindsight chunks so audit trail is complete
        confidence=_confidence(kb_chunks),
        kb_gap=False,
        category=category,
        model_used=model_used,
        should_post=True,
        reason=(
            f"Drafted from {len(kb_sources)} KB source(s) "
            f"[{', '.join(kb_sources)}]; for human review."
        ),
        is_escalation=False,
        priority=priority,
    )


# --------------------------------------------------------------------------- #
# Self-test — offline, mock provider. Proves the three safety behaviors.
# --------------------------------------------------------------------------- #
def _selftest():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    # Force the deterministic offline mock provider for the test.
    import os
    os.environ["LLM_PROVIDER"] = "mock"
    assert not model_gateway.is_live(), "self-test must run under the mock provider"

    failures = []

    # (a) Benign ticket -> a draft, no gap, kb_sources non-empty, should_post. -
    print("=== (a) benign: 'where is my order?' ===")
    a = generate_draft("Where is my order? Has it shipped yet?")
    print(f"  should_post={a.should_post} kb_gap={a.kb_gap} "
          f"category={a.category} model_used={a.model_used} "
          f"confidence={a.confidence}")
    print(f"  kb_sources={a.kb_sources}")
    print(f"  draft_text (first 160): {a.draft_text[:160]!r}")
    if not a.should_post:
        failures.append("(a) benign ticket should have should_post=True")
    if a.kb_gap:
        failures.append("(a) benign ticket should have kb_gap=False")
    if not a.kb_sources:
        failures.append("(a) benign ticket should have non-empty kb_sources")
    if not a.draft_text.strip():
        failures.append("(a) benign ticket should produce a non-empty draft")
    if a.is_escalation:
        failures.append("(a) benign ticket must not be flagged as an escalation")

    # (b) KB-gap nonsense -> kb_gap=True, no fabricated policy, no auto-send. --
    print("\n=== (b) KB gap: nonsense question ===")
    b = generate_draft("purple platypus quantum tax accordion lawnmower zzxq?")
    print(f"  should_post={b.should_post} kb_gap={b.kb_gap} "
          f"category={b.category} model_used={b.model_used}")
    print(f"  kb_sources={b.kb_sources}")
    print(f"  draft_text (first 200): {b.draft_text[:200]!r}")
    if not b.kb_gap:
        failures.append("(b) nonsense question should have kb_gap=True")
    if b.should_post:
        failures.append("(b) KB-gap result must not be auto-postable (should_post=False)")
    if b.kb_sources:
        failures.append("(b) KB-gap result must have no kb_sources")
    # No fabricated policy: the gap note must not invent windows/prices/refunds.
    blob = b.draft_text.lower()
    for forbidden in ("refund", "% off", "discount", "30 day", "30-day", "$"):
        if forbidden in blob:
            failures.append(f"(b) KB-gap note appears to fabricate policy (found {forbidden!r})")

    # (c) Sensitive ticket WITH its real classification -> no customer draft. --
    print("\n=== (c) sensitive: 'I want a refund' (real classification) ===")
    sensitive_msg = "I want a refund for my order, it's not what I expected."
    real_cls = classifier.classify(sensitive_msg)
    assert not real_cls.auto_draft_allowed and real_cls.sensitive, \
        "precondition: refund must be sensitive + not auto-draftable"
    c = generate_draft(sensitive_msg, classification=real_cls)
    print(f"  should_post={c.should_post} kb_gap={c.kb_gap} "
          f"is_escalation={c.is_escalation} category={c.category} "
          f"model_used={c.model_used}")
    print(f"  draft_text (first 200): {c.draft_text[:200]!r}")
    if c.should_post:
        failures.append("(c) sensitive ticket MUST have should_post=False")
    if not c.is_escalation:
        failures.append("(c) sensitive ticket should be flagged is_escalation=True")
    if c.kb_sources:
        failures.append("(c) sensitive ticket must not have customer-facing kb_sources")
    # Must be a clearly-labeled internal note, NOT a customer reply.
    if "ESCALATE" not in c.draft_text and "DO NOT AUTO-REPLY" not in c.draft_text.upper():
        failures.append("(c) sensitive note must be clearly labeled as escalate/do-not-reply")
    # No customer-ready refund promise.
    cl = c.draft_text.lower()
    if "your refund" in cl or "we will refund" in cl or "i've refunded" in cl:
        failures.append("(c) sensitive note must not contain a customer-facing refund promise")

    # (c2) Safe-by-default: NO classification passed for a sensitive ticket. --
    print("\n=== (c2) safe-by-default: sensitive ticket, no classification passed ===")
    c2 = generate_draft("I'm filing a chargeback with my bank.")
    print(f"  should_post={c2.should_post} is_escalation={c2.is_escalation} "
          f"category={c2.category}")
    if c2.should_post or not c2.is_escalation:
        failures.append("(c2) engine must self-classify and escalate sensitive tickets by default")

    # (d) Resilience: live provider, no key -> LLMConfigError -> graceful. ----
    print("\n=== (d) resilience: live provider w/ no key -> graceful degrade ===")
    os.environ["LLM_PROVIDER"] = "openrouter"  # live provider, no key configured
    os.environ.pop("LLM_API_KEY", None)
    try:
        d = generate_draft("Where is my order? Has it shipped yet?")
        print(f"  should_post={d.should_post} model_used={d.model_used} "
              f"reason={d.reason!r}")
        if d.should_post:
            failures.append("(d) must not post a draft when the LLM is unavailable")
        if "unavailable" not in d.reason.lower():
            failures.append("(d) reason should explain the LLM was unavailable")
    finally:
        os.environ["LLM_PROVIDER"] = "mock"  # restore the offline default

    # ---- Verdict ----------------------------------------------------------- #
    print()
    if failures:
        print("DRAFT_ENGINE SELF-TEST FAILED:")
        for f in failures:
            print("  FAIL " + f)
        return False
    print("All draft-engine safety checks passed:")
    print("  (a) benign -> grounded draft, kb_gap=False, kb_sources set, should_post=True")
    print("  (b) KB gap -> kb_gap=True, should_post=False, NO fabricated policy")
    print("  (c) sensitive (refund) -> should_post=False, internal escalate note only")
    print("  (c2) sensitive w/o classification -> self-classified + escalated (safe by default)")
    print("  (d) LLM unavailable -> graceful degrade, should_post=False (no crash)")
    print("DRAFT_ENGINE SELF-TEST OK")
    return True


if __name__ == "__main__":
    import sys
    sys.exit(0 if _selftest() else 1)
