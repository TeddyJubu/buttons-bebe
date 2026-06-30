#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# install-kb-service.sh — install/refresh the long-running KB black-box service.
#
# This installs gorgias-kb.service (kb_service.py): the standalone, localhost-only
# semantic KB retrieval service that owns the embeddings model + pgvector, so the
# webhook process stays pure-stdlib and KB retrieval can neither block nor crash
# the webhook (and vice-versa).
#
# IDEMPOTENT: re-running just refreshes the unit file and re-enables; it never
# creates duplicates. Requires root (writes to /etc/systemd/system).
#
#   sudo ./infra/kb-service/install-kb-service.sh             # install + enable + start
#   sudo ./infra/kb-service/install-kb-service.sh --uninstall
#
# After install, verify with:  systemctl status gorgias-kb.service
# Health check (localhost):    curl -s http://127.0.0.1:8899/health
# Logs:                        journalctl -u gorgias-kb.service -f
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="/etc/systemd/system"
SERVICE="gorgias-kb.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "ERROR: must run as root (writes to $UNIT_DIR). Try: sudo $0 $*" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  echo "Disabling and removing the KB service..."
  systemctl disable --now "$SERVICE" 2>/dev/null || true
  systemctl stop "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT_DIR/$SERVICE"
  systemctl daemon-reload
  echo "Uninstalled. (The pgvector DB and kb/ repo are left untouched.)"
  exit 0
fi

echo "Installing $SERVICE into $UNIT_DIR (idempotent: overwrites in place)..."
install -m 0644 "$HERE/$SERVICE" "$UNIT_DIR/$SERVICE"

systemctl daemon-reload
systemctl enable --now "$SERVICE"

echo
echo "Installed. Status:"
systemctl --no-pager status "$SERVICE" || true
echo
echo "Health check  : curl -s http://127.0.0.1:8899/health"
echo "Reload caches : sudo systemctl reload $SERVICE   (POST /reload)"
echo "Logs          : journalctl -u $SERVICE -f"
echo "Uninstall     : sudo $0 --uninstall"
