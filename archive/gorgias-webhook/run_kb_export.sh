#!/usr/bin/env python3
"""
run_kb_export.sh — Full pipeline: Gorgias CSV export → Google Sheets sync loop.

Step 1: Export tickets + messages from Gorgias (resumable).
Step 2: Sync CSV rows into Google Sheets in batches (resumable loop).

Usage:
  ./run_kb_export.sh                    # export 12 months + sync
  ./run_kb_export.sh --export-only      # CSV only
  ./run_kb_export.sh --sync-only FILE   # sync existing CSV
  ./run_kb_export.sh --max-tickets 20   # smoke test
"""

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Prefer project venv (has google-api-python-client for Sheets sync)
if [[ -x "$DIR/.venv/bin/python3" ]]; then
  PYTHON="$DIR/.venv/bin/python3"
else
  PYTHON="python3"
fi

GSETUP="$PYTHON ${HERMES_HOME:-$HOME/.hermes}/skills/productivity/google-workspace/scripts/setup.py"

MONTHS=12
LABEL="${MONTHS}mo_$(date -u +%Y-%m-%d)"
EXPORT_ONLY=0
SYNC_ONLY=0
MAX_TICKETS=""
SYNC_CSV=""
LOOP_SYNC=1
INTERVAL=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    --export-only) EXPORT_ONLY=1; shift ;;
    --sync-only) SYNC_ONLY=1; SYNC_CSV="${2:?--sync-only requires CSV path}"; shift 2 ;;
    --months) MONTHS="$2"; LABEL="${MONTHS}mo_$(date -u +%Y-%m-%d)"; shift 2 ;;
    --max-tickets) MAX_TICKETS="--max-tickets $2"; shift 2 ;;
    --no-loop) LOOP_SYNC=0; shift ;;
    --interval) INTERVAL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

MESSAGES_CSV="exports/messages_${LABEL}.csv"

if [[ "$SYNC_ONLY" -eq 0 ]]; then
  echo "==> Exporting Gorgias tickets (last ${MONTHS} months) → ${MESSAGES_CSV}"
  $PYTHON export_tickets.py --months "$MONTHS" --label "$LABEL" --resume $MAX_TICKETS
fi

if [[ "$EXPORT_ONLY" -eq 1 ]]; then
  echo "Export complete. CSV at: $MESSAGES_CSV"
  exit 0
fi

CSV_PATH="${SYNC_CSV:-$MESSAGES_CSV}"
SYNC_ARGS=(--csv "$CSV_PATH" --tab Messages --create-title "Buttons Bebe Support KB — ${LABEL}")

if [[ "$LOOP_SYNC" -eq 1 ]]; then
  SYNC_ARGS+=(--loop --interval "$INTERVAL")
fi

echo "==> Syncing to Google Sheets: $CSV_PATH"
$PYTHON sync_to_sheets.py "${SYNC_ARGS[@]}"

echo "Done."
