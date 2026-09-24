# Buttons Bebe — AI support agent

Hermes drafts customer-support replies for the Buttons Bebe Shopify store;
humans review and send from the console. Hermes never sends and never writes
to Gorgias.

## Start here

| Doc | What |
|---|---|
| **[AGENTS.md](AGENTS.md)** | Sole root source of truth — architecture, safety model, services, verify gate |
| [RETIRED.md](RETIRED.md) | Paths that must stay dead (plus archive pointers) |
| [archive/](archive/) | Pre-rebuild / superseded trees and docs kept for archaeology only |

## Live packages (short)

`webhook/` · `processor/` · `kb/` · `tools/` · `kb-admin/` · `whatsapp-connect/` · `console-src/` · `testing/` · `deploy/`

## Verify before merging to `main`

```bash
bash tools/verify_release.sh
```

A push to `main` that passes CI **auto-deploys to production**. Never commit
`.env*` or anything from `_VPS-FULL-BACKUP-*/`.
