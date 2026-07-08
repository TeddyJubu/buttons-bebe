---
title: Intent 14 — Customer asks what a refund was for / thinks refund may be an error
category: intents
status: confirmed
tags: [refund, return, out-of-stock, store-credit, review, escalate]
---

## Policy — refund reason depends on timing

Customer received a refund and wants to know why, or thinks it may be an error.

Refund reason depends on whether refund happened before shipping or as part of a return.

## Agent action

Locate order.
Check when refund was issued.
If refund was before shipping, the reason is usually that an item was out of stock/unavailable.
If refund was connected to a return, review carefully because there may be an error.
For return-related refunds, check:
- item refunded
- refund amount
- store credit vs refund
- return window
- rejected/accepted items
- restocking/return fees if applicable
Do not assume a return-related refund is correct until reviewed. A refund connected to a return that may be incorrect is an escalation case — route to a human/warehouse before confirming (see `../policies/refunds-and-disputes.md` and `../policies/agent-core-rules.md`).

## Customer response if refund was before shipping

Hi! We checked your order.
The refund was issued because [item name] was out of stock/unavailable before your order shipped, so you were refunded for that item.

## Customer response if refund was connected to a return

Hi! We’ll look into this for you.
Since this refund was connected to a return, we want to review the return details carefully to make sure everything was processed correctly. We’ll check the items, refund amount, and return status and get back to you.

## Customer response while under review

Hi! We’re checking into this now.
We want to make sure the refund was processed correctly, so we’re reviewing the order/return details before confirming.
