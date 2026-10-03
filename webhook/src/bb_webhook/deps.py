"""Configured database path shared by the console action routes."""

from pathlib import Path

from .config import get_settings


def get_db() -> Path:
    return get_settings().db_path_absolute
