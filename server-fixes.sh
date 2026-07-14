#!/usr/bin/env bash
# ============================================================================
# server-fixes.sh — run this ON THE VPS (srv1766050) as root, from anywhere.
#
# Fixes the server-only issues from INCONSISTENCIES.md that can't be changed
# from the local project folder. It BACKS UP every file before touching it and
# prints what it changed, so it is safe to run once and review.
#
#   scp this file to the server, then:   bash server-fixes.sh
# ============================================================================
set -euo pipefail
AGENT="/root/Buttonsbebe Agent"
stamp="$(date -u +%Y%m%d-%H%M%S)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
# H1 — wrong Shopify store name in the webhook env (missing hyphen).
#   The Gorgias subdomain is 'buttonsbebe' (no hyphen) — correct.
#   The Shopify store is 'buttons-bebe.myshopify.com' (WITH a hyphen).
# ---------------------------------------------------------------------------
say "H1: checking SHOPIFY_SHOP in webhook/.env"
WENV="$AGENT/webhook/.env"
if [[ -f "$WENV" ]]; then
  cp "$WENV" "$WENV.bak-$stamp"
  before="$(grep -E '^SHOPIFY_SHOP=' "$WENV" || echo '(not set)')"
  # only rewrite if it is the wrong hyphen-less value
  sed -i -E 's|^SHOPIFY_SHOP=buttonsbebe(\.myshopify\.com)?[[:space:]]*$|SHOPIFY_SHOP=buttons-bebe.myshopify.com|' "$WENV"
  after="$(grep -E '^SHOPIFY_SHOP=' "$WENV" || echo '(not set)')"
  echo "  before: $before"
  echo "  after:  $after"
else
  echo "  $WENV not found — skipping."
fi

# ---------------------------------------------------------------------------
# H4 — 'coroutine was never awaited' warning in database.py (PRAGMA line).
#   The aiosqlite call must be awaited.
# ---------------------------------------------------------------------------
say "H4: checking for an un-awaited PRAGMA busy_timeout"
DB="$AGENT/webhook/src/bb_webhook/database.py"
if [[ -f "$DB" ]]; then
  if grep -nE '^[[:space:]]*conn\.execute\([[:space:]]*["'\'']PRAGMA busy_timeout' "$DB" >/dev/null; then
    cp "$DB" "$DB.bak-$stamp"
    sed -i -E 's|^([[:space:]]*)conn\.execute\(([[:space:]]*["'\'']PRAGMA busy_timeout)|\1await conn.execute(\2|' "$DB"
    echo "  patched: added 'await' to the PRAGMA busy_timeout call."
    grep -nE 'PRAGMA busy_timeout' "$DB" | sed 's/^/  now: /'
  else
    echo "  no un-awaited PRAGMA found (already fixed, or written differently) — skipping."
  fi
else
  echo "  $DB not found — skipping."
fi

# ---------------------------------------------------------------------------
# H3 — Redo access. Per the current CLAUDE.md the processor reaches Redo
#   THROUGH the buttonsbebe_redo MCP tool (which reads the MAIN .env), so it
#   should NOT need REDO_* in webhook/.env. This block only REPORTS, so you can
#   confirm the design rather than duplicating secrets.
# ---------------------------------------------------------------------------
say "H3: reporting Redo credential locations (no changes made)"
grep -lE '^REDO_(API_KEY|STORE_ID)=' "$AGENT/.env" 2>/dev/null | sed 's/^/  present in: /' || echo "  MAIN .env: REDO_* not found"
grep -lE '^REDO_(API_KEY|STORE_ID)=' "$WENV" 2>/dev/null | sed 's/^/  present in: /' || echo "  webhook/.env: REDO_* not found (expected — Redo comes via the MCP tool)"

# ---------------------------------------------------------------------------
say "Restarting affected services"
systemctl restart buttonsbebe-processor 2>/dev/null && echo "  restarted buttonsbebe-processor" || echo "  (could not restart processor — do it manually)"

say "Done. Backups saved next to each changed file as *.bak-$stamp"
echo "Verify:  journalctl -u buttonsbebe-processor -n 40 --no-pager"
