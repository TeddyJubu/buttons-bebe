---
title: Sensitive Draft Policy — Always Draft, Human Reviews
category: policies
status: confirmed
sensitive: false
tags: [safety-model, sensitive-draft, always-draft, human-review, console]
---

## Core principle

The AI agent always generates a draft for every ticket, including sensitive
topics. The draft appears in the console Ticket feed for a human to review. The
AI never sends a customer-facing message and does not automatically post the
draft as an internal note.

From the console, a human may edit the draft and choose **Send reply**, **Draft
as internal note**, or **Request edit**. Sending always requires human confirmation,
and sensitive tickets show an additional warning.

## What makes a topic sensitive

- Refunds, chargebacks, and payment disputes
- Damaged, wrong, or missing items
- Lost or stolen packages
- Cancellations and address changes
- Angry or abusive customers
- Final-sale exception requests
- Return-related refunds that may be incorrect

## Required draft behavior

1. Prefix the draft with `[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]`.
2. Use safe acknowledgment language grounded in the relevant KB intent or policy.
3. Do not promise money, confirm a refund, state a refund amount, or make any
   binding commitment.
4. Classify the ticket at the appropriate elevated priority and notify the owner
   via WhatsApp when required.
5. Return the complete draft for human review even when the KB has no factual
   answer; acknowledge the request and flag the information gap instead of guessing.

## What does not change

- The AI never sends customer-facing messages.
- Shopify, Redo, and Gorgias access used by Hermes is read-only.
- Gorgias writes happen only after a human console action.
- The human decides whether to send, edit, post as a note, request a rewrite, or
  discard the draft.
- Everything is logged.
