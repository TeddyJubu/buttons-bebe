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
PY="${INBOX_PYTHON:-python3}"
# Default the three store paths to a local directory so the documented local
# entry point works off-VPS (the server's own defaults point at /var/lib/...,
# which yields StoreUnavailable on any machine without the VPS layout).
# Explicit env still wins over these defaults. mkdir only when a default is
# actually needed, so read-only checkouts with explicit paths still boot.
if [ -z "${HELPDESK_DB_FILE:-}" ] || [ -z "${INBOX_PROJECTION_PATH:-}" ] || [ -z "${SHOP_RAIL_PATH:-}" ]; then
  mkdir -p "$HERE/local"
fi
HELPDESK_DB_FILE="${HELPDESK_DB_FILE:-$HERE/local/inbox.sqlite3}"
INBOX_PROJECTION_PATH="${INBOX_PROJECTION_PATH:-$HERE/local/projection.sqlite3}"
SHOP_RAIL_PATH="${SHOP_RAIL_PATH:-$HERE/local/shop-rail.sqlite3}"
# A fresh checkout has no projection snapshot, and the inbox 503s every
# ticket read while one is missing (/ready demands a fresh projection).
# Bootstrap an empty one from an empty source DB — an observed history of
# zero tickets is the honest local state, not a broken projection.
if [ ! -f "$INBOX_PROJECTION_PATH" ]; then
  mkdir -p "$(dirname "$INBOX_PROJECTION_PATH")"
  "$PY" - "$INBOX_PROJECTION_PATH" <<'PY' || echo "projection bootstrap failed — inbox reports projection_unavailable until one is exported" >&2
import sys
sys.path.insert(0, "console-src/inbox")
from export_projection import export
import sqlite3, tempfile, os
fd, path = tempfile.mkstemp(suffix=".sqlite3"); os.close(fd)
with sqlite3.connect(path) as db:
    db.execute("CREATE TABLE parsed_messages(ticket_id INTEGER,message_id TEXT,author_type TEXT,author_email TEXT,customer_email TEXT,ticket_subject TEXT,channel TEXT,ticket_status TEXT,ticket_assignee TEXT,ticket_tags TEXT,ticket_priority TEXT,ticket_spam INTEGER,ticket_trashed INTEGER,ticket_snoozed INTEGER,created_at TEXT,received_at TEXT,is_customer_message INTEGER,message_text TEXT)")
    db.execute("CREATE TABLE webhook_events(ticket_id INTEGER,message_id TEXT,raw_payload TEXT)")
    db.execute("CREATE TABLE ticket_results(ticket_id INTEGER,message_id TEXT,draft_text TEXT,priority TEXT,action TEXT,reason TEXT,processed_at TEXT)")
export(path, sys.argv[1], group=None)
os.unlink(path)
PY
fi
export HELPDESK_DB_FILE INBOX_PROJECTION_PATH SHOP_RAIL_PATH
exec "$PY" console-src/inbox/review_server.py --host "$HOST" --port "$PORT"
