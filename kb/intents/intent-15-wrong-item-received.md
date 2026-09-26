---
title: Intent 15 — Customer received the wrong item
category: intents
status: confirmed
tags: [wrong-item, photo, warehouse, escalate, inventory]
---

## Policy — wrong item received

Customer says they received the wrong item.

Apologize and verify. The correct item may still physically be available even if inventory does not show extra stock, because the wrong item was shipped.

## Agent action

Apologize.
Ask for photo of item received, including tag/label if needed.
Compare received item to order.
Draft a staff handoff asking an authorized warehouse teammate to locate the
correct item, decide whether the wrong item must be returned, and arrange any
replacement shipment. The AI must not notify the warehouse, create a return,
ship an item, or claim that any of those actions happened.
Do not rely only on inventory count.

## Customer response

Hi there, I’m sorry you received the wrong item. Could you send a photo of the item with its tag?

Ask only if the photo is not already supplied. Put any warehouse/replacement decision in authenticated staff_next_step; never promise a correction or follow-up.
