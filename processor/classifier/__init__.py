"""Public classifier facade with a script-safe labelled self-test."""

from __future__ import annotations

import sys

if __package__ in (None, ""):
    from pathlib import Path
    _processor = str(Path(__file__).resolve().parent.parent)
    if _processor not in sys.path:
        sys.path.insert(0, _processor)
    __package__ = "classifier"

from . import compat as _compat, data, engine, matching, patterns, selftest, views
from .engine import (Any, HIGH, IMMEDIATE, NORMAL, Priority, classify,
                     get_logger, get_settings, logger, log_event, re)

_MODULES = (engine, data, matching, patterns, views, selftest)


def __getattr__(name: str):
    for module in _MODULES:
        if name in vars(module):
            return getattr(module, name)
    raise AttributeError(name)


_compat.install(sys.modules[__name__], _MODULES)
__all__ = ["classify", "IMMEDIATE", "HIGH", "NORMAL", "get_settings",
           "logger", "log_event"]

if __name__ == "__main__":
    _ok, _ = selftest._selftest()
    raise SystemExit(0 if _ok else 1)
