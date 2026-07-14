#!/usr/bin/env bash
# Nightly learning: promote human-approved console replies into indexed
# exemplars (PII-masked), then rebuild the search index.
set -e
cd "$(dirname "$0")"
./.venv/bin/python scripts/auto_promote_learned.py
./.venv/bin/python scripts/index_kb.py
