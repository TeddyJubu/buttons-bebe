"""Pluggable brain adapters. Selected by FABLE_BRAIN (mock|anthropic|hermes)."""
from __future__ import annotations

from .. import config
from .base import Brain, DraftContext, DraftResult
from .mock import MockBrain


def get_brain(name: str | None = None) -> Brain:
    name = (name or config.BRAIN or "mock").lower()
    if name == "mock":
        return MockBrain()
    if name == "anthropic":
        from .anthropic_stub import AnthropicBrain
        return AnthropicBrain()
    if name == "hermes":
        from .hermes_stub import HermesBrain
        return HermesBrain()
    # Unknown → safe default.
    return MockBrain()


__all__ = ["Brain", "DraftContext", "DraftResult", "MockBrain", "get_brain"]
