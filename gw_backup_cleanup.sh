#!/usr/bin/env bash
# One-shot: on/after 2026-07-09, verify the rewritten gorgias-webhook history is
# intact, then remove the pre-rewrite backup mirror. If anything looks wrong,
# KEEP the backup and log the problem. Self-cleans the systemd units when done.
set -uo pipefail
LOG=/root/gw_backup_cleanup.log
BK=/root/gw_history_backup.git
REPO=/root/gorgias-webhook
ts(){ date -u +"%Y-%m-%dT%H:%M:%SZ"; }
{
  echo "[$(ts)] gw-backup-cleanup running"
  if [ ! -e "$BK" ]; then
    echo "backup $BK already gone — nothing to remove."
  else
    commits=$(git -C "$REPO" rev-list --all --count 2>/dev/null || echo 0)
    csv=$(git -C "$REPO" rev-list --all --objects 2>/dev/null | grep -ic '\.csv$')
    key=$(git -C "$REPO" log --all -S 41f4011433244d8d8d25a9e7f76e4660 --oneline 2>/dev/null | grep -c .)
    imp=$("$REPO"/.venv/bin/python -c "import sys;sys.path.insert(0,'$REPO');import draft_engine,classifier;print('OK')" 2>/dev/null)
    echo "checks: commits=$commits csv_in_history=$csv key_in_history=$key import=${imp:-FAIL}"
    if [ "${commits:-0}" -ge 1 ] && [ "${csv:-1}" -eq 0 ] && [ "${key:-1}" -eq 0 ] && [ "${imp:-}" = "OK" ]; then
      rm -rf "$BK" && echo "VERIFIED OK -> removed $BK (10-day backup retention elapsed)."
    else
      echo "CHECKS FAILED -> KEPT $BK. Investigate gorgias-webhook history before deleting."
    fi
  fi
  # Self-clean the scheduler whether or not we deleted (one-shot is done).
  systemctl disable --now gw-backup-cleanup.timer >/dev/null 2>&1
  rm -f /etc/systemd/system/gw-backup-cleanup.timer /etc/systemd/system/gw-backup-cleanup.service
  systemctl daemon-reload >/dev/null 2>&1
  echo "[$(ts)] gw-backup-cleanup done (units self-removed). See above for result."
} >> "$LOG" 2>&1
