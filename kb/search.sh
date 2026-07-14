#!/usr/bin/env bash
# Ask the knowledge base a question (for testing).
# Example:   ./search.sh "how long does shipping take"
set -e
cd "$(dirname "$0")"
./.venv/bin/python scripts/search_kb.py "$@"
