"""Anthropic (Claude API) brain adapter — feature F3.

Drop-in replacement for ``MockBrain``: same ``draft`` / ``rewrite`` interface, so
``FABLE_BRAIN=anthropic`` swaps it in with no other code change. It builds a
grounded system prompt from the safety rules (CLAUDE.md §2) plus the order/return
context and KB snippets on the ``DraftContext``, calls the Anthropic Messages API
over httpx, and returns the model's reply as a ``DraftResult``.

Safety / grounding baked in
---------------------------
* The model only ever *drafts* — a human reviews and sends. It is told never to
  claim anything was sent.
* It may only state facts present in the provided order context / KB snippets;
  it must never invent prices, sizes, dates or policies.
* On a **sensitive** ticket it must make no promises about money (refunds,
  replacements, credits) — acknowledge and defer to the care team.
* Every model output is run through the shared ``clean_draft`` cleaner; if nothing
  draf-able remains, no draft is returned.
* The customer message is gated by ``should_draft`` — bare "thanks"/empty messages
  produce no draft.

Offline-testable
----------------
The constructor accepts an injected httpx ``client`` **or** ``transport``. Tests
pass an ``httpx.MockTransport`` so no real network call is ever made. In normal
runtime, if no API key is configured the constructor raises ``BrainConfigError``;
the brain factory catches that and falls back to ``MockBrain`` (with a warning) so
the app never crashes.
"""
from __future__ import annotations

import logging
from typing import List, Optional

import httpx

from .. import config
from ..draft_cleaner import clean_draft, should_draft
from .base import DraftContext, DraftResult

log = logging.getLogger("fable.brains.anthropic")

# API defaults (overridable via env / .env.fable, resolved through config.get).
DEFAULT_BASE = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1024
ANTHROPIC_VERSION = "2023-06-01"

SIGNOFF = "— Buttons Bebe Care Team"


class BrainConfigError(RuntimeError):
    """Raised at construction when the adapter cannot be configured (no API key)."""


