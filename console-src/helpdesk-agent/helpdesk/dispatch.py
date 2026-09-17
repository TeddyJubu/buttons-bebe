"""Single dispatch used by MCP and CLI."""

from __future__ import annotations

from typing import Any
import os

from .env import mutations_enabled
from . import tickets
from .errors import REFUSED_WRITES, HelpdeskError, bad_request, forbidden_write
from .names import READ_TOOLS, TOOL_NAMES, TOOL_SEND_REPLY
from .tissues import HANDLERS

TOOLS = TOOL_NAMES

WRITE_TOOLS = frozenset(f"helpdesk.{name}" for name in REFUSED_WRITES)
HUMAN_ONLY_TOOLS = frozenset({TOOL_SEND_REPLY})


def list_tools() -> list[str]:
    return list(TOOL_NAMES)


def human_only(*, tool: str) -> HelpdeskError:
    return HelpdeskError(
        "human_only",
        "This action is human-only. Use the inbox Send button with confirmation.",
        details={"tool": tool, "actor": "agent"},
    )


def dispatch(
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    actor: str = "agent",
) -> dict[str, Any]:
    arguments = dict(args or {})
    if tool in WRITE_TOOLS:
        raise forbidden_write(tool=tool, mutations_enabled=mutations_enabled())
    if tool in HUMAN_ONLY_TOOLS and actor != "human":
        raise human_only(tool=tool)
    if os.environ.get("HELPDESK_PRODUCTION") == "1":
        if tool in {"helpdesk.pull_mailbox", "helpdesk.draft_reply", "helpdesk.summarize_thread", "helpdesk.search_macros", "helpdesk.apply_macro"}:
            raise HelpdeskError("integration_inactive", "This inbox connection is not active yet.")
    handler = HANDLERS.get(tool)
    if handler is None:
        raise bad_request("unknown tool", tool=tool)
    result = handler(arguments)
    return {"ok": True, "tool": tool, **result}


def invoke(
    tool: str,
    args: dict[str, Any] | None = None,
    *,
    actor: str = "agent",
) -> dict[str, Any]:
    try:
        with tickets.transaction(write=tool not in READ_TOOLS):
            return dispatch(tool, args, actor=actor)
    except HelpdeskError as exc:
        return exc.as_json()
