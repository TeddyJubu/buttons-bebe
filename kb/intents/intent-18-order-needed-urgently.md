---
title: Intent 18 — Customer needs order urgently
category: intents
status: confirmed
tags: [urgent, rush, shipping-method, warehouse, upgrade-shipping]
---

## Policy — rush request

Customer needs order rushed or delivered by a specific date.

Warehouse can be asked to rush processing, but delivery depends on selected shipping method.

## Agent action

Locate order.
Ask warehouse to prioritize if possible.
Check selected shipping method.
If slow shipping was selected, staff must verify available upgrades and any cost
before offering one. Record this as staff_next_step with review_required=true
when no verified customer step is available. Never imply prioritization or a
shipping change has happened. Do not guarantee dispatch or delivery.

## Customer response

Use observed fulfillment and shipping facts only. Ask for the needed-by date
if it is absent: "What date do you need the order by?" If it is already supplied,
do not ask again. Hold the draft for staff to confirm possible dispatch/upgrade
options, with the exact order and deadline in authenticated internal metadata.
