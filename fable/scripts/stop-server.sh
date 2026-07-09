#!/usr/bin/env bash
# Stop the Fable help desk server.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$FABLE_DIR/logs"
PORT="${FABLE_PORT:-9600}"

if [ -f "$LOG_DIR/server.pid" ]; then
    PID="$(cat "$LOG_DIR/server.pid")"
    if kill "$PID" 2>/dev/null; then
        echo "Stopped Fable server (PID $PID)."
    fi
    rm -f "$LOG_DIR/server.pid"
fi

# Belt-and-suspenders: kill anything still bound to the port's uvicorn.
pkill -f "uvicorn main:app.*--port $PORT" 2>/dev/null || true
echo "Done."
