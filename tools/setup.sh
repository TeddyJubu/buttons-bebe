#!/usr/bin/env bash
# Create the shared tools environment used by the Redo and Gorgias MCP services.
set -euo pipefail
cd "$(dirname "$0")"

python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip -q
./.venv/bin/python -m pip install -r requirements.txt -q

echo "Tools environment ready: $PWD/.venv"
