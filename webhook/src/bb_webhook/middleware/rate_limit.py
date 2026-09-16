"""Bounded sliding-window rate limiting for authenticated webhooks."""

from __future__ import annotations

import time
from collections import deque

_MAX_REQUESTS_PER_MINUTE = 60
_WINDOW_SECONDS = 60.0
_rate_window: deque[tuple[float, str]] = deque()


def _check_rate_limit(client_ip: str, max_requests: int | None = None) -> bool:
    """Return whether *client_ip* remains within the one-minute window."""
    request_limit = _MAX_REQUESTS_PER_MINUTE if max_requests is None else max_requests
    if request_limit < 1:
        return False
    now = time.monotonic()
    cutoff = now - _WINDOW_SECONDS
    while _rate_window and _rate_window[0][0] < cutoff:
        _rate_window.popleft()
    count = sum(1 for _timestamp, ip in _rate_window if ip == client_ip)
    if count >= request_limit:
        return False
    _rate_window.append((now, client_ip))
    return True
