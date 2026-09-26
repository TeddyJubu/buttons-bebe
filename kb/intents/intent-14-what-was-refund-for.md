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

Explain the refund reason only when that reason is explicitly recorded; an unfulfilled order or a typical stock issue does not establish it. Otherwise mark Needs staff input and have staff verify the affected item, amount and recorded reason.

## Customer response if refund was connected to a return

Use verified return/refund facts only. Ask for an identifying order or invoice only if missing. If records do not explain the amount or decision, hold the draft for a specific staff review of items, amount, return status and applicable policy.

## Customer response while under review

Mark Needs staff input with the specific refund facts to verify. Do not claim someone is reviewing, promise an update, or replace the answer with a generic acknowledgment.
