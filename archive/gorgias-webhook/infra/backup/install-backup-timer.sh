#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-backup-timer.sh — install/refresh the daily backup systemd timer.
#
# IDEMPOTENT: re-running just refreshes the unit files and re-enables; it never
# creates duplicates. Requires root (writes to /etc/systemd/system).
#
#   sudo ./infra/backup/install-backup-timer.sh           # install + enable + start
#   sudo ./infra/backup/install-backup-timer.sh --uninstall
#
# After install, verify with:  systemctl list-timers gorgias-backup.timer
# Manually trigger a run with: sudo systemctl start gorgias-backup.service
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"
SERVICE="gorgias-backup.service"
TIMER="gorgias-backup.timer"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (writes to $UNIT_DIR). Try: sudo $0 $*" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  echo "Disabling and removing the backup timer..."
  systemctl disable --now "$TIMER" 2>/dev/null || true
  systemctl stop "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT_DIR/$TIMER" "$UNIT_DIR/$SERVICE"
  systemctl daemon-reload
  echo "Uninstalled. (Existing backups under backups/ are left untouched.)"
  exit 0
fi

echo "Installing units into $UNIT_DIR (idempotent: overwrites in place)..."
install -m 0644 "$HERE/$SERVICE" "$UNIT_DIR/$SERVICE"
install -m 0644 "$HERE/$TIMER"   "$UNIT_DIR/$TIMER"

systemctl daemon-reload
systemctl enable --now "$TIMER"

echo
echo "Installed. Schedule:"
systemctl list-timers "$TIMER" --no-pager || true
echo
echo "Manual run now : sudo systemctl start $SERVICE"
echo "Logs           : journalctl -u $SERVICE -n 50 --no-pager"
echo "Uninstall      : sudo $0 --uninstall"
