---
title: Exemplar — Refund request (ESCALATION, not auto-answered)
category: tickets
status: confirmed
source: derived-from-tickets
tags: [refund, escalation, immediate, sensitive, exemplar]
---

## Customer situation

Customer asks for a refund (out-of-stock item, "money back," returning items they
don't want, etc.).

## The correct AI behavior: escalate, do not draft a customer reply

Refunds are **sensitive → IMMEDIATE → escalation-only**. The AI must **not** draft
a customer-facing reply that confirms, promises, denies, or processes a refund.

What the system does instead:
1. Classify the ticket IMMEDIATE.
2. Tag it and post an **internal note** summarizing the refund request (for the
   human agent), not a customer reply.
3. Notify the owner so a human handles the money decision.

## Internal note the AI may post (NOT sent to the customer)

## Why

Money movement is irreversible and high-stakes, and refund/chargeback/dispute
tickets are exactly the cases the safety model reserves for a human. See
`../policies/refunds-and-disputes.md` and `SYSTEM_WORKFLOW.md` Safety Model item 2.
