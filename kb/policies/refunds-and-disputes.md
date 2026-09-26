---
title: Refunds, Chargebacks & Disputes — sensitive requests require human review
category: policies
status: confirmed
tags: [refund, chargeback, dispute, immediate, sensitive, sensitive-draft, refund-window, store-credit]
---

## Actionable sensitive requests

Refunds, chargebacks, and payment disputes are **sensitive**. For a new actionable
request, produce a grounded answer, necessary clarification or verified customer
step, prefixed with
`[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]`. The draft is shown in the console
for human review; it is never sent to the customer automatically.

A pure acknowledgment creates no new draft or alert, even on a refund thread.
It does not resolve the underlying dispute or clear existing alerts. Generation
failure displays AI draft unavailable; never substitute a generic acknowledgment.

The AI must never promise, confirm, deny, or process a refund, state a refund
amount, or use binding language about a chargeback or dispute. These tickets are
classified IMMEDIATE/HIGH and the owner is notified via WhatsApp for fast review.

## Why sensitive drafts still require human review

- Money movement is irreversible and high-stakes; it must have a human decision.
- Chargebacks/disputes involve the payment processor and deadlines a bot must not
  handle.
- The draft only gives the human a starting point; the human chooses whether to
  edit, send, post as an internal note, or discard it.

## What the agent SHOULD do for these tickets

1. Classify as IMMEDIATE or HIGH.
2. Draft a grounded answer or necessary clarification with the SENSITIVE prefix.
   If only staff can answer, mark Needs staff input with the exact facts to check.
3. Never state whether money will move or how much.
4. Notify the owner via WhatsApp.
5. Return the draft to the console; only a human may send or post it.

## Safe language

Name the customer's request and the verified facts. Put required review work in
staff_next_step, outside customer text. Do not claim staff are reviewing or
promise they will follow up. Avoid promises and
the phrases “money back,” “compensate,” “reimburse,” “credit your account,” “issue
a refund,” and “we will refund.” It is acceptable to name the customer's request
when necessary, but never present a money decision as made.

## Refund-timing context the agent MAY explain (informational, not a money decision)

The owner-approved responses let the agent explain refund **timing and reason**
without promising, denying, or processing any money:

- **Refund window:** an order is refundable for **7 days after delivery** — the
  return must be **scanned by the carrier within 7 days** of delivery. After that
  window, eligible returns may only qualify for **store credit**. (See
  `return-and-exchange-policy.md`.)
- **Refund issued before shipping:** usually because an item was **out of
  stock/unavailable** before the order shipped, so the customer was refunded for
  that item.
- **Store credit instead of refund:** if the item was held past the refund window
  and the return was scanned outside it, the return changes to **store credit**.

## A return-related refund that may be wrong

A refund **connected to a return** may be incorrect and must be reviewed by a human
before anything is confirmed—do not assume it is correct. Check the item refunded,
the refund amount, store-credit-vs-refund, the return window, accepted/rejected
items, and any restocking/return fees. Record this specific check as staff input;
do not claim it is already being reviewed. The money decision is a human action. See
`agent-core-rules.md` and
`intents/intent-14-what-was-refund-for.md`.
