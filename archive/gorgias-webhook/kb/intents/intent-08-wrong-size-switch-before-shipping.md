---
title: Intent 8 — Customer entered wrong size, wants size switched before shipping
category: intents
status: confirmed
tags: [size-swap, order-change, before-shipping, availability]
---

## Policy — size change before shipping

Customer ordered the wrong size and wants the size changed.

Size can be changed only if the order has not shipped and requested size is available.

## Agent action

Locate order.
Check if order shipped.
Check requested size availability.
If available and order has not shipped, edit order.
If agent cannot edit directly, draft action for approval.
Respond after action is completed.

## Customer response if changed

Hi! No problem, we switched the size for you.
Your order is now updated to size [new size].

## Customer response if requested size unavailable

Hi! We checked, but unfortunately the size you wanted is not available.
Please let us know if you’d like to keep the original size or choose another available option.

## Customer response if already shipped

Hi! I’m sorry, this order has already shipped, so we’re no longer able to change the size in the order.
