---
type: Rule
title: Ticket Priority Rules
description: Three-tier priority system that governs how the agent handles every ticket.
tags: [priority, immediate, high, low, urgent, escalate, routing, triage]
links:
  - kb/core-rules.md
timestamp: 2026-06-28
---

# Ticket Priority Rules

Every ticket is assigned one of three priority levels before any action is taken.

---

## IMMEDIATE — notify owner, act now

Time-sensitive issues where waiting makes the problem worse or impossible to fix.

**Examples:**
- Address / zip code correction — must happen before the order ships
- Wrong size or item correction — must happen before fulfillment
- Pre-shipment cancellation — must happen before the order leaves the warehouse
- Pickup ↔ shipping switch — must happen before fulfillment
- Urgent delivery rerouting — failed delivery, package needs redirection

**Agent behavior:**
- Immediately alert owner via Telegram with 🚨 ACT NOW
- Do NOT generate a draft reply — the owner needs to act, not read a draft
- The owner opens Gorgias and handles it directly

---

## HIGH — notify owner, draft reply, can wait a few hours

Critical for retention and revenue, but a short delay is acceptable.

**Examples:**
- Refund / chargeback / cancellation requests (post-fulfillment)
- Damaged, wrong, or missing item received
- Payment disputes
- Order not received (tracking shows delivered, customer doesn't have it)
- Angry or abusive language
- Repeated follow-ups (same customer, 3+ messages, ticket still open)

**Agent behavior:**
- Generate a draft reply using the KB
- Post the draft as an internal note in Gorgias (owner reviews before sending)
- Alert owner via Telegram with ⚠️ Review needed

---

## LOW — auto-draft, routine

Informational — safe to queue or automate.

**Examples:**
- Order status questions
- Shipping delay inquiries (general, not urgent rerouting)
- Product / sizing questions
- Policy FAQs (return window, shipping times, etc.)
- "Thank you" messages
- General product inquiries
- Newsletter / opt-out requests

**Agent behavior:**
- Generate a draft reply
- If WORKFLOW_A_CONFIRM=1 AND the intent is in AUTO_SEND_INTENTS AND KB confidence is HIGH → auto-send as public reply
- Otherwise → post as internal note for human review
- Notify owner via Telegram with 📝 Draft ready (or ✅ Auto-sent)

---

## Quick rule of thumb

| Question | Priority |
|---|---|
| Will waiting make this impossible to fix? | IMMEDIATE |
| Is this critical to the business or customer relationship? | HIGH |
| Is this informational or routine? | LOW |
