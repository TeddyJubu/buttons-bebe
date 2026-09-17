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

# Since the 2026-07-08 consolidation both the processor and the webhook read
# the SAME file at the project root. processor/config.py hard-codes that path
# and never looks at webhook/.env, so there is no useful fallback: starting
# anyway would boot the processor with an empty GORGIAS_API_KEY, which fails
# per-ticket rather than at startup. Fail loudly.
ENV_FILE="../.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "ERROR: no .env at $(cd .. && pwd)/.env"
    if [ -f "../webhook/.env" ]; then
        echo "       Found the legacy webhook/.env. processor/config.py does NOT read"
        echo "       it — this VPS has not been migrated. Follow"
        echo "       deploy/ENV-CONSOLIDATION-RUNBOOK.md before starting the processor."
    fi
    exit 1
fi

echo "Starting job processor..."
echo "  Config file:   $ENV_FILE"
echo "  Poll interval: $(grep -E '^PROCESSOR_POLL_INTERVAL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 || echo '2.0s (default)')"

exec uv run python -m orchestrator