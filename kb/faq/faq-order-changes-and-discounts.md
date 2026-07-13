---
title: FAQ — Order Changes, Cancellations & Discounts
category: faq
status: confirmed
source: derived-from-tickets
tags: [order-change, size-swap, address, pickup, discount, price-adjustment, package-protection]
---

## Do you offer a first-time customer discount?

No — Buttons Bebe does **not** offer a first-time customer discount. We do run sales
and promotions from time to time; the best way to stay updated is through our
emails/texts and website. Do not offer a code unless there is an active public
promotion. See `../intents/intent-01-first-time-customer-discount.md`.

## Can you change my size/item before it ships?

Yes, if the order **hasn't shipped** and the requested size is available. The AI
checks status and drafts a staff handoff; it never changes the order. If it already shipped, apologize — the size can
no longer be changed. See `../intents/intent-08-wrong-size-switch-before-shipping.md`.

## Can you change my shipping address?

Only **before the order ships**. If not shipped, the AI drafts the correction for
staff and does not update the order itself. If
it already shipped, it cannot be changed on our end and the customer may contact the
carrier directly. See `../intents/intent-10-zip-code-address-correction.md`.

## Can I switch between pickup and shipping?

A **pickup** order can be switched to **shipping** as long as it hasn't been picked
up (check free-shipping eligibility; staff may invoice the charge if it doesn't qualify). A
**shipping** order can be switched to **pickup** only if it hasn't shipped yet. The
AI only drafts the staff handoff. See
`../intents/intent-02-pickup-to-shipping.md` and
`../intents/intent-03-shipping-to-pickup.md`.

## Can you remove package protection?

Yes — authorized staff can remove/refund it when allowed. The AI must only draft a
staff handoff and may claim completion only after read-only data confirms it. See
`../intents/intent-06-cancel-refund-package-protection.md`.

## The price dropped after I ordered — can I get the difference?

No. We do **not** offer price adjustments. Once an order is placed it is final sale
at the price paid. Decline kindly.

## Can I cancel my order?

Cancellation can generally be requested **before the order ships**. Because a
cancellation may involve a refund, and **refunds are escalation-only**, the AI should
not confirm or process a cancellation refund — it escalates to a human. A simple
"please don't ship yet" hold can be noted for the warehouse.

## I forgot to apply a promo code — can you add it?

If the order hasn't shipped, this is handled case by case. Because applying a code
may mean refunding a difference, and refunds are escalation-only, surface it to a
human rather than promising the refund automatically.

## Can I order by phone?

Customers having trouble checking out online can call/text the store's public number
for help. Use the store's published contact number, never a customer's.
