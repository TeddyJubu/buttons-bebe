#!/usr/bin/env bash
# Re-build the search index. Run this after you add or edit any content in vault/.
set -e
cd "$(dirname "$0")"
./.venv/bin/python scripts/index_kb.py
