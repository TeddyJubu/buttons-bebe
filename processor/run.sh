#!/usr/bin/env bash
# run.sh — start the job processor
# Usage: ./run.sh
set -euo pipefail
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

# Activate venv if exists, otherwise create it
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    uv venv
    echo "Installing dependencies..."
    uv pip install -e .
fi

# Ensure webhook .env exists (shared credentials)
if [ ! -f "../webhook/.env" ]; then
    echo "ERROR: webhook/.env not found. Credentials are shared with the webhook receiver."
    exit 1
fi

echo "Starting job processor..."
echo "  Poll interval: $(grep -E '^PROCESSOR_POLL_INTERVAL=' ../webhook/.env 2>/dev/null | cut -d= -f2 || echo '2.0s (default)')"

exec uv run python -m orchestrator