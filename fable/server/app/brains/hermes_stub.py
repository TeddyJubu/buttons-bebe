"""Hermes Agent brain adapter — STUB.

TODO(sprint-2): shell out to the Hermes CLI (hermes --yolo -z "...") or its HTTP
bridge, guided by SOUL.md + the buttonsbebe skill, passing DraftContext and
capturing the drafted reply as DraftResult. Same interface as MockBrain so it is a
drop-in swap via FABLE_BRAIN=hermes.
"""
from __future__ import annotations

from .base import DraftContext, DraftResult

_TODO = (
    "HermesBrain is not implemented yet. Set FABLE_BRAIN=mock for local dev, or wire "
    "up the Hermes Agent CLI/bridge here (sprint 2)."
)


class HermesBrain:
    name = "hermes"

    def draft(self, ctx: DraftContext) -> DraftResult:
        raise NotImplementedError(_TODO)

    def rewrite(self, ctx: DraftContext, current_draft: str, instruction: str) -> DraftResult:
        raise NotImplementedError(_TODO)
