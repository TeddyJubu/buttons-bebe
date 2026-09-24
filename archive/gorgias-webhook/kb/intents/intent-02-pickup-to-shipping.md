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
Remove order from pickup workflow/bin.
Confirm shipping address.
Check whether order qualifies for free shipping.
If it qualifies, switch order to shipping.
If it does not qualify, send or draft invoice for shipping charge.
Notify warehouse to ship the order.

## Customer response if free shipping applies

Hi! No problem, we can switch your order from pickup to shipping.
We updated the order to ship to the address on file. You’ll receive tracking once it ships.

## Customer response if shipping payment is needed

Hi! We can switch your order from pickup to shipping.
Your order does not meet the free-shipping minimum, so there will be a shipping charge. We can send you an invoice for the shipping cost, and once it’s paid, we’ll have the order shipped out.
