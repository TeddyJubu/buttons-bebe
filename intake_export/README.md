# Explicit Gorgias test snapshot

This is a separate operator utility, **not part of the intake application**.
The owner authorized a read-only export on 2026-09-28. It uses only the existing
Gorgias MCP at the fixed loopback endpoint, verifies read-only tool annotations,
and permits only list tickets, get ticket and get ticket messages. It never loads
credentials, contacts Gorgias directly, fetches attachment/body URLs, or imports
anything into intake automatically.

```bash
python3 -m intake_export --tickets 50
```

The default is a bounded sample of recently updated tickets, not a whole-account
migration. Every message page is followed with loop/size limits; ticket detail is
read before and after capture to detect changes. Failed/changing conversations
remain explicit failures. Capture is not an atomic snapshot of the whole account.

Outputs go to a new private ignored `intake/exports/gorgias-<timestamp>/` directory:
one JSON file per ticket, original-detail evidence, and a checksum/count manifest.
Files are `0600`; directories are `0700`. Logs contain progress/counts only.
Headers and archived full-body availability depend on what the read-only provider
returns. Missing fields/content must be reported, never invented or fetched via
an unapproved URL. `body_url`/attachment URLs remain inert metadata.

Run the offline importer separately on those files. Neither the exporter nor its
network client is imported by `intake/`. No new service, poller or live channel is
created. Do not publish these outputs or commit customer data.
