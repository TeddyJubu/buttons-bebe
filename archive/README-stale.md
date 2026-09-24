# Buttons Bebe / Hermes

Monorepo for the Buttons Bebe customer-support automation stack.

## Components

| Directory | Purpose |
|-----------|---------|
| `gorgias-webhook/` | Webhook receiver, draft engine, KB pipeline, classifier |
| `teddy/` | Teddy AI support agent |
| `shopify/` | Shared Shopify Admin API module |
| `kb-editor/` | Local KB markdown editor |
| `qa_v3/` | QA fixtures and comparison harness |

## Secrets

Never commit `config.json`, `.env`, databases, exports, or ticket data. See each subproject's `.gitignore`.

## Layout note

On the production VPS these directories live as siblings under `/root/`. After cloning, either keep the same layout or update path references in `gorgias-webhook/shopify_lookup.py`, `product_lookup.py`, and `teddy/agent.py`.
