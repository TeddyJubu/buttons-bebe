---
title: Order Changes & Cancellations
category: policies
status: confirmed
tags: [order-change, size-swap, address-change, pickup, package-protection, price-adjustment]
---

The owner-confirmed rule across order changes is **action before response**:
complete or draft the change first, then reply. Most changes are only possible
**before the order ships** — always check fulfillment status first. See
`agent-core-rules.md`.

## Changing a size before it ships

A size can be changed **only if the order has not shipped and the requested size is
available**. Locate the order, check fulfillment status and size availability, and
edit the order (or draft the change for approval), then reply. If the order already
shipped, apologize — the size can no longer be changed. See
`intents/intent-08-wrong-size-switch-before-shipping.md`.

## Changing the shipping address / zip code

An address or zip-code correction is possible **only if the order has not shipped**.
If not shipped, update or draft the correction and confirm. If it already shipped,
explain it cannot be changed on our end and the customer may contact the carrier
directly to request a delivery change. See
`intents/intent-10-zip-code-address-correction.md`.

## Switching pickup and shipping

A **pickup** order can be switched to **shipping** even after it was processed for
pickup, as long as it has not been picked up (check free-shipping eligibility; if it
does not qualify, invoice the shipping charge). A **shipping** order can be switched
to **pickup** only if it has **not shipped** yet (refund shipping if applicable).
See `intents/intent-02-pickup-to-shipping.md` and
`intents/intent-03-shipping-to-pickup.md`.

## Removing / refunding package protection

If a customer was charged package protection and wants it removed, **remove/refund
it if possible** and respond after the action is completed. If it cannot be
completed directly, draft it for staff approval. See
`intents/intent-06-cancel-refund-package-protection.md`.

## Price adjustments — we do NOT offer them

We do **not** offer price adjustments after an order is placed. If a customer sees an
item drop in price later, the order is final sale at the price paid. Decline kindly;
do not retroactively match a lower later price.

## Cancellations

[PLACEHOLDER — confirm with owner.] Conservative default: a cancellation can be
requested **before the order ships**. Because a cancellation may involve a refund,
and **refunds are escalation-only**, do not confirm or process a cancellation refund
in a draft — escalate per `refunds-and-disputes.md`. A simple "please hold / don't
ship yet" change before fulfillment can be noted for the warehouse.

## Promo / discount codes applied late

[PLACEHOLDER — confirm with owner.] If a customer forgot to apply a current promo
code and the order has not shipped, treat it case by case. Because applying it may
mean refunding a difference, and **refunds are escalation-only**, surface this to a
human rather than promising the refund in an automated draft.
