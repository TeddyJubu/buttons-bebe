---
title: Return & Exchange Policy
category: policies
status: confirmed
tags: [returns, exchanges, final-sale, refund-window, store-credit, warehouse-exchange]
---

## Refund window — 7 days after delivery

An order is refundable for **7 days after delivery**. To qualify for a refund, the
return package must be **scanned in by the shipping carrier within 7 days** of the
order being delivered. After that window, eligible returns may only qualify for
**store credit** instead of a refund. (See `agent-core-rules.md`: a refund
connected to a return that may be incorrect is escalated to a human.)

## Store credit after the refund window

Refunds are available only within the 7-day refund window. If a customer held onto
the item and the return was scanned by the carrier **outside** the refund window,
the return changes to **store credit** instead of a refund. An agent can check the
order's delivery date and carrier scan date to confirm the timing for the customer.

## Final sale items (shipped) — warehouse-only exchange exception

Final sale items that have already shipped are **not eligible for a regular return
or exchange by mail**. If an exception is made, it must be a **warehouse-only,
in-person exchange** that is approved. The item must be **unworn, unused, and with
tags attached**. The warehouse exchange address is:

Buttons Bebe
2133 Lakewood Road
Unit 104
Toms River, NJ

Do not promise an exception — escalate or ask staff for approval (see
`agent-core-rules.md`).

## Final sale wrong size before shipping

If a final sale order has **not shipped yet**, the size can still be changed if the
requested size is available — this is the normal pre-ship size swap, not an
exception. Locate the order, check fulfillment status and size availability, and
draft a staff handoff for the change before the order ships. The AI must never
edit the order itself. See
`order-changes-and-cancellations.md` and the intents
`intents/intent-08-wrong-size-switch-before-shipping.md` and
`intents/intent-12-final-sale-exchange-exception.md`.

## When the item is our / the vendor's fault

If the customer received the wrong item, a defective/damaged item, or an item that
does not match its description, this is **not** a buyer return and final-sale rules
do **not** apply. Apologize, request a photo (including the tag/label), and draft a
staff handoff for the defect flow (replacement/store-credit review and a vendor
email with photos). The AI performs none of those actions. See
`warranty-and-defects.md` and the intents
`intents/intent-15-wrong-item-received.md` and
`intents/intent-16-damaged-item-received.md`.

## How to handle a return / refund-timing request

1. Locate the order; check the delivery date and (if a return exists) the carrier
   scan date.
2. If within 7 days of delivery and scanned in time → eligible for a refund.
3. If scanned outside the 7-day window → store credit, not a refund.
4. For a **shipped final sale item**, explain the warehouse-only exchange exception
   (do not promise it — escalate for approval).
5. For our/vendor fault (wrong/damaged/mismatch), route to the defect flow, not a
   buyer return.
6. **A refund connected to a return that may be incorrect is escalated to a human**
   — review item refunded, refund amount, store-credit-vs-refund, return window, and
   accepted/rejected items before confirming. See `refunds-and-disputes.md`.
