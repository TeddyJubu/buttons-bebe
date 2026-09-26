---
title: Intent 13 — Customer asks if item is long sleeve or short sleeve
category: intents
status: confirmed
tags: [sleeve-length, product-info, long-sleeve, short-sleeve, do-not-guess]
---

## Policy — sleeve length

Customer wants sleeve-length information.

Answer only if information is available from product data, image, title, description, or saved notes.

## Agent action

Check product page/title/description.
Check saved product info.
If clear, answer.
If not clear, use normal staff review.

## Customer response if known

Hi! This item is [long sleeve / short sleeve].

## Customer response if unknown

Ask for product identity only if missing; otherwise hold for normal staff input. Set staff_next_step to verify sleeve type/length for the exact item and size. Never invent the measurement or claim it is being checked.
