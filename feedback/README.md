# feedback/ — the Buttons Bebe learning loop

Turns the human's real reply into knowledge the agent can reuse — **safely**.
Three moves: **capture → review → promote**. Nothing here messages a customer, and
nothing reaches the live KB without a human approving it.

## Why it's built this way (the one thing to understand)

The search engine **does not index `kb/learned/`** on purpose (`kb/scripts/kb_lib.py`).
So a captured reply sits inert until a human **promotes** it into `kb/tickets/`
(which is indexed, and required to be PII-free). That human step is the quality gate
*and* the PII gate. Automatic promotion is intentionally impossible.

## The pieces

| File | Job |
|---|---|
| `config.py` | env + KB paths + knobs (`FEEDBACK_*`). SHADOW by default. |
| `gorgias_read.py` | read-only Gorgias client (GET only — no writes exist). |
| `pairing.py` | find the (AI draft = internal note, human reply = public reply) pair; skip macros, multi-turn, empty, sensitive. |
| `text_clean.py` | strip glm self-commentary tails + de-dupe repeated answers. |
| `language.py` | flag non-English (e.g. Hebrew) so the similarity hint is suppressed. |
| `similarity.py` | difflib ratio as a **display hint only — never a gate**. |
| `pii.py` | PII **highlighter** (emails/phones/orders/addresses). Does **not** catch names. |
| `store.py` | SQLite: high-water-mark cursor + processed-ticket ledger (no double-writes). |
| `collector.py` | legacy capture: write `kb/learned/ticket-<id>.md` (review_pending); network polling is disabled by default. |
| `../kb/scripts/review_learned.py` | the human gate: review + promote to `kb/tickets/`. |
| `validate.py` | before/after retrieval check — the go-live proof. |

## Run it

Capture (read-only; safe to run repeatedly):

```bash
FEEDBACK_LEGACY_OPT_IN=1 python3 -m feedback.collector poll  # one bounded rollback pass
python3 -m feedback.collector             # show the ledger
```

Review + promote (the human gate):

```bash
python3 kb/scripts/review_learned.py list
python3 kb/scripts/review_learned.py show <ticket_id>
python3 kb/scripts/review_learned.py approve <ticket_id> --pii-cleared   # refuses without the flag
python3 kb/scripts/review_learned.py reject <ticket_id> [--purge]
# after editing the drafted exemplar(s) and setting status: confirmed:
python3 kb/scripts/review_learned.py reindex
```

Prove it helped before going live:

```bash
python3 -m feedback.validate "do you ship to canada" "778899"
```

## Config (`.env` / env vars)

```
FEEDBACK_ENABLED=shadow          # shadow | live  (stay shadow until validated)
FEEDBACK_BOT_EMAIL=              # the Gorgias user that posts AI internal notes — set this!
FEEDBACK_BOT_USER_ID=            # alternative to email
FEEDBACK_CAPTURE_MULTI_TURN=0    # v1 skips multi-message threads
FEEDBACK_MACRO_FILE=feedback/macro_signatures.txt
FEEDBACK_POLL_OVERLAP_SECONDS=120
FEEDBACK_KB_ROOT=/root/Buttonsbebe Agent/KB     # defaults to repo kb/
FEEDBACK_STATE_DB=/root/Buttonsbebe Agent/processor/feedback_state.db
FEEDBACK_LEGACY_OPT_IN=0             # required for the superseded poller
```

Set `FEEDBACK_BOT_EMAIL` to the agent's Gorgias identity — it makes "which internal
note is the AI draft" exact instead of a guess.

## Deploy on the VPS

1. Copy `feedback/` next to `processor/` under `/root/Buttonsbebe Agent/`, and
   `review_learned.py` into `KB/scripts/`.
2. Set the `FEEDBACK_*` vars in the main `.env`.
3. **Spike first (task 0):** `get_ticket_messages` on 2–3 resolved tickets and
   confirm the `public` / `from_agent` fields behave as assumed here. Adjust
   `pairing.is_internal_note` / `is_public_agent_reply` if the payload differs.
4. Do not add a systemd timer. The poller is superseded and fail-closed. If a
   rollback test is explicitly approved, run one bounded pass with
   `FEEDBACK_LEGACY_OPT_IN=1` and inspect `kb/learned/`.
5. Keep production learning on the console-action capture + nightly promotion.

## Do NOT flip CLAUDE.md §8 STUB→LIVE until

- capture verified on **10+ real tickets** across easy/hard paths (not 3), **and**
- `feedback/validate.py` shows a promoted exemplar is actually retrieved for its
  own question (M5). Plumbing passing is not proof the agent improved.

## Known limits (honest list)

- **Names are not masked** by `pii.py` — the human must read every promotion. Hebrew
  names especially won't match anything.
- **"Human derived from the draft" is unprovable** from data alone; the human gate
  is what catches from-scratch/off-topic replies. Similarity is only a hint.
- **Retrieval poisoning:** promoting many similar tickets can skew drafts. Cap
  promotions per topic and periodically review what `kb/tickets/` retrieves.
- Archived (rejected) packets still hold raw text — use `--purge` if PII-at-rest
  matters.
