"""Staff may use Inbox send/review routes; admin console APIs stay admin-only."""
from __future__ import annotations

import re

_STAFF_DASHBOARD_TICKET = re.compile(
    r"^/dashboard/api/ticket/[1-9][0-9]{0,17}/(retry-draft|rewrite|actions/[^/]+)$"
)
_STAFF_CONSOLE_TICKET = re.compile(
    r"^/console/api/ticket/[1-9][0-9]{0,17}/(retry-draft|rewrite|actions/[^/]+)$"
)


def staff_may_use_dashboard_path(path: str) -> bool:
    if path.startswith("/dashboard/api/inbox/") or path == "/dashboard/api/inbox":
        return True
    return bool(_STAFF_DASHBOARD_TICKET.fullmatch(path))


def staff_may_use_forwarded_path(path: str) -> bool:
    """Paths as seen by Caddy forward-auth (`/console/...` or `/inbox/...`)."""
    if path.startswith("/inbox/") or path in ("/inbox", "/inbox2") or path.startswith("/inbox2/"):
        return True
    if path.startswith("/console/login"):
        return True
    if path.startswith("/console/api/inbox/") or path == "/console/api/inbox":
        return True
    return bool(_STAFF_CONSOLE_TICKET.fullmatch(path))
