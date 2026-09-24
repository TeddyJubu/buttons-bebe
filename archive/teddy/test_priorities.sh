#!/bin/bash
# test_priorities.sh — smoke-test all three priority tiers
# Usage: bash test_priorities.sh [host:port] [webhook-secret]
HOST="${1:-localhost:8000}"
SECRET="${2:-choose-a-long-random-secret}"  # matches .env.example default

pass=0; fail=0

check() {
    local id="$1" desc="$2" expect="$3" body="$4"
    resp=$(curl -s -X POST "http://$HOST/webhook" \
        -H "Content-Type: application/json" \
        -H "X-Gorgias-Secret: $SECRET" \
        -d "$body")
    got=$(echo "$resp" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('priority','?'))" 2>/dev/null)
    if [ "$got" = "$expect" ]; then
        echo "✅  #$id $desc → $got"
        ((pass++))
    else
        echo "❌  #$id $desc → got=$got expected=$expect | $resp"
        ((fail++))
    fi
}

msg() { printf '{"ticket":{"id":%s,"subject":"%s","customer":{"email":"test@example.com"},"messages":[{"body_text":"%s","from_agent":false}]}}' "$1" "$2" "$3"; }

echo "=== IMMEDIATE ==="
check 3001 "Change address"       IMMEDIATE "$(msg 3001 'Change my address' 'I need to change my shipping address please')"
check 3002 "Cancel order"         IMMEDIATE "$(msg 3002 'Cancel my order'   'Please cancel my order immediately')"
check 3003 "Wrong size ordered"   IMMEDIATE "$(msg 3003 'Wrong size'        'I ordered the wrong size, can you change it before it ships')"
check 3004 "Switch to shipping"   IMMEDIATE "$(msg 3004 'Switch to ship'    'I want to switch to shipping instead of pickup')"
check 3005 "Wrong zip code"       IMMEDIATE "$(msg 3005 'Wrong zip'         'I put the wrong zip code on my order')"

echo ""
echo "=== HIGH ==="
check 3006 "Damaged item"         HIGH "$(msg 3006 'Item damaged'    'My order arrived damaged')"
check 3007 "Wrong item received"  HIGH "$(msg 3007 'Wrong item'      'I received the wrong item in my package')"
check 3008 "Refund request"       HIGH "$(msg 3008 'Refund'          'I would like a refund please')"
check 3009 "Angry customer"       HIGH "$(msg 3009 'Terrible'        'This is unacceptable, worst experience ever')"
check 3010 "Lost package"         HIGH "$(msg 3010 'Package lost'    'My package says delivered but I never received it')"

echo ""
echo "=== LOW (needs real LLM key) ==="
check 3011 "Order status"         LOW  "$(msg 3011 'Order status'    'Where is my order? It has been a week')"
check 3012 "Shipping time"        LOW  "$(msg 3012 'Shipping time'   'How long does shipping take to New Jersey')"
check 3013 "Return window"        LOW  "$(msg 3013 'Return policy'   'What is your return policy? How many days do I have')"
check 3014 "Sizing question"      LOW  "$(msg 3014 'Sizing'          'What sizes do you carry for a 3 year old')"
check 3015 "Discount question"    LOW  "$(msg 3015 'Discount'        'Do you offer any discount for first time customers')"

echo ""
echo "=== Results: $pass passed, $fail failed ==="