class AnthropicBrain:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        max_tokens: Optional[int] = None,
        client: Optional[httpx.Client] = None,
        transport: Optional[httpx.BaseTransport] = None,
        timeout: float = 30.0,
    ) -> None:
        # Resolve the API key: explicit arg > FABLE_ANTHROPIC_API_KEY (env/.env).
        self.api_key = api_key if api_key is not None else config.get("FABLE_ANTHROPIC_API_KEY")
        if not self.api_key:
            raise BrainConfigError(
                "AnthropicBrain requires an API key. Set FABLE_ANTHROPIC_API_KEY "
                "(env or fable/.env.fable) or use FABLE_BRAIN=mock for local dev."
            )
        self.model = model or config.get("FABLE_ANTHROPIC_MODEL") or DEFAULT_MODEL
        self.base_url = (base_url or config.get("FABLE_ANTHROPIC_BASE") or DEFAULT_BASE).rstrip("/")
        self.max_tokens = int(max_tokens or config.get("FABLE_ANTHROPIC_MAX_TOKENS") or DEFAULT_MAX_TOKENS)

        # Transport injection: a caller (tests) can pass a ready client or a
        # transport (e.g. httpx.MockTransport) so no real socket is ever opened.
        if client is not None:
            self._client = client
        else:
            # trust_env=False keeps the call off any ambient proxy.
            self._client = httpx.Client(transport=transport, timeout=timeout, trust_env=False)

    # -- public interface ---------------------------------------------------
    def draft(self, ctx: DraftContext) -> DraftResult:
        # Gate on the customer message: nothing to answer -> no draft.
        gate = should_draft(ctx.last_customer_text)
        if not gate.ok:
            return DraftResult(body_text="", notes=f"no_draft: {gate.reason}")

        system = self._system_prompt(ctx)
        user = self._draft_user_content(ctx)
        text = self._call(system, [{"role": "user", "content": user}])
        return self._finalize(text, ctx)

    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult:
        # A rewrite is an explicit human ask on an existing draft — no should_draft gate.
        system = self._system_prompt(ctx)
        user = (
            "Below is the current draft reply to the customer.\n\n"
            f"CURRENT DRAFT:\n{current_draft}\n\n"
            f"Rewrite it to follow this instruction: {instruction}\n"
            "Return only the rewritten customer reply — no preamble, no commentary."
        )
        text = self._call(system, [{"role": "user", "content": user}])
        return self._finalize(text, ctx)

    # -- prompt building ----------------------------------------------------
    def _system_prompt(self, ctx: DraftContext) -> str:
        """Safety rules (CLAUDE.md §2) + grounding rules for the drafter."""
        sensitive = ctx.risk == "sensitive"
        lines = [
            "You are the support-reply drafting assistant for Buttons Bebe, a baby "
            "and children's clothing shop. You help a human agent by writing a "
            "first-pass reply to a customer.",
            "",
            "SAFETY RULES (never violate):",
            "1. You only DRAFT a reply. A human reviews it and decides whether to send. "
            "Never say or imply that a message has already been sent to the customer.",
            "2. Only state facts that appear in the ORDER CONTEXT or KNOWLEDGE BASE "
            "snippets provided below. Never invent or guess prices, shipping rates, "
            "sizes, dates, tracking numbers, or policies. If you do not have a fact, "
            "say you will check or ask the customer for the detail you need.",
            "3. Do not quote a price, rate, or policy figure that is not present in the "
            "knowledge base snippets.",
            "4. Reply in the same language the customer wrote in.",
            "5. Be warm, concise, and professional. Sign off exactly as: "
            f"\"{SIGNOFF}\".",
        ]
        if sensitive:
            reason = f" (reason: {ctx.risk_reason})" if ctx.risk_reason else ""
            lines += [
                "",
                f"THIS TICKET IS FLAGGED SENSITIVE{reason}. Extra rules:",
                "- Make NO promises about money: do not promise a refund, replacement, "
                "store credit, discount, or any specific resolution or amount.",
                "- Acknowledge the concern warmly, apologise for the trouble, and say the "
                "care team is looking into it personally and will follow up. Leave the "
                "decision to the human.",
            ]
        return "\n".join(lines)

    def _draft_user_content(self, ctx: DraftContext) -> str:
        first = _firstname(ctx.customer)
        blocks: list[str] = [
            f"Customer first name: {first}",
            f"Channel: {ctx.channel}",
        ]
        if ctx.subject:
            blocks.append(f"Ticket subject: {ctx.subject}")
        blocks.append(f"Risk level: {ctx.risk}")

        blocks.append("\n--- RECENT CONVERSATION ---")
        blocks.append(_format_messages(ctx.messages, ctx.last_customer_text))

        blocks.append("\n--- ORDER CONTEXT (Shopify, read-only) ---")
        blocks.append(_format_orders(ctx.orders))

        blocks.append("\n--- RETURNS (Redo, read-only) ---")
        blocks.append(_format_returns(ctx.returns))

        blocks.append(
            "\n--- KNOWLEDGE BASE SNIPPETS (the ONLY policy facts you may state) ---")
        blocks.append(_format_kb(ctx.kb_snippets))

        blocks.append(
            "\nTASK: Draft a reply to the customer's latest message, following every "
            "rule above. Return only the reply text."
        )
        return "\n".join(blocks)

    # -- API call + parsing -------------------------------------------------
    def _call(self, system: str, messages: List[dict]) -> str:
        """POST to the Messages API. Returns the text, or "" on any error.

        Never raises: an API failure (429/500/timeout/bad body) degrades to an
        empty string so the pipeline simply produces no draft.
        """
        url = f"{self.base_url}/v1/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": messages,
        }
        try:
            r = self._client.post(url, json=payload, headers=headers)
        except Exception as e:  # connect/read timeout, transport error, etc.
            log.warning("anthropic request failed: %r", e)
            return ""
        if r.status_code != 200:
            body = ""
            try:
                body = r.text[:200]
            except Exception:
                pass
            log.warning("anthropic api %s: %s", r.status_code, body)
            return ""
        try:
            data = r.json()
        except Exception as e:
            log.warning("anthropic bad json: %r", e)
            return ""
        return _extract_text(data)

    def _finalize(self, text: str, ctx: DraftContext) -> DraftResult:
        cleaned = clean_draft(text)
        if cleaned.no_draft:
            return DraftResult(
                body_text="", kb_refs=[],
                notes="no_draft: " + "; ".join(cleaned.reasons or ["empty model output"]),
            )
        kb_refs = [f"kb:{s.get('file')}" for s in (ctx.kb_snippets or []) if s.get("file")]
        return DraftResult(
            body_text=cleaned.text,
            kb_refs=kb_refs,
            notes="; ".join(cleaned.reasons) if cleaned.reasons else "anthropic draft",
        )


