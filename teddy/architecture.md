# Teddy — Architecture

AI support agent for Buttons Bebe.
Runs 100% locally inside `teddys-container` on the VPS.
No cloud knowledge catalog. No vector database. No ML models to manage.

---

## Guiding ideas

**From Pi Agent:** The agent is a minimal core that calls modular skills.
Each skill does one thing and can be swapped, improved, or disabled independently.
The core never changes — only skills evolve.

**From Open Knowledge Format (OKF):** The knowledge base is a directory of markdown files.
Each file has YAML frontmatter (type, title, tags, links) plus a free-form markdown body.
Files link to each other, forming a local knowledge graph.
No proprietary format, no database, no indexing step — just files.

---

## Folder structure

```
teddy/
  agent.py              — the core: receives webhook, orchestrates skills, returns response
  skills/
    search_kb.py        — reads OKF knowledge base, returns relevant context
    lookup_order.py     — calls /root/shopify/shopify.py, returns order status
    post_reply.py       — posts public reply or internal note to Gorgias
    notify.py           — sends Telegram notification to owner
    classify.py         — detects ticket intent (ORDER_STATUS, RETURN, SHIPPING, OTHER)
    escalate.py         — decides if a ticket must go to a human, and why
  kb/                   — OKF knowledge base (see KB section below)
  .env                  — all config and secrets
  .env.example          — safe template
  requirements.txt
  Dockerfile

/root/shopify/          — independent Shopify module (separate entity, not inside teddy/)
  shopify.py
  architecture.md
```

---

## The core: agent.py

The core does nothing clever. It receives the webhook and calls skills in order.
Each skill returns a structured result. The core decides what to do next based on that result.

```
webhook arrives
  │
  ├── classify skill    → intent: ORDER_STATUS | RETURN | SHIPPING | GENERAL | UNKNOWN
  │
  ├── search_kb skill   → kb_context: [list of relevant OKF concept files]
  │                        confidence: HIGH | MEDIUM | LOW | NONE
  │
  ├── lookup_order skill (only if intent is ORDER_STATUS)
  │     → order: {status, tracking_url, items, date} | None
  │
  ├── escalate skill    → should_escalate: True/False, reason: str
  │     (escalates if confidence=NONE, or order not found, or intent=UNKNOWN)
  │
  ├── [if not escalating]
  │     call LLM with: kb_context + order_data + ticket messages + system prompt
  │     → draft reply
  │
  ├── post_reply skill  (only if WORKFLOW_A_CONFIRM=1)
  │     → posts draft as internal note (human reviews) OR public reply (full auto)
  │
  └── notify skill      → Telegram message to owner
```

---

## The knowledge base: OKF format, 100% local

Each topic in the KB is one markdown file with YAML frontmatter.
No database. No indexing. No embeddings. Just files the owner edits in any text editor.

### File format

```markdown
---
type: Policy
title: Return Policy
description: Rules for returning items purchased at Buttons Bebe.
tags: [return, refund, exchange, money-back, unwanted, wrong-size]
links:
  - kb/policies/shipping.md
  - kb/policies/exchanges.md
timestamp: 2026-06-28
---

# Return Policy

You can return any item within **30 days** of delivery for a full refund.
Items must be unworn and unwashed with original tags attached.

To start a return, email us at returns@buttonsbebe.com with your order number.
We will email you a prepaid return label within 24 hours.

Refunds are processed within 5-7 business days after we receive the item.

## What cannot be returned
- Sale items marked "Final Sale"
- Swimwear (hygiene policy)
- Gift cards
```

### Why this is better than plain markdown

| Plain markdown | OKF markdown |
|---|---|
| Search by keyword only | Search by tags (explicit) + keywords (body) |
| No relationship between files | `links:` field connects related topics |
| Agent reads the whole file to decide relevance | Agent reads frontmatter first (fast), body second |
| No freshness signal | `timestamp:` shows when info was last verified |
| Agent treats all files equally | `type:` lets agent filter (policy question → only Policy files) |

### KB folder layout

```
kb/
  index.md              — overview of all topics (agent reads this first)
  policies/
    index.md
    returns.md          — type: Policy
    shipping.md         — type: Policy
    exchanges.md        — type: Policy
    discount-codes.md   — type: Policy
    privacy.md          — type: Policy
  products/
    index.md
    sizing-guide.md     — type: Guide
    care-instructions.md — type: Guide
    materials.md        — type: Guide
  faq/
    index.md
    order-tracking.md   — type: FAQ  (links to /root/shopify/ for live data)
    damaged-items.md    — type: FAQ
    gift-wrapping.md    — type: FAQ
  log.md                — chronological record of KB changes (owner updates this)
```

### How the search_kb skill works (local, no vector DB)

```
1. Read YAML frontmatter from every .md file (fast — frontmatter is small)
2. Score each file:
     tag score    = number of tags that appear in the customer's message (weight: 3x)
     keyword score = number of 4+ character words from message that appear in body (weight: 1x)
     total = (tag_score * 3) + keyword_score
3. Filter: only files where total > 0
4. Sort by score, take top 3 files
5. Follow links: for each top file, read its `links:` field and include those files too
     (so returns.md automatically pulls in exchanges.md and shipping.md)
6. Return combined text as kb_context
7. Confidence:
     HIGH  = top file score > 5
     MEDIUM = top file score 2-5
     LOW   = top file score 1
     NONE  = no files scored above 0
```

