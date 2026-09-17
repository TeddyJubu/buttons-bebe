# Knowledge-base conventions

The KB is read-only source material for Hermes. Hermes uses it to ground a
draft; it never sends a customer reply or changes Shopify, Gorgias, or a
return. A human-confirmed console action remains the only customer-facing
write.

## Indexed folders

The authoritative folder list and ranking map live in
`scripts/kb_lib.py` as `CONTENT_FOLDERS` and `CATEGORY_WEIGHT`. Keep the
frontmatter `category` aligned with the containing folder.

| Folder | Contents | Search weight |
|---|---|---:|
| `intents/` | Curated question-and-answer intent guidance | 1.00 |
| `faq/` | Curated frequently asked questions | 1.00 |
| `policies/` | Buttons Bebe store policy and escalation rules | 1.00 |
| `tickets/` | Anonymized, curated solved-ticket exemplars | 1.00 |
| `products/` | Auto-synced Shopify catalog facts | 0.85 |
| `shopify/` | Shopify platform background, not store policy | 0.70 |

`learned/` is a review holding area and is not indexed. `README.md` files and
files whose names start with `_` are also skipped. If a platform explanation
and store policy appear to conflict, the store policy governs the draft.

## File format

Each indexed Markdown file starts with YAML frontmatter:

```yaml
---
title: Short human-readable title
category: policies
status: confirmed
tags: [shipping]
---
```

Use level-two headings for searchable sections:

```markdown
## What the customer needs to know

Self-contained guidance for this topic.
```

Each `##` section becomes one search chunk. Put all safety boundaries and
human-action guards needed for a section inside that section; do not rely on a
different chunk to supply them. Text before the first `##` is treated as
preamble and is not included when a file has headed sections. A file with no
level-two headings is indexed as one chunk.

After adding or editing content, run `./update.sh` from this folder to rebuild
the validated local index.

## Concurrency conventions (deliberate — do not "harmonize")

Markdown edits (kb-admin saves, nightly promotion) are lock-free by design:
they publish via atomic rename, and name collisions fail loudly instead of
overwriting. Indexers take `flock` on `.index_kb.lock` /
`.index_kb.promote.lock`. The Notice Board uses a directory-mkdir lock with a
60-second stale takeover because it must interoperate with Node. A save that
lands mid-index is picked up by the next reindex — the console's "Saved —
re-index to apply" message is the accepted workflow, not a bug.
