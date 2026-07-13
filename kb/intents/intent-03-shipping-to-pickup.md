---
title: Intent 3 — Customer selected shipping but wants pickup instead
category: intents
status: confirmed
tags: [pickup, shipping, order-change, before-shipping, refund-shipping]
---

## Policy — switching shipping to pickup

Customer placed order for shipping but wants pickup instead.

This can only be changed if the order has not shipped yet.

## Agent action

Locate order.
Check fulfillment/shipping status.
If not shipped, draft a staff handoff to change the order to pickup and review
any shipping refund. The AI must not edit the order or issue a refund.
If already shipped, explain that it cannot be changed.

## Customer response if not shipped

Use this only when the read-only order record confirms a human completed the change:
Hi! Your order was switched to pickup.
You’ll receive pickup instructions once the order is ready.

## Customer response if already shipped

Hi! I’m sorry, this order has already shipped, so we’re no longer able to switch it to pickup.
You should receive tracking updates from the carrier.
