# feedback/ — Buttons Bebe learning safety helpers

The live learning flow is **console capture → nightly masking/promotion → KB
rebuild**. Nothing in this package sends a customer message or writes to
Gorgias.

## Live path

1. A human clicks Send, internal Note, or Request edit in the console.
2. `webhook/src/bb_webhook/learning.py` creates a unique mode-0600
   `KB/learned/lesson-*.md` packet and updates `_ledger.json` under a lock.
3. `buttonsbebe-kb-learn.timer` runs at 03:30 UTC.
4. `KB/scripts/auto_promote_learned.py` masks identifier patterns and the known
   Gorgias customer name, writes a distinct confirmed
   `KB/tickets/exemplar-learned-*.md`, and archives the raw lesson.
5. `KB/learn-nightly.sh` rebuilds the validated index.

`KB/learned/` is never indexed. Promoted exemplars are searchable because they
live in `KB/tickets/` with `status: confirmed` and `source: learned-auto`.

## Current helpers

| File | Purpose |
|---|---|
| `pii.py` | Best-effort masking for emails, phones, orders, cards, tracking numbers, addresses, postal codes, URLs, known names, and greeting-shaped first names. |
| `config.py` | Shared KB/learned/archive paths and legacy configuration. |
| `text_clean.py` | Remove model commentary and repeated text. |
| `language.py` | Language detection hints. |
| `similarity.py` | Display-only similarity scoring. |

PII masking is deliberately described as best-effort, not anonymous-by-proof.
Operators should review and purge any unexpected personal information in an
exemplar.

## Retired rollback path

The old poll-based `feedback.collector`, pairing logic, and
`processor/feedback_collector.py` are retained only for rollback investigation.
They are disabled by default and are not used by the processor. Do not enable
them as a second production learning path; doing so can duplicate or mis-pair
lessons.

For current verification, run:

```bash
python3 -m unittest feedback.tests.test_all feedback.tests.test_retirement -v
python3 -m unittest discover -s kb/tests -v
```
