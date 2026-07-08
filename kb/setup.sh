#!/usr/bin/env bash
# One-time setup. Builds an isolated Python environment and installs the search
# engine, then builds the first index. Safe to re-run any time.
set -e
cd "$(dirname "$0")"

echo "==> Creating an isolated Python environment (.venv) ..."
python3 -m venv .venv

echo "==> Installing the search engine (can take a few minutes the first time) ..."
./.venv/bin/pip install --upgrade pip -q
./.venv/bin/pip install -r requirements.txt -q

echo "==> Building the first index (also downloads the small language model once) ..."
./.venv/bin/python scripts/index_kb.py

echo ""
echo "==> Setup complete. Try a search:"
echo '     ./search.sh "where is my order"'
