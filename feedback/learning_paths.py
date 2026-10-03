"""Path-only settings for the approved lesson learning flow."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

FEEDBACK_KB_ROOT_ENV = "FEEDBACK_KB_ROOT"


@dataclass(frozen=True)
class LearningPaths:
    """One KB root keeps lesson, exemplar, archive, and ledger paths aligned."""

    kb_root: Path

    @property
    def learned_dir(self) -> Path:
        return self.kb_root / "learned"

    @property
    def tickets_dir(self) -> Path:
        return self.kb_root / "tickets"

    @property
    def archive_dir(self) -> Path:
        return self.kb_root / "_archive_learned"

    @property
    def ledger_path(self) -> Path:
        return self.learned_dir / "_ledger.json"


def default_kb_root(repo_root: Path, *, uppercase_only: bool) -> Path:
    """Choose KB/ for the deployed tree, or kb/ for a local checkout."""
    return Path(repo_root) / ("KB" if uppercase_only else "kb")


def _nonempty(value: str | os.PathLike[str] | None) -> str | None:
    if value is None:
        return None
    text = os.fspath(value).strip()
    return text or None


def _root_path(value: str | os.PathLike[str], repo_root: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = repo_root / path
    return Path(os.path.normpath(os.fspath(path)))


def resolve_learning_paths(
    explicit_root: str | os.PathLike[str] | None,
    *,
    environ: Mapping[str, str],
    repo_root: Path,
    default_root: Path,
    corpus_root: Path | None = None,
) -> LearningPaths:
    """Resolve only the KB path without loading files or reading global state.

    A non-empty explicit path wins, then ``FEEDBACK_KB_ROOT``, then the caller's
    local or deployed default. Relative paths are anchored at ``repo_root``.
    Empty values are unset. When ``corpus_root`` is supplied, a different path
    is rejected so learning cannot write somewhere the active indexer skips.
    """
    requested = _nonempty(explicit_root) or _nonempty(environ.get(FEEDBACK_KB_ROOT_ENV))
    root = _root_path(requested, repo_root) if requested else _root_path(default_root, repo_root)

    if corpus_root is not None and root != _root_path(corpus_root, repo_root):
        raise ValueError("FEEDBACK_KB_ROOT must match the active KB corpus root")
    return LearningPaths(root)
