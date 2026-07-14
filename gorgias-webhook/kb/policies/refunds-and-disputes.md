---
title: Refunds, Chargebacks & Disputes — ESCALATION ONLY
category: policies
status: confirmed
tags: [refund, chargeback, dispute, escalation, immediate, sensitive, refund-window, store-credit]
---

## These are NEVER auto-answered

Refunds, chargebacks, and payment disputes are **sensitive** and are
**escalation-only**. The AI agent must **never** auto-draft a customer-facing
answer that promises, confirms, denies, or processes a refund, and must never
respond to a chargeback or dispute notice on its own.

Per the classifier and the safety model, any message about a **refund,
chargeback, or dispute is classified IMMEDIATE and escalated to the owner** — the
system posts an internal escalation note and notifies the owner, but does not
draft a reply to the customer. (See `SYSTEM_WORKFLOW.md`, Safety Model item 2.)

## Why it is escalation-only

- Money movement is irreversible and high-stakes; it must have a human decision.
- Chargebacks/disputes involve the payment processor and deadlines a bot must not
  handle.
- Tone matters: these customers are often already upset; a wrong automated reply
  makes it worse.

## What the agent SHOULD do for these tickets

1. Classify as IMMEDIATE.
2. Tag the ticket and post an **internal note** summarizing the request (no
   customer-facing draft).
3. Notify the owner (Telegram) so a human handles it promptly.
4. Stop. Do not state whether a refund will be issued.

## Refund-timing context the agent MAY explain (informational, not a money decision)

The owner-approved responses let the agent explain refund **timing and reason**
without promising, denying, or processing any money:

- **Refund window:** an order is refundable for **7 days after delivery** — the
  return must be **scanned by the carrier within 7 days** of delivery. After that
  window, eligible returns may only qualify for **store credit**. (See
  `return-and-exchange-policy.md`.)
- **Refund issued before shipping:** usually because an item was **out of
  stock/unavailable** before the order shipped, so the customer was refunded for
  that item.
- **Store credit instead of refund:** if the item was held past the refund window
  and the return was scanned outside it, the return changes to **store credit**.

## A return-related refund that may be wrong is ESCALATED

A refund **connected to a return** may be incorrect and must be reviewed by a human
before anything is confirmed — do not assume it is correct. Check the item refunded,
the refund amount, store-credit-vs-refund, the return window, accepted/rejected
items, and any restocking/return fees. The agent may tell the customer it is being
reviewed (approved response), but the money decision is a human action. See
`agent-core-rules.md` ("when to escalate") and
`intents/intent-14-what-was-refund-for.md`.
