"""Anthropic (Claude API) brain adapter — STUB.

TODO(sprint-2): implement using the Anthropic Messages API. Build a system prompt
from SOUL.md, pass DraftContext (subject, customer, orders, returns, kb_snippets,
risk) as structured context, and return the model's reply as DraftResult. Same
interface as MockBrain so it is a drop-in swap via FABLE_BRAIN=anthropic.
"""
from __future__ import annotations

from .base import DraftContext, DraftResult

_TODO = (
    "AnthropicBrain is not implemented yet. Set FABLE_BRAIN=mock for local dev, or "
    "implement the Anthropic Messages API call here (sprint 2)."
)


class AnthropicBrain:
    name = "anthropic"

    def draft(self, ctx: DraftContext) -> DraftResult:
        raise NotImplementedError(_TODO)

    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult:
        raise NotImplementedError(_TODO)
