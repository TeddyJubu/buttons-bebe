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
If available and the order has not shipped, draft a staff handoff requesting
the size change. The AI must not edit the order. Use the completed-action
response only when the read-only order record confirms a human completed it.

## Customer response while staff reviews the requested change

Hi! No problem — we’re reviewing whether we can update the item to size
[new size] before it ships. We’ll confirm as soon as our team has checked it.

The AI must not say the size was switched or the order was updated. A human may
replace this draft with completed-action wording only after the order record
confirms that authorized staff made the change.

## Customer response if requested size unavailable

Hi! We checked, but unfortunately the size you wanted is not available.
Please let us know if you’d like to keep the original size or choose another available option.

## Customer response if already shipped

Hi! I’m sorry, this order has already shipped, so we’re no longer able to change the size in the order.
