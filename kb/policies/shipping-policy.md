---
title: Shipping Policy
category: policies
status: confirmed
tags: [shipping, delivery, tracking, processing-time, usps, ups, eta, pickup]
---

## Processing time — 24 to 48 hours

Orders usually ship within **24–48 hours**. This is the **processing time** before
the package leaves the warehouse, and it is **separate** from carrier delivery
time. If an order is unfulfilled, explain this normal processing time; if it has
shipped, provide the tracking/update.

## Shipping time by carrier / method

Carrier delivery time depends on the shipping method selected at checkout, and is
separate from the 24–48 hour processing time:

- **USPS:** approximately **7–14 days** (USPS can be slow).
- **UPS:** usually **next day**.
- **ETA shipping:** usually **1–2 days**.

If a customer needs an order by a specific date, recommend a faster shipping option
(USPS can be slow). Do not promise a delivery date the carrier controls.

## Where we ship from and how

Orders ship from our warehouse in Toms River, NJ. Customers receive a tracking
number once the order ships. If a customer asks "where is my order," look up the
order's fulfillment status and tracking and share it. See
`intents/intent-17-when-will-order-ship.md` and
`intents/intent-22-how-long-shipping-takes.md`.

## Rush / urgent requests

Staff may check whether **prioritization** or an upgrade is possible, but the AI
must not say it happened or promise an upgrade. Put that check in staff_next_step
and hold the reply if staff-only facts are needed. Delivery depends on the
selected carrier/method; do not guarantee a delivery date. See
`intents/intent-18-order-needed-urgently.md`.

## Local pickup

Local customers can pick up from the Toms River warehouse. A pickup order can be
switched to shipping (even after it was processed for pickup) as long as it has not
been picked up; a shipping order can be switched to pickup only if it has not
shipped. See `intents/intent-02-pickup-to-shipping.md` and
`intents/intent-03-shipping-to-pickup.md`.

Do not invite collection of a specific order without verified pickup readiness.
Outdoor-bin access is separate from staffed hours and confirmed hours for a
particular day or holiday. An unfulfilled status does not prove packing or dispatch.

## International shipping

We do ship internationally (e.g. Canada and Israel order regularly). [PLACEHOLDER —
confirm international rates, carriers, and customs/duties responsibility with
owner.] Conservative default: international customers are responsible for any
customs fees or import duties charged by their country.

## Lost or stuck tracking

If tracking shows no movement or the package appears lost, this is a service issue —
gather the order/tracking details and route to a human rather than guessing. Do not
promise a refund or reshipment automatically.
