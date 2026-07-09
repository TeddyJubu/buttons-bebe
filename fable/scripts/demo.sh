#!/usr/bin/env bash
# Fable demo — boots the whole stack and plays the API-CONTRACT §7 scenario with
# real HTTP calls and friendly narration, then LEAVES THE STACK RUNNING so you can
# open the console in a browser.
#
#   ./fable/scripts/demo.sh
#
# Stop it afterwards with:
#   ./fable/scripts/stop-server.sh && ./fable/emulators/stop-emulators.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FABLE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export FABLE_DB="${FABLE_DB:-/tmp/fable-demo.db}"
export FABLE_HOST="${FABLE_HOST:-127.0.0.1}"
export FABLE_PORT="${FABLE_PORT:-9600}"
BASE="http://$FABLE_HOST:$FABLE_PORT"
MAILBOX="http://127.0.0.1:9603"

# fresh DB so the demo always starts clean
rm -f "$FABLE_DB" "$FABLE_DB"-* 2>/dev/null || true

PY="${PYTHON:-python3}"
# curl helper (localhost is reached directly via no_proxy)
c() { curl -s -m 6 "$@"; }

echo
echo "════════════════════════════════════════════════════════════"
echo "  Fable demo — Buttons Bebe AI help desk (local, no network)"
echo "════════════════════════════════════════════════════════════"
echo

echo "▶ Booting the stack (Fable + Shopify/Redo/Mailbox emulators)…"
bash "$SCRIPT_DIR/run-all.sh" >/tmp/fable-demo-boot.log 2>&1
if c "$BASE/fable/api/health" >/dev/null 2>&1; then
    echo "  ✅ all services healthy"
else
    echo "  ❌ stack did not come up — see /tmp/fable-demo-boot.log" >&2
    exit 1
fi
echo

wait_draft() {  # $1 ticket_id -> echoes the draft body_text (may be empty)
    local tid="$1" i
    for i in $(seq 1 40); do
        local body
        body="$(c "$BASE/fable/api/tickets/$tid" | $PY -c \
            "import sys,json; t=json.load(sys.stdin)['ticket']; print((t.get('draft') or {}).get('body_text','') if t.get('draft') else '')" 2>/dev/null)"
        if [ -n "$body" ]; then echo "$body"; return 0; fi
        sleep 0.3
    done
    echo ""
}

# 1. EMAIL — Emma asks where her order is -----------------------------------
echo "✉  Emma Wilson emails: \"Where is my order #BB1015?\""
EMAIL_TID="$(c -X POST "$MAILBOX/simulate/incoming" -H 'content-type: application/json' \
    -d '{"from_email":"emma.wilson@example.com","from_name":"Emma Wilson","subject":"Where is my order?","body_text":"Where is my order #BB1015?"}' \
    | $PY -c "import sys,json;print(json.load(sys.stdin)['ticket_id'])")"
EMAIL_DRAFT="$(wait_draft "$EMAIL_TID")"
if echo "$EMAIL_DRAFT" | grep -q "1Z999AA10123456784"; then
    echo "   ✨ AI drafted a reply with the real tracking number 1Z999AA10123456784"
else
    echo "   ✨ AI drafted a reply (ticket #$EMAIL_TID)"
fi
echo

# 2. CHAT — shipping question ------------------------------------------------
echo "💬 A visitor chats: \"Do you ship to Canada?\""
CHAT_TID="$(c -X POST "$BASE/fable/api/intake/chat" -H 'content-type: application/json' \
    -d '{"session_id":"demo-chat","name":"Nora","body_text":"Do you ship to Canada?"}' \
    | $PY -c "import sys,json;print(json.load(sys.stdin)['ticket_id'])")"
wait_draft "$CHAT_TID" >/dev/null
echo "   ✨ AI drafted a shipping answer (ticket #$CHAT_TID)"
echo

# 3. WHATSAPP — damaged + refund (sensitive) ---------------------------------
echo "📱 WhatsApp: \"My order arrived damaged, I want a refund!!\""
WA_TID="$(c -X POST "$BASE/fable/api/intake/whatsapp" -H 'content-type: application/json' \
    -d '{"phone":"+15558231838","name":"Emma","body_text":"My order arrived damaged, I want a refund!!"}' \
    | $PY -c "import sys,json;print(json.load(sys.stdin)['ticket_id'])")"
wait_draft "$WA_TID" >/dev/null
SENS="$(c "$BASE/fable/api/tickets/$WA_TID" | $PY -c "import sys,json;print(json.load(sys.stdin)['ticket']['sensitive'])")"
echo "   ⚠️  flagged SENSITIVE=$SENS — careful draft, makes no promises (ticket #$WA_TID)"
echo

# 4. CONSOLE VERBS -----------------------------------------------------------
echo "🧑‍💻 Agent actions from the console:"
c -X DELETE "$MAILBOX/outbox" >/dev/null
DRAFT_TXT="$(mktemp)"; SEND_JSON="$(mktemp)"
printf '%s' "$EMAIL_DRAFT" > "$DRAFT_TXT"
$PY -c "import json,sys; open(sys.argv[1],'w').write(json.dumps({'text':open(sys.argv[2]).read()}))" \
    "$SEND_JSON" "$DRAFT_TXT"
c -X POST "$BASE/fable/api/tickets/$EMAIL_TID/send" -H 'content-type: application/json' \
    -d @"$SEND_JSON" >/dev/null
rm -f "$DRAFT_TXT" "$SEND_JSON"
OUT_COUNT="$(c "$MAILBOX/outbox" | $PY -c "import sys,json;print(json.load(sys.stdin)['count'])")"
OUT_TO="$(c "$MAILBOX/outbox" | $PY -c "import sys,json;d=json.load(sys.stdin);print(d['outbox'][0]['to'] if d['outbox'] else '')")"
echo "   ✅ sent Emma's email reply — outbox now holds $OUT_COUNT message to $OUT_TO"
c -X POST "$BASE/fable/api/tickets/$CHAT_TID/note" -H 'content-type: application/json' \
    -d '{"text":"internal: quoted the shipping policy"}' >/dev/null
echo "   📝 saved an internal note on the chat ticket (never leaves Fable)"
c -X POST "$BASE/fable/api/tickets/$WA_TID/rewrite" -H 'content-type: application/json' \
    -d '{"instruction":"make it friendlier"}' >/dev/null
echo "   ♻️  asked the AI to rewrite the WhatsApp draft (friendlier)"
echo

# 5. GORGIAS-COMPAT ----------------------------------------------------------
GC_TOTAL="$(c "$BASE/api/tickets" | $PY -c "import sys,json;print(json.load(sys.stdin)['meta']['total_resources'])")"
echo "🔌 Gorgias-compat  GET /api/tickets  → lists $GC_TOTAL tickets (drop-in for the VPS tools)"
echo

echo "════════════════════════════════════════════════════════════"
echo "  Demo complete. The stack is STILL RUNNING."
echo "  Open the console:   $BASE"
echo "  Stop it with:       $SCRIPT_DIR/stop-server.sh && $FABLE_DIR/emulators/stop-emulators.sh"
echo "════════════════════════════════════════════════════════════"
