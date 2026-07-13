# Recovered live VPS snapshot

This source and deployment snapshot was recovered read-only from the live
Buttons Bebe VPS (`chaim`, host `srv1766050`) on 2026-07-13. No remote files,
services, credentials, products, or indexes were changed during recovery.

## What is versioned

- `webhook/`: the live FastAPI webhook receiver, queue, dashboard, and tests.
- `processor/`: the live job processor, Hermes runner, write path, and tests.
- `hermes/`: `SOUL.md`, the Buttons Bebe-specific Hermes skill, and a
  secret-redacted `config.example.yaml`.
- `kb/learn-nightly.sh` and `kb/scripts/auto_promote_learned.py`: the live
  nightly learning pipeline.
- `deploy/systemd/`: all thirteen `buttonsbebe-*` service/timer units and the
  two active drop-ins. Sensitive `Environment=` values are `<REDACTED>`.
- `deploy/caddy/Caddyfile.redacted`: the deployed Caddy routing snapshot. Its
  WhatsApp route token and console password hash are represented by
  `<WA_TOKEN>` and `<CONSOLE_PASSWORD_HASH>`.

`processor/whatsapp_notifier.py` is the HTTP notifier for the local Baileys
`whatsapp-connect` service. No Twilio client or configuration is retained.

## Intentionally excluded

- Every runtime `.env` file and backup. The versioned `webhook/.env.example`
  contains names and empty placeholders only.
- Hermes authentication, history, sessions, paste history, caches, logs,
  bundled runtimes, and non-project skills.
- Baileys/WhatsApp credentials and session state.
- SQLite databases, WAL/SHM files, customer/ticket data, logs, locks,
  virtualenvs, bytecode, generated package metadata, and backups.
- Shopify product exports and the generated LanceDB index. These must be
  regenerated through the product-sync/index pipeline; they must not be copied
  from production.

## Restore notes

The files containing `<REDACTED>` or angle-bracket placeholders are audit and
recovery references, not deployable secrets. Restore credentials through the
service's protected environment files or secret manager, validate the units
and Caddy configuration, and only then reload services. Never replace a live
configuration with a redacted snapshot verbatim.

During read-only validation, `systemd-analyze verify` reported that the live
processor unit's unquoted `PYTHONPATH` was split at the spaces in the project
path and ignored. The versioned processor unit quotes that assignment; the VPS
was deliberately not changed during this recovery phase. The deployed Caddyfile
validated successfully, with warnings only for redundant forwarded headers.
