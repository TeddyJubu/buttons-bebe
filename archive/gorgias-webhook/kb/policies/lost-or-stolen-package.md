---
title: Lost or Stolen Package ("Delivered but Not Received")
category: policies
status: confirmed
source: derived-from-tickets
tags: [lost-package, stolen-package, delivered-not-received, package-protection, carrier-claim, escalation]
---

## Step 1 — Have the customer check first

When tracking shows **delivered** but the customer didn't receive the package, first
ask them to check:

- With a **neighbor**, building manager, or concierge.
- **Alternate drop spots** (front door, back door, porch, garage).
- With **other members of the household**.

Also check the carrier for a delivery photo. Many of these resolve at this step
without any refund. See `../intents/intent-15-wrong-item-received.md` for the
related wrong-item flow.

## Step 2 — Package-protection branch

If the package still can't be found, check whether the order had **package
protection**:

- **With protection** → the customer is eligible for a **full refund**. The agent
  may confirm coverage, but the refund itself is **escalation-only** — route to a
  human (see `package-protection.md` and `refunds-and-disputes.md`).
- **Without protection** → the store is **not liable**. Help the customer file a
  carrier claim (Step 3). Do not promise a refund.

## Step 3 — Carrier claim and escalation

Without package protection, help the customer **file a claim with the carrier**
(UPS / USPS / FedEx). If the **carrier claim is denied or fails**, **escalate to the
boss** — do not promise or issue a refund on your own authority. Document the
outcome in the ticket notes. This whole flow never auto-issues money; the money
decision is always a human action.
