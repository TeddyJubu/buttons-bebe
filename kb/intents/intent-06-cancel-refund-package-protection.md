---
title: Intent 6 — Customer asks to cancel/refund package protection
category: intents
status: confirmed
tags: [package-protection, refund, order-change, remove]
---

## Policy — removing package protection

Customer was charged package protection and wants it removed/refunded.

Package protection may be removed/refunded by authorized staff when allowed.

## Agent action

Locate order.
If order is identified, do not ask for order number.
Draft a staff handoff requesting the removal/refund if allowed. The AI must not
cancel protection or issue a refund. Only use a completed-action response when
the read-only order record confirms a human completed it.

## Customer response if completed

Only describe a removal/refund as completed when the read-only record explicitly proves it. Do not infer bank timing. Otherwise hold the draft for the authorized removal/refund decision with a specific staff_next_step. Keep the refund request sensitive.

## Customer response if order cannot be identified

Hi! We can help with that. Can you please send your order number so we can pull up the order?
