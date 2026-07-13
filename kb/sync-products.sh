#!/usr/bin/env bash
# Fetch the latest products from Shopify, rebuild the search index, and reload
# the KB service. Run it manually anytime, or let the timer run it every 3 days.
set -e
cd "$(dirname "$0")"
echo "[$(date -u +%FT%TZ)] product sync starting"
./.venv/bin/python scripts/sync_products.py
systemctl restart buttonsbebe-kb-mcp 2>/dev/null || true
echo "[$(date -u +%FT%TZ)] product sync done"
