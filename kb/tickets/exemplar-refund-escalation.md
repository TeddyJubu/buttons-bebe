---
title: Exemplar — Refund request (SENSITIVE DRAFT, human reviews)
category: tickets
status: confirmed
source: derived-from-tickets
tags: [refund, immediate, sensitive, sensitive-draft, exemplar]
---

## Customer situation

Customer asks for a refund (out-of-stock item, "money back," returning items they
don't want, etc.).

## The correct AI behavior: always draft a SENSITIVE reply

Refunds are **sensitive → IMMEDIATE/HIGH**. The AI always drafts a reply, prefixed
with `[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]`, using safe acknowledgment
language. The draft is shown in the console for human review and is never sent by
the AI.

The AI must not confirm, promise, deny, or process a refund; state an amount; or
make a binding commitment. The system classifies the ticket IMMEDIATE or HIGH,
notifies the owner via WhatsApp, and gives the draft to a human who can edit,
send, post as an internal note, or discard it.

## Sample sensitive draft

`[SENSITIVE — REVIEW CAREFULLY BEFORE SENDING]`

Hi! We're checking into this now. We want to make sure everything is processed
correctly, so we're reviewing the order and return details before confirming.
We'll get back to you shortly.

## Why

Money movement is irreversible and high-stakes. The draft acknowledges the
request without deciding it; the human remains the safety gate. See
`../policies/refunds-and-disputes.md` and
`../policies/sensitive-draft-policy.md`.
