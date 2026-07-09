"""Brain interface (API contract §2).

A Brain drafts a first-pass reply and can rewrite it to an instruction. The
interface is identical for the mock and the (future) real adapters so they are
drop-in swappable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Protocol, runtime_checkable


@dataclass
class DraftContext:
    ticket_id: int
    subject: str
    channel: str
    customer: dict                       # {id,name,firstname,lastname,email,phone}
    messages: List[dict]                 # [{from_agent,body_text,sender_name,created_at}]
    last_customer_text: str
    orders: List[dict] = field(default_factory=list)     # ShopifyOrderTrimmed
    returns: List[dict] = field(default_factory=list)     # RedoReturnTrimmed
    kb_snippets: List[dict] = field(default_factory=list)
    risk: str = "low"
    risk_reason: Optional[str] = None


@dataclass
class DraftResult:
    body_text: str
    kb_refs: List[str] = field(default_factory=list)
    notes: str = ""


@runtime_checkable
class Brain(Protocol):
    name: str

    def draft(self, ctx: DraftContext) -> DraftResult:
        ...

    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult:
        ...
