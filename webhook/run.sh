#!/usr/bin/env bash
# run.sh — start the webhook receiver (FastAPI + uvicorn)
# Usage:  ./run.sh [--reload]
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

# Ensure .env exists
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and fill in credentials."
    exit 1
fi

# Ensure DB directory exists
mkdir -p data

# Read host/port from .env
HOST=$(grep -E '^WEBHOOK_HOST=' .env | cut -d= -f2 || echo "127.0.0.1")
PORT=$(grep -E '^WEBHOOK_PORT=' .env | cut -d= -f2 || echo "8000")

echo "Starting webhook receiver on ${HOST}:${PORT}"
echo "  Health:     http://${HOST}:${PORT}/health"
echo "  Webhook:    https://srv1766050.hstgr.cloud/webhook/gorgias/buttonsbebe"
echo ""

if [ "${1:-}" = "--reload" ]; then
    uv run uvicorn bb_webhook.app:app --host "$HOST" --port "$PORT" --reload
else
    uv run uvicorn bb_webhook.app:app --host "$HOST" --port "$PORT"
fi