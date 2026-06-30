# Buttons Bebe — Knowledge Base (KB)

This folder is the **source of truth** for everything the Hermes AI support agent
knows. It is plain Markdown in Git. A separate ingestion worker (Stage 4) pushes
these files into Supermemory, which the draft engine queries at draft time. In
Stage 1–2 the same files are read directly by `kb_client.py`.

> Architecture: see `../PHASE1_KB_ARCHITECTURE.md`.
> File format (the contract every file follows): see `CONVENTIONS.md`. **Read it
> before adding or editing any file here.**

## Folder layout

```
kb/
  intents/    The 22 owner-confirmed customer intents (one file each). Each holds
              the Policy, Agent action, and the owner-APPROVED customer response
              template(s) verbatim. status: confirmed. The highest-trust source.
  policies/   Store policy docs (agent core rules, shipping, returns/exchanges,
              refunds, order changes, sizing, product care, warranty/defects).
              Owner-curated. Reconciled to the real KB: facts the owner doc
              confirms are status: confirmed; topics the doc doesn't cover stay
              DRAFT.
  faq/        Frequently-asked-question Q&A. The entries here are DERIVED from
              the real 12-month ticket export (most-common real questions +
              how agents actually answered), fully PII-scrubbed.
  tickets/    Curated, fully-anonymized exemplar resolved tickets (pattern +
              ideal answer). A few hand-picked teaching examples.
  learned/    EMPTY at setup. Filled automatically later by the feedback loop:
              validated agent replies (kb/learned/ticket-<id>.md) and owner
              Q&A answers (kb/learned/owner-qa-<ts>.md). Kept separate so the
              owner can review/prune what the system learned.
```

## Status of this content (read this)

- **`policies/` = DRAFT placeholders.** We do **not** yet have Chaim's real
  policy wording. Each policy file is marked `status: confirmed` in front-matter and
  shows a `> ⚠️ DRAFT — pending owner (Chaim) confirmation.` banner. The defaults
  are conservative and informed by how agents actually replied in the export, but
  Chaim must confirm specifics (return window, who pays return shipping, exact
  sale-season exchange rules, etc.) before these are trusted.
- **`faq/` = derived from real tickets** (`source: derived-from-tickets`). These
  capture the store's actual tone and real answers, scrubbed of PII. Still
  `status: confirmed` until confirmed, but higher-confidence than the policies.
- **`learned/` = grows by itself** once the feedback loop is wired (Stages 3–4).

## Hard rules (non-negotiable)

1. **No customer PII** in any file: no customer names, emails, phones, addresses,
   or order numbers. Generalize. (The store's own public contact details are fine
   where they are the actual answer.)
2. **Refunds, chargebacks, and disputes are escalation-only** — never
   auto-answered. See `policies/refunds-and-disputes.md`.
3. Follow `CONVENTIONS.md` exactly so retrieval and ingestion stay reliable.

## How to confirm a DRAFT file

1. Replace placeholder values with the real policy.
2. Remove the `> ⚠️ DRAFT …` banner line.
3. Set `status: confirmed` in the front-matter.
4. Commit. The worker re-ingests on the next push.
