---
title: Exemplar — Pre-ship size swap
category: tickets
status: confirmed
source: derived-from-tickets
tags: [order-change, size-swap, exemplar, tone]
---

## Customer situation

Customer placed an order and immediately noticed they selected the wrong size on
one item (e.g. picked 12 months, meant 18 months). They ask if it can be switched
before it ships.

## How it was handled

An authorized human agent checked that the order had not yet shipped, made the
size change, and replied warmly and briefly. No charge change was needed for a
same-price size swap. This history describes a human action; it is not permission
for the AI to edit an order or claim that the edit happened.

## Safe AI draft while the change is pending

> Hi! No problem — we’re reviewing whether we can update the [item] to size
> [new size] before it ships. We’ll confirm once our team has checked it.

The AI must not say the item was switched or the order was updated. Only a human
may use completed-action wording after the read-only order record confirms that
authorized staff completed the change.

## Why this is the model

- Acknowledges the request without falsely confirming an operational change.
- Staff can make the change only while the order is unshipped; always verify
  fulfillment status first and draft a handoff for the human action.
- Warm, low-friction tone matching the store's voice.