The `links:` graph traversal is the key upgrade over plain KB.
A question about "wrong size return" hits `returns.md` (score: HIGH),
which pulls in `exchanges.md` and `shipping.md` via links —
giving the LLM complete context about the whole returns/exchange/shipping flow
without the customer having to ask the right keywords.

---

## Skills

Each skill is a standalone Python file. It has one public function. It imports nothing
from `agent.py`. It can be tested and replaced independently.

### `skills/classify.py`
**Input:** ticket subject + customer message text
**Output:** `{intent: "ORDER_STATUS" | "RETURN" | "SHIPPING" | "PRODUCT" | "GENERAL" | "UNKNOWN", confidence: float}`
**How:** Keyword rules first (fast, no LLM call for obvious cases).
If ambiguous, one small LLM call to classify.
ORDER_STATUS is the first auto-send eligible intent (Phase 1).

### `skills/search_kb.py`
**Input:** customer message text
**Output:** `{context: str, files_used: [str], confidence: "HIGH"|"MEDIUM"|"LOW"|"NONE"}`
**How:** OKF search described above. Pure Python, no external dependencies.

### `skills/lookup_order.py`
**Input:** customer email (from Gorgias ticket)
**Output:** `{order: Order | None, source: "shopify"}`
**How:** Thin wrapper around `/root/shopify/shopify.py`. Handles the case where shopify
module is not configured (returns None gracefully, agent escalates).

### `skills/escalate.py`
**Input:** intent, kb_confidence, order result, ticket messages
**Output:** `{should_escalate: bool, reason: str}`
**Escalates when:**
- KB confidence is NONE (no relevant info found)
- Intent is UNKNOWN
- Order lookup failed or returned no result (for ORDER_STATUS intents)
- Message contains escalation keywords: "lawyer", "chargeback", "fraud", "complaint", "furious"
- Refund amount mentioned above threshold (configurable in .env)

### `skills/post_reply.py`
**Input:** ticket_id, draft text, mode ("internal_note" | "public_reply")
**Output:** `{posted: bool, gorgias_message_id: str | None}`
**Gate:** Only runs if WORKFLOW_A_CONFIRM=1 in .env
**Phase 1:** Always internal_note (human reviews)
**Phase 2+:** public_reply for high-confidence ORDER_STATUS tickets

### `skills/notify.py`
**Input:** ticket_id, intent, confidence, escalated, draft_preview
**Output:** `{sent: bool}`
**How:** Telegram message to owner. Always runs, even when escalating.
Escalation messages are flagged with ⚠️ so owner knows to look.

---

## Self-learning loop (no ML required)

The agent logs every ticket to `log.jsonl`:

```json
{"ticket_id": 1234, "intent": "RETURN", "kb_confidence": "LOW",
 "files_used": ["kb/policies/returns.md"], "escalated": false,
 "timestamp": "2026-06-28T10:00:00Z"}
```

Once a week, a summary script reads `log.jsonl` and prints:

```
Week of June 28:
  200 tickets processed
  Escalated: 31 (15.5%)

  Top escalation reasons:
    KB confidence NONE: 18 tickets  ← these topics need new KB files
    Intent UNKNOWN:      8 tickets  ← these need better classify rules
    Order not found:     5 tickets  ← customer used different email

  Top intents: RETURN (72), ORDER_STATUS (61), SHIPPING (38), PRODUCT (29)
  Auto-sent (ORDER_STATUS): 58/61 (95%)
```

Owner (or Claude) reads this, writes the missing KB files, and escalation rate drops.
The system learns — through weekly 10-minute KB maintenance sessions, not machine learning.

---

## Phase plan

### Phase 1 — Now (build this)
- Webhook → classify → search_kb → LLM draft → internal note → Telegram
- ORDER_STATUS: auto-send public reply (shopify lookup → confident → send)
- Everything else: internal note for human review

### Phase 2 — After 4 weeks of data
- Add RETURN, SHIPPING to auto-send when kb_confidence=HIGH
- Add escalation threshold tuning based on log data

### Phase 3 — After 3 months
- All common intents auto-send
- Escalation handles only edge cases
- Human reviews weekly log, not individual tickets

---

## Config (.env)

```bash
# LLM
LLM_BASE_URL=https://token-plan-sgp.xiaomimimo.com/v1
LLM_API_KEY=...
LLM_MODEL=mimo-v2.5-pro

# Gorgias
GORGIAS_DOMAIN=buttonsbebe
GORGIAS_EMAIL=...
GORGIAS_API_KEY=...
WEBHOOK_SECRET=...

# Shopify (for ORDER_STATUS skill)
SHOPIFY_STORE=buttons-bebe
SHOPIFY_ACCESS_TOKEN=shpat_...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...

# Gates
WORKFLOW_A_CONFIRM=0       # 0=dry-run, 1=live
AUTO_SEND_INTENTS=ORDER_STATUS   # comma-separated intents eligible for auto-send
ESCALATION_REFUND_THRESHOLD=50   # escalate if refund over $50 mentioned

# Port
PORT=8000
```

---

## Dependencies

```
flask==3.0.3
gunicorn==22.0.0
requests==2.32.3
openai==1.35.0
python-dotenv==1.0.1
pyyaml==6.0.1          # reads OKF frontmatter
```

Six packages. PyYAML is the only addition over the previous design — needed to parse
the YAML frontmatter in OKF files.

---

## What this is NOT

- Not a vector database
- Not a machine learning system
- Not a cloud service
- Not a framework with its own runtime
- Not tied to Google Cloud or any cloud provider

It is files, Python, and an LLM API call. A beginner can read every file in 30 minutes
and ask Claude to change anything.
