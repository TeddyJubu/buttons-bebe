# 06 · Historical Known Issues & Repo Completeness

> ⚠️ **SUPERSEDED — DO NOT USE THIS FILE AS A CURRENT ISSUE REGISTER OR
> DEPLOYMENT RUNBOOK.** The repository-root `AGENTS.md` and `CLAUDE.md` are the
> current sources of truth. The earlier version of this file was based on a
> partial checkout and contained obsolete claims about production behavior and
> missing source code.

This short replacement records only the current corrections needed to keep the
historical handover from misleading a reader. Reassess every open issue against
the current code and live services before scheduling work.

## Current completeness snapshot

- Runtime source now exists in the repository: `webhook/`, `processor/`, and
  scrubbed Hermes skills under `hermes/skills/buttonsbebe/`.
- The console-action learning path and nightly promotion source are reviewable
  in `webhook/src/bb_webhook/learning.py` and `kb/scripts/auto_promote_learned.py`.
- Secrets, the SQLite queue, raw lessons, the generated product corpus, and the
  built LanceDB index are deployment data and intentionally remain untracked.
- The active Shopify catalog currently contains **4,018 products**. It is
  refreshed every three days and rebuilt transactionally.
- The retired `_VPS-FULL-BACKUP-20260706/` remains forensic reference only. It
  must never be treated as current source and must never be committed because it
  contains sensitive material.

## Current live behavior

| Component | Current behavior |
|---|---|
| Webhook → queue → processor | Live; source is in `webhook/` and `processor/`. |
| Hermes tools | Three read-only MCP tools: KB, Redo, and Gorgias reads. |
| Knowledge base | LanceDB hybrid search over policies, FAQs, 22 intents, tickets, and 4,018 active products. |
| Draft handling | Every processed ticket gets a console draft. Sensitive drafts are prefixed and elevated for human review. |
| Gorgias writes | Only human-triggered console **Send reply** and **internal Note** actions write to Gorgias. |
| Deterministic classifier | Implemented as an advisory, escalation-only safety net; Hermes also classifies risk. |
| Learning | Console actions create lessons; nightly promotion masks PII and rebuilds indexed exemplars. |
| Legacy feedback poller | Retired and fail-closed. It is not part of the live learning path. |

## Current limitations to track

1. Runtime configuration remains split between the main `.env` and
   `webhook/.env`; keep duplicated values aligned until configuration is
   consolidated.
2. PII masking is best-effort. Promoted exemplars remain reviewable and
   purgeable, and the promotion tests must stay in the release gate.
3. The deterministic classifier can only raise priority. Preserve that
   escalation-only behavior and continue testing its coverage.
4. Hermes runs with `--yolo`. This is acceptable only while installed skills
   and registered MCP tools remain strictly read-only.
5. Do not restore the retired poll-based feedback path or retired architecture
   from the full backup.

## Current verification source

Use the commands in root `CLAUDE.md`, plus the repository release gate:

```bash
bash tools/verify_release.sh
```

Production verification must include MCP connectivity, service health, queue
status, exact index/source parity, product-catalog parity, and a browser check of
the console Connections and KB views.

For any discrepancy, trust current code and observed live behavior over every
file in `HANDOVER/`.
