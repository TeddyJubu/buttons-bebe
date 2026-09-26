---
title: Intent 16 — Customer received a damaged item
category: intents
status: confirmed
tags: [damaged, defect, photo, vendor, replacement, store-credit]
---

## Policy — damaged item received

Customer says item arrived damaged.

Photos are required. Damage should be passed to the company/vendor. If replacement is available, check whether replacement can be sent.

## Agent action

Apologize.
Ask for clear damage photos.
Ask for tag/label photo if needed.
Check if replacement is available.
If replacement is available, draft a staff handoff to the warehouse.
Draft the vendor/company escalation with photos and item/order details for staff
to send. The AI must not notify the warehouse, email a vendor, or create a replacement.
If no replacement is available, follow refund/return/store-credit policy.

## Customer response

Hi there, I’m sorry the item arrived damaged. Could you send clear photos of the damage and the item’s tag?

Ask only for photos not already supplied. When evidence is complete, hold the draft for a staff decision about the item/vendor/replacement; record the exact task in staff_next_step without claiming it has started.
