---
name: gorgias
description: "Gorgias Helpdesk REST API: manage tickets, messages, macros, customers, and internal notes for Buttons Bebe support."
version: 1.0.0
author: Buttons Bebe
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Gorgias, Helpdesk, Customer-Support, Tickets, API]
    related_skills: [shopify, support-agent]
prerequisites:
  envvars: [GORGIAS_SUBDOMAIN, GORGIAS_API_KEY, GORGIAS_API_EMAIL]
---

# Gorgias Helpdesk API

Manage Buttons Bebe support tickets through the Gorgias REST API using curl from
the terminal tool. All endpoints are documented at
https://docs.gorgias.com/reference.

## Prerequisites

Three environment variables must be set (load from `~/.hermes/.env` or the
project `.env` file):

| Variable            | Example                        | Where to get it                          |
|---------------------|--------------------------------|------------------------------------------|
| GORGIAS_SUBDOMAIN   | `buttonsbebe`                  | Gorgias Settings > Account > Subdomain   |
| GORGIAS_API_EMAIL   | `agent@example.com`            | The API user's email                      |
| GORGIAS_API_KEY     | `sk_...`                        | Gorgias Settings > REST API > API Key     |

### Auth

Gorgias REST uses HTTP Basic auth with the API email as the username and the
API key as the password. Every curl call must include:

```bash
-u "$GORGIAS_API_EMAIL:$GORGIAS_API_KEY"
```

### Base URL

```
https://{{subdomain}}.gorgias.com/api
```

### Setup snippet (run at the top of any session)

```bash
# Load credentials
if [ -f "/root/Buttonsbebe Agent/.env" ]; then
  set -a; source "/root/Buttonsbebe Agent/.env"; set +a
fi

BASE="https://${GORGIAS_SUBDOMAIN}.gorgias.com/api"
AUTH="-u ${GORGIAS_API_EMAIL}:${GORGIAS_API_KEY}"
```

---

## 1. Tickets

### List tickets (with filters)

```bash
# All open tickets, most recent first, 25 per page
curl -s $AUTH "$BASE/tickets?status=open&per_page=25&order_by=updated_at&order_dir=desc" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for t in data.get('data', []):
    print(f\"#{t['id']}  {t['status']:8}  {t['subject']}\")"

# Filter by assignee, status, tag
curl -s $AUTH "$BASE/tickets?assignee_id=12345&status=open&tags=refund"

# Search by text
curl -s $AUTH "$BASE/tickets?q=where+is+my+order"
```

### Get a single ticket (full detail)

```bash
TICKET_ID=1234567890

curl -s $AUTH "$BASE/tickets/$TICKET_ID" \
  | python3 -c "
import sys, json
t = json.load(sys.stdin)
print(f\"Ticket #{t['id']}: {t['subject']}\")
print(f\"Status: {t['status']}\")
print(f\"Channel: {t['channel']}\")
print(f\"Customer: {t.get('customer', {}).get('email', 'N/A')}\")
print(f\"Assignee: {t.get('assignee_user_id', 'Unassigned')}\")
print(f\"Tags: {', '.join(t.get('tags', []))}\")
print(f\"Created: {t['created_at']}\")
print(f\"Updated: {t['updated_at']}\")
print()
print('--- Body ---')
print(t.get('body', '(no body)'))"
```

### Get ticket messages (the conversation thread)

```bash
curl -s $AUTH "$BASE/tickets/$TICKET_ID/messages" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    sender = m.get('sender', {}).get('email', 'unknown')
    role = m.get('role', 'unknown')  # 'user' = customer, 'agent' = staff, 'system'
    action = m.get('action', 'message')  # 'message', 'internal_note', etc.
    body = m.get('body', {}).get('plain', '(no body)')
    print(f\"[{role}/{action}] {sender}:\")
    print(body)
    print('---')"
```

### Create a ticket

```bash
curl -s -X POST $AUTH "$BASE/tickets" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "Customer inquiry",
    "channel": "email",
    "customer": {"email": "customer@example.com"},
    "messages": [
      {
        "role": "user",
        "action": "message",
        "body": {"plain": "Customer message body"}
      }
    ]
  }'
```

---

## 2. Replying to a ticket

### Post an external reply (customer-facing)

> **SAFETY:** The support agent must NEVER send an external reply automatically.
> Always draft the reply as an internal note first, let a human review, then the
> human sends it. Use `action: "internal_note"` for drafts.

```bash
# Post an INTERNAL NOTE (draft for human review — safe)
curl -s -X POST $AUTH "$BASE/tickets/$TICKET_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "agent",
    "action": "internal_note",
    "body": {"plain": "DRAFT REPLY:\n\nHello [Customer],\n\nThank you for reaching out...\n\n[Draft continues here]"}
  }'

# Post an EXTERNAL REPLY (sends to customer — HUMAN ONLY)
curl -s -X POST $AUTH "$BASE/tickets/$TICKET_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "role": "agent",
    "action": "message",
    "body": {"plain": "Hello,\n\nYour reply text here."}
  }'
```

