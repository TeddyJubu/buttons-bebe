# Production deployment

`deploy-production.yml` runs only after the `verify` workflow succeeds for a
push to `main`. It streams a checksum-verified `git archive` to the dedicated
`buttons-deploy` SSH account, whose forced command can run only the receiver
installed at `/usr/local/sbin/buttonsbebe-deploy-receive`.

The receiver releases the tracked application source for `webhook/`,
`processor/`, `kb/`, `tools/`, `kb-admin/`, `whatsapp-connect/`, and
`console-src/index.html`. It preserves credentials, virtual environments,
WhatsApp session data, the webhook database, KB products, the live KB index,
and learning/notice data. A failed health check restores the pre-deploy source
backup and restarts the services.

Systemd and Caddy configuration are deliberately never copied by CD. Their
content fingerprints are root-approved in
`/etc/buttonsbebe-deploy-approved-config.sha256`; a release that changes either
configuration directory stops safely until the configuration is manually
reviewed, applied, and re-approved on the VPS. The approval file contains one
`<relative_path> <sha256>` entry per line, matching the paths consumed by
`assert_config_approved` (currently `deploy/systemd` and `deploy/caddy`).

Incoming archives are capped at 64 MiB and must contain only bounded regular
files and directories. Readiness checks retry slow starts and require the
WhatsApp bridge to report `connected`. After a successful release, the receiver
keeps the five newest release trees and five newest source backups.
