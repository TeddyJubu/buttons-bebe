"""Inbox HTTP door (actor=human). Same invoke() as MCP and CLI."""

from __future__ import annotations

from typing import Any

from .dispatch import invoke
from .names import TOOL_NAMES


def handle_http(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    actor: str = "human",
) -> dict[str, Any]:
    return invoke(str(tool or ""), arguments or {}, actor=actor)


def allowed_tools() -> tuple[str, ...]:
    return TOOL_NAMES
