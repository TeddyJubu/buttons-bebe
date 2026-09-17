"""Shared builder for the fixture catalogs (report 11, action 8).

Only the truly common shape lives here; each catalog keeps its own rows,
isolation labels, and order shapes (sample orders carry per-row extras the
live-holes shape does not).
"""
from __future__ import annotations


def customer(gid: str, name: str, orders: str, spent: str, email: str | None = None,
             created: str = "2026-08-01T12:00:00Z", **extra) -> dict:
    row = {
        "id": gid,
        "displayName": name,
        "defaultEmailAddress": {"emailAddress": email} if email else None,
        "createdAt": created,
        "numberOfOrders": orders,
        "amountSpent": {"amount": spent, "currencyCode": "USD"},
        "tags": ["sample"],
    }
    row.update(extra)
    return row
