#!/usr/bin/env bash
# Start the Fable help desk server (uvicorn) in the background via nohup.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SERVER_DIR="$FABLE_DIR/server"
LOG_DIR="$FABLE_DIR/logs"
mkdir -p "$LOG_DIR" "$SERVER_DIR/data"

HOST="${FABLE_HOST:-127.0.0.1}"
PORT="${FABLE_PORT:-9600}"

cd "$SERVER_DIR"
echo "Starting Fable server on $HOST:$PORT ..."
nohup python3 -m uvicorn main:app --host "$HOST" --port "$PORT" \
    > "$LOG_DIR/server.log" 2>&1 &
echo $! > "$LOG_DIR/server.pid"
echo "PID $(cat "$LOG_DIR/server.pid") — logs: $LOG_DIR/server.log"

# Wait for health.
for i in $(seq 1 30); do
    if curl -sf "http://$HOST:$PORT/fable/api/health" >/dev/null 2>&1; then
        echo "Server healthy."
        exit 0
    fi
    sleep 0.5
done
echo "WARNING: server did not report healthy in time; check $LOG_DIR/server.log" >&2
exit 1
