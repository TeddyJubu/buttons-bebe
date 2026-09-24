# KB File Conventions — the chunk contract

This file is the **authoritative format spec** for every Markdown file under
`kb/`. `kb_client.py` (Stage 1, Task 3) and the future `ingestion_worker.py`
(Stage 4, Task 12) both depend on this contract, so it must be followed exactly
and consistently in every seed file. If you change the format here, update both
of those consumers.

See `PHASE1_KB_ARCHITECTURE.md` for the surrounding system design.

---

## 1. Every file is: YAML front-matter + `##` sections

```
---
title: Return & Exchange Policy
category: policies
status: confirmed            # DRAFT until owner (Chaim) confirms; else "confirmed"
source: derived-from-tickets   # OPTIONAL — see §3
tags: [returns, exchanges, final-sale]
---

Optional short intro paragraph (NOT a retrievable chunk on its own; treated as
file-level preamble).

## First section heading

Body text for the first chunk.

## Second section heading

Body text for the second chunk.
```

- The file **must begin** with a YAML front-matter block delimited by `---` on
  its own line, top and bottom. No blank lines before the opening `---`.
- After the front-matter, content is split into **chunks by `##` (level-2)
  headings**. Each `##` heading and the body text beneath it (up to the next
  `##` or end of file) is **one retrievable chunk**.
- Use the level-1 `#` title sparingly or not at all — the canonical title is the
  `title:` front-matter field, not a `#` heading. (Seed files omit `#` to avoid
  ambiguity.)
- Use `###` (level-3) freely **inside** a section for sub-structure; `###` does
  **not** start a new chunk — it stays part of its parent `##` chunk.

## 2. Front-matter fields

| Field      | Required | Meaning |
|------------|----------|---------|
| `title`    | yes      | Human title; becomes chunk-source metadata. |
| `category` | yes      | One of: `policies`, `faq`, `tickets`, `learned`. Mirrors the folder. |
| `status`   | yes      | `DRAFT` (unverified — placeholder pending owner) or `confirmed`. |
| `source`   | no       | Provenance. Use `derived-from-tickets` for anything mined from real exports; `agent_reply` / `owner_qa` are reserved for files the write-back loop creates under `kb/learned/`. |
| `tags`     | no       | YAML list, lowercase, hyphenated. Used for filtered retrieval. |

Reserved fields the **write-back loop** (Stage 4) will add under `kb/learned/`,
documented here so nothing collides: `ticket_id`, `source_type`,
`review_pending`, `created_at`. Do not use them in hand-authored seed files.

## 3. `status` and `source` rules (important for Phase 1)

- We do **not** have the owner's real policy text yet. Every hand-written policy
  is therefore `status: confirmed` and carries a visible
  `> ⚠️ DRAFT — pending owner (Chaim) confirmation.` line as the first line of
  the body. Chaim flips `status: confirmed` → `status: confirmed` and removes the
  banner once he validates the text.
- FAQ entries **mined from the real ticket exports** carry
  `source: derived-from-tickets`. They reflect what agents actually did, so they
  are more trustworthy than the placeholder policies, but they are still
  `status: confirmed` until Chaim confirms wording/edge cases.

## 4. Chunking guidance (so retrieval is good)

- **One topic per `##` section.** A reader (or the LLM) should be able to answer
  one question from one chunk without needing a sibling chunk.
- Keep sections roughly 40–200 words. Split a long policy into several `##`
  sections rather than one giant section.
- Put the **answer first**, context after. Retrieval favors self-contained
  chunks.
- Repeat a key noun in the heading and body (e.g. heading "Exchanges" + body
  says "exchange") so keyword + semantic ranking both hit.

## 5. Hard content rules

- **NO customer PII, ever.** No real names, emails, phone numbers, street
  addresses of customers, or order numbers in any committed KB file. Generalize.
  (The store's own public business contact info is allowed where it is genuinely
  the answer — e.g. the public store phone for "can I order by phone".)
- **Refunds / chargebacks / disputes are escalation-only.** Any chunk touching
  these must state they are never auto-answered — they route to a human (see
  `kb/policies/refunds-and-disputes.md`).
- Conservative defaults only. When the real policy is unknown, say so in the
  chunk and keep `status: confirmed`.

## 6. File naming

- Lowercase, hyphenated, descriptive: `return-and-exchange-policy.md`,
  `faq-shipping.md`.
- `kb/learned/` files are named by the write-back loop:
  `ticket-<id>.md`, `owner-qa-<timestamp>.md` (created later — not by hand now).

## 7. Stable doc id

The ingestion worker uses the **repo-relative path** (e.g.
`kb/policies/shipping-policy.md`) as the stable Supermemory doc id. Do not rename
files casually once ingested — a rename is delete-old + insert-new in Supermemory
(`git diff` status `R`).