### Mark a ticket as resolved / reopen

```bash
# Set status to closed/resolved
curl -s -X PUT $AUTH "$BASE/tickets/$TICKET_ID" \
  -H "Content-Type: application/json" \
  -d '{"status": "closed"}'

# Reopen
curl -s -X PUT $AUTH "$BASE/tickets/$TICKET_ID" \
  -H "Content-Type: application/json" \
  -d '{"status": "open"}'
```

### Assign a ticket

```bash
curl -s -X PUT $AUTH "$BASE/tickets/$TICKET_ID" \
  -H "Content-Type: application/json" \
  -d '{"assignee_user_id": 12345}'
```

### Add / remove tags

```bash
curl -s -X PUT $AUTH "$BASE/tickets/$TICKET_ID" \
  -H "Content-Type: application/json" \
  -d '{"tags": ["refund", "escalated"]}'
```

---

## 3. Macros (canned replies)

### List macros

```bash
curl -s $AUTH "$BASE/macros?per_page=50" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for m in data.get('data', []):
    print(f\"#{m['id']}  {m['name']}\")"
```

### Get macro detail (body template)

```bash
MACRO_ID=67890

curl -s $AUTH "$BASE/macros/$MACRO_ID" \
  | python3 -c "
import sys, json
m = json.load(sys.stdin)
print(f\"Name: {m['name']}\")
print(f\"Body:\")
print(m.get('body', '(no body)'))"
```

### Apply a macro to a ticket (preview then send)

```bash
# Preview — shows what the macro would produce with the ticket's variables
curl -s $AUTH "$BASE/macros/$MACRO_ID/preview?ticket_id=$TICKET_ID"

# Apply — posts the macro body as a reply to the ticket
# HUMAN ONLY — do not auto-apply
curl -s -X POST $AUTH "$BASE/macros/$MACRO_ID/apply" \
  -H "Content-Type: application/json" \
  -d "{\"ticket_id\": $TICKET_ID}"
```

---

## 4. Customers

### Search / list customers

```bash
# Search by email
curl -s $AUTH "$BASE/customers?email=customer@example.com" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for c in data.get('data', []):
    print(f\"#{c['id']}  {c['name']}  {c['email']}\")"

# List recent customers
curl -s $AUTH "$BASE/customers?per_page=25&order_by=updated_at&order_dir=desc"
```

### Get customer detail (includes order history link)

```bash
CUSTOMER_ID=12345

curl -s $AUTH "$BASE/customers/$CUSTOMER_ID" \
  | python3 -c "
import sys, json
c = json.load(sys.stdin)
print(f\"Name: {c['name']}\")
print(f\"Email: {c['email']}\")
print(f\"Phone: {c.get('phone', 'N/A')}\")
print(f\"Created: {c['created_at']}\")
# c['data'] often includes Shopify order IDs and addresses
import json as j
print('Data:', j.dumps(c.get('data', {}), indent=2))"
```

---

## 5. Satisfaction Surveys

### List satisfaction survey results

```bash
curl -s $AUTH "$BASE/satisfaction-surveys?per_page=25" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for s in data.get('data', []):
    print(f\"Ticket #{s.get('ticket_id')}  Score: {s.get('score')}  Comment: {s.get('comment', '')}\")"
```

---

## 6. Fulfillment Events (track package status)

Gorgias can receive fulfillment events from Shopify (tracking links, delivery
status). Use these to check on a package without going to Shopify directly.

### List fulfillment events for a ticket

```bash
curl -s $AUTH "$BASE/tickets/$TICKET_ID/fulfillment-events" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for e in data.get('data', []):
    print(f\"{e.get('type')}  {e.get('tracking_number')}  {e.get('tracking_url')}\")"
```

---

## Quick Reference

| Action                  | Method | Endpoint                              |
|-------------------------|--------|---------------------------------------|
| List tickets            | GET    | `/tickets`                            |
| Get ticket              | GET    | `/tickets/{id}`                       |
| Get messages            | GET    | `/tickets/{id}/messages`              |
| Post internal note      | POST   | `/tickets/{id}/messages`              |
| Post external reply     | POST   | `/tickets/{id}/messages`              |
| Update ticket (status)  | PUT    | `/tickets/{id}`                        |
| List macros             | GET    | `/macros`                              |
| Get macro               | GET    | `/macros/{id}`                        |
| Apply macro             | POST   | `/macros/{id}/apply`                  |
| Search customers        | GET    | `/customers?email=...`                 |
| Get customer            | GET    | `/customers/{id}`                     |
| Fulfillment events      | GET    | `/tickets/{id}/fulfillment-events`    |

## Safety rules (from agent-core-rules)

- **Never auto-send** external replies. Draft as internal notes for human review.
- Search the KB (`search_kb` tool) before answering any customer question.
- If the KB flags a topic as sensitive (refunds, disputes, damaged/wrong items,
  cancellations, address changes), **escalate** — do not draft a reply.
