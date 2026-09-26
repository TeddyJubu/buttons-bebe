---
title: Intent 2 — Customer selected pickup but wants shipping instead
category: intents
status: confirmed
tags: [pickup, shipping, order-change, free-shipping, invoice]
---


## Policy — switching pickup to shipping

Customer placed order for pickup but now wants it shipped.

This can be changed even if the order was already processed for pickup, as long as it has not been picked up.

## Agent action

Locate order.
Confirm shipping address.
Check whether order qualifies for free shipping.
Draft a staff handoff listing the required pickup-workflow, shipping, invoice,
and warehouse changes. The AI must not make those changes or send an invoice.

## Customer response if free shipping applies

Use this only when the read-only order record confirms a human completed the change:
Hi! Your order was updated to ship to the address on file. You’ll receive tracking once it ships.

## Customer response if shipping payment is needed

State a shipping charge only when verified for this order. If staff have not confirmed the change and invoice, hold the draft as Needs staff input. Set staff_next_step to verify pickup status, address, shipping eligibility/cost and the required invoice. Never claim a switch, invoice or dispatch has happened or is guaranteed.
