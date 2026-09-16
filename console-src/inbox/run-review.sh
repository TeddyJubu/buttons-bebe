#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
HOST="${INBOX_HOST:-127.0.0.1}"
PORT="${INBOX_PORT:-8766}"
export SHOPIFY_MUTATIONS_ENABLED=0
export HELPDESK_OUTBOUND_ENABLED=0
export GORGIAS_BRIDGE_ENABLED=0
export PYTHONUNBUFFERED=1
# Default the three store paths to a local directory so the documented local
# entry point works off-VPS (the server's own defaults point at /var/lib/...,
# which yields StoreUnavailable on any machine without the VPS layout).
# Explicit env still wins over these defaults.
mkdir -p "$HERE/local"
export HELPDESK_DB_FILE="${HELPDESK_DB_FILE:-$HERE/local/inbox.sqlite3}"
export INBOX_PROJECTION_PATH="${INBOX_PROJECTION_PATH:-$HERE/local/projection.sqlite3}"
export SHOP_RAIL_PATH="${SHOP_RAIL_PATH:-$HERE/local/shop-rail.sqlite3}"
exec "${INBOX_PYTHON:-python3}" console-src/inbox/review_server.py --host "$HOST" --port "$PORT"
