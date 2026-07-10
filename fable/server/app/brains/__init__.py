"""Pluggable brain adapters. Selected by FABLE_BRAIN (mock|anthropic|hermes)."""
from __future__ import annotations

import logging

from .. import config
from .base import Brain, DraftContext, DraftResult
from .mock import MockBrain

log = logging.getLogger("fable.brains")


def get_brain(name: str | None = None) -> Brain:
    name = (name or config.BRAIN or "mock").lower()
    if name == "mock":
        return MockBrain()
    if name == "anthropic":
        # Real Claude adapter. If it can't be configured (e.g. no API key set),
        # fall back to MockBrain with a warning so the app never crashes.
        from .anthropic import AnthropicBrain, BrainConfigError
        try:
            return AnthropicBrain()
        except BrainConfigError as e:
            log.warning("anthropic brain unavailable (%s); falling back to mock", e)
            return MockBrain()
    if name == "hermes":
        from .hermes_stub import HermesBrain
        return HermesBrain()
    # Unknown → safe default.
    return MockBrain()


__all__ = ["Brain", "DraftContext", "DraftResult", "MockBrain", "get_brain"]
