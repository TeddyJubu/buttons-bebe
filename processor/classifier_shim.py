"""Compatibility shim; the package directory is the canonical classifier."""

import classifier as _classifier
from classifier import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_classifier, name)


if __name__ == "__main__":
    _ok, _ = _classifier._selftest()
    raise SystemExit(0 if _ok else 1)
