---
title: Intent 17 — Customer asks when order will ship
category: intents
status: confirmed
tags: [shipping, processing-time, ship-status, fulfillment, tracking]
---

## Policy — processing time 24–48 hours

Customer wants order shipping status.

Orders usually ship within 24–48 hours. This is processing time, not carrier delivery time.

## Agent action

Locate order.
Check fulfillment status.
If unfulfilled, report that exact state. Do not infer packing or a dispatch date.
Mention normal processing time only when the order could still be within it;
otherwise acknowledge that it is past the usual window and identify the staff check.
If shipped, provide tracking/update.

## Customer response

Answer the observed shipping-status question first and include confirmed tracking
when available. Ask for an order number only if context cannot identify the order.
A general “are you shipping today?” question requires a current operational-hours
answer, not an order lookup. If staff must confirm dispatch or opening hours, mark
Needs staff input and specify that task outside the customer reply.
