#!/usr/bin/env python3
"""
dotenv_loader.py — Loads /root/.env for the gorgias-webhook project.

Thin wrapper around env_loader.py in this package.
Call load() once at the top of every entry point before any config is read.
"""

import env_loader

ROOT_DOTENV = env_loader.ROOT_DOTENV


def load(path=None):
    """Load /root/.env into os.environ. Silent no-op if the file is missing."""
    env_loader.load(path)


def apply_aliases():
    """Apply cross-system env var aliases (gorgias-webhook ↔ teddy)."""
    env_loader.apply_aliases()
