#!/usr/bin/env bash
# check_export_status.sh — Check 12-month export; package CSVs when complete.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LABEL="12mo_2026-06-26"
STATE="$DIR/exports/.state/export_${LABEL}.json"
LOG="$DIR/exports/export_${LABEL}.log"
DONE_FLAG="$DIR/exports/.EXPORT_COMPLETE"
ZIP="$DIR/exports/buttons-bebe-kb-${LABEL}.zip"

export_pid() {
  pgrep -f "export_tickets.py --months 12 --label ${LABEL}" 2>/dev/null | head -1 || true
}

read_state() {
  if [[ -f "$STATE" ]]; then
    python3 -c "import json; d=json.load(open('$STATE')); print(d.get('status','?'), d.get('tickets_exported',0), d.get('messages_exported',0))"
  else
    echo "missing 0 0"
  fi
}

package_exports() {
  local messages="$DIR/exports/messages_${LABEL}.csv"
  local tickets="$DIR/exports/tickets_${LABEL}.csv"
  if [[ ! -f "$messages" || ! -f "$tickets" ]]; then
    echo "ERROR: CSV files missing"
    return 1
  fi
  rm -f "$ZIP"
  zip -j "$ZIP" "$messages" "$tickets" >/dev/null
  # Symlink into workspace root for easy Cursor download
  ln -sf "$ZIP" "$DIR/../buttons-bebe-kb-export.zip"
  ln -sf "$messages" "$DIR/../buttons-bebe-messages.csv"
  ln -sf "$tickets" "$DIR/../buttons-bebe-tickets.csv"
  touch "$DONE_FLAG"
  echo "PACKAGED|$ZIP|$(du -h "$ZIP" | cut -f1)"
}

# --- main ---
pid="$(export_pid)"
read -r status tickets messages <<< "$(read_state)"

if [[ -f "$DONE_FLAG" ]]; then
  echo "ALREADY_DONE|tickets=$tickets|messages=$messages|zip=$ZIP"
  exit 0
fi

if [[ "$status" == "complete" ]] && [[ -z "$pid" ]]; then
  package_exports
  echo "COMPLETE|tickets=$tickets|messages=$messages|zip=$ZIP"
  exit 0
fi

if [[ -n "$pid" ]]; then
  echo "RUNNING|pid=$pid|tickets=$tickets|messages=$messages"
  exit 0
fi

# Process gone but state not complete — may have crashed
if [[ "$status" == "in_progress" ]]; then
  echo "STALLED|tickets=$tickets|messages=$messages|hint=run: cd $DIR && python3 export_tickets.py --label $LABEL --resume"
  exit 1
fi

echo "UNKNOWN|status=$status|tickets=$tickets|messages=$messages"
exit 1
