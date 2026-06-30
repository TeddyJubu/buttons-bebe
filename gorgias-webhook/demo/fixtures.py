#!/usr/bin/env python3
"""
fixtures.py — Preset demo scenarios using qa_v3 MOCK_ORDERS.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

SCENARIOS: Dict[str, dict] = {
    "tracking": {
        "label": "Tracking inquiry",
        "email": "shipped@example.com",
        "subject": "Has my order shipped yet?",
        "message": (
            "Can you tell me if my order has shipped, and if so the tracking number?"
        ),
    },
    "cancel": {
        "label": "Cancel order",
        "email": "cancelme@example.com",
        "subject": "Please cancel my order",
        "message": (
            "I just placed an order this morning but changed my mind — "
            "can you cancel it before it ships?"
        ),
    },
    "no_order": {
        "label": "No linked order (KB-only)",
        "email": "customer@example.com",
        "subject": "Sizing question",
        "message": (
            "Do your clothes run small? Trying to decide whether to size up "
            "for a 9-month-old."
        ),
    },
    "urgent": {
        "label": "Urgent — safety concern",
        "email": "shipped@example.com",
        "subject": "URGENT — product safety issue",
        "message": (
            "My baby was injured by a broken snap on the onesie from my recent order. "
            "This is extremely urgent — I need help immediately and may need to "
            "report this to consumer safety authorities!"
        ),
    },
    "kb_gap": {
        "label": "KB gap — obscure product question",
        "email": "customer@example.com",
        "subject": "Organic bamboo swaddles with gold trim?",
        "message": (
            "Do you carry organic bamboo swaddles with gold trim in newborn size? "
            "I couldn't find them on the website and need to know if you can "
            "special-order them before my baby shower next week."
        ),
    },
}


def list_scenarios() -> Dict[str, str]:
    return {name: spec["label"] for name, spec in SCENARIOS.items()}


def get_scenario(name: str) -> Optional[dict]:
    return SCENARIOS.get(name)


def load_scenario_into_store(store, name: str) -> Optional[dict]:
    """Create a ticket from a named scenario. Returns ticket dict or None."""
    spec = get_scenario(name)
    if not spec:
        return None
    return store.create_ticket(
        email=spec["email"],
        subject=spec["subject"],
        initial_message=spec["message"],
    )