# --- helpers (module-level, easy to unit-test) ------------------------------
def _firstname(customer: dict) -> str:
    fn = (customer or {}).get("firstname")
    if fn:
        return str(fn).strip()
    name = (customer or {}).get("name")
    if name:
        return str(name).strip().split()[0]
    return "there"


def _format_messages(messages: List[dict], last_customer_text: str) -> str:
    if not messages:
        return f"Customer: {last_customer_text or '(no message text)'}"
    out = []
    for m in messages[-8:]:  # keep the prompt bounded
        who = "Agent" if m.get("from_agent") else "Customer"
        body = (m.get("body_text") or "").strip()
        if body:
            out.append(f"{who}: {body}")
    return "\n".join(out) if out else f"Customer: {last_customer_text or '(no message text)'}"


def _format_orders(orders: List[dict]) -> str:
    if not orders:
        return "No orders on file for this customer."
    lines = []
    for o in orders[:5]:
        bits = [f"Order {o.get('name') or '(unknown)'}"]
        if o.get("financial_status"):
            bits.append(f"payment={o['financial_status']}")
        if o.get("fulfillment_status"):
            bits.append(f"fulfillment={o['fulfillment_status']}")
        if o.get("tracking_number"):
            bits.append(f"tracking_number={o['tracking_number']}")
        if o.get("tracking_url"):
            bits.append(f"tracking_url={o['tracking_url']}")
        if o.get("total_price"):
            cur = o.get("currency") or ""
            bits.append(f"total={o['total_price']} {cur}".strip())
        items = o.get("line_items") or []
        if items:
            names = ", ".join(
                f"{li.get('quantity', '')}x {li.get('title', '')}".strip() for li in items[:6]
            )
            bits.append(f"items=[{names}]")
        lines.append("- " + "; ".join(bits))
    return "\n".join(lines)


def _format_returns(returns: List[dict]) -> str:
    if not returns:
        return "No returns on file for this customer."
    lines = []
    for rt in returns[:5]:
        bits = [f"Return for {rt.get('order_name') or '(unknown order)'}"]
        if rt.get("status"):
            bits.append(f"status={rt['status']}")
        if rt.get("refund_amount") is not None:
            bits.append(f"refund_amount={rt['refund_amount']}")
        lines.append("- " + "; ".join(bits))
    return "\n".join(lines)


def _format_kb(snippets: List[dict]) -> str:
    if not snippets:
        return "No knowledge base snippets were found for this question."
    lines = []
    for s in snippets:
        heading = s.get("heading") or s.get("title") or ""
        loc = s.get("file") or ""
        head = f"[{loc}" + (f" — {heading}" if heading else "") + "]"
        lines.append(head)
        lines.append(s.get("text") or "")
    return "\n".join(lines)


def _extract_text(data: dict) -> str:
    """Pull the text out of a Messages API response body."""
    parts = []
    for block in (data.get("content") or []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text") or "")
    return "\n".join(p for p in parts if p).strip()
