---
title: Intent 10 — Customer needs zip code / address corrected
category: intents
status: confirmed
tags: [address-change, zip-code, order-change, before-shipping, carrier]
---

## Policy — address change before shipping

Customer entered correct address but wrong zip code appeared, or customer needs address corrected.

Address can be changed only if order has not shipped.

## Agent action

Locate order.
Check if order shipped.
If not shipped, draft a staff handoff with the exact requested correction. The
AI must not update the order.
If shipped, explain that it cannot be changed by Buttons Bebe.
If needed, advise customer to contact carrier.

## Customer response if changed

Use this only when the read-only order record confirms a human completed the change:
Hi! The shipping address/zip code on your order was updated.
Your order will now ship to the corrected address.

## Customer response if already shipped

Hi! I’m sorry, this order has already shipped, so we’re no longer able to update the address on our end.
You may be able to contact the carrier directly to request a delivery change.
