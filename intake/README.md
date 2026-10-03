# Offline ticket intake

An isolated first implementation of the ticket backend that will eventually
replace Gorgias. Read [the active checklist](../INTAKE-TASKLIST.md) before changing
it. This is a local test application, not production-ready helpdesk software.

## Run locally

From the repository root, with Python 3.12+ (standard library only):

```bash
python3 -m intake --workspace review user-set --username reviewer --name 'Reviewer' --role admin
python3 -m intake --workspace review serve --port 8891
```

Open `http://127.0.0.1:8891/` on the same machine. For a remote checkout, use an SSH
local port forward (for example, `ssh -L 8891:127.0.0.1:8891 your-server`) and open
that address locally. Do not expose this service through Caddy, a public tunnel or
a public bind address. Stop with Ctrl-C. There is no autostart or systemd unit.

The account command prompts for a new sandbox-only password. See [local users
and team workflow](TEAM.md) for roles, account changes and read state.

Normal startup creates an empty workspace; it never seeds or imports anything.
Choose a separate workspace name for each independent test dataset.

The [isolated Inbox](INBOX.md) is available at `http://127.0.0.1:8891/inbox/` on
the same server. It reuses the existing Inbox layout and connects directly to
this offline database. The intake lab at `/` retains import and proof tools.

The [cutover and rollback runbook](CUTOVER.md) describes the future migration
gates. The [proof and activation scope packet](READINESS.md) records the current
offline evidence and the remaining production requirements. Completing the
offline checklist does not activate live intake or sending.

## What works now

- Permanent independent ticket IDs and readable `BB-` numbers in normalized SQLite.
- Manual test tickets with private initial descriptions; internal notes.
- Native-ID assistance inputs, saved customer/order/return/product/knowledge
  context fixtures and explicit test suggestion review. Stale results and
  staff-only output are blocked from Use; no model runs. See [ASSISTANCE](ASSISTANCE.md).
- Local per-user sign-in, admin/agent/viewer roles, verified actors, individual
  read/unread state and shared assignment/claim/handoff. See [TEAM](TEAM.md).
- Shared status, priority and assignment, version checks and idempotent operations.
  Snoozed can reopen on a qualifying simulated customer reply; timed wake is future work.
- Search, pagination, conversation history, source references and audit events.
- Explicit Gorgias JSON preview/import with atomic writes, source-ID deduplication,
  conflict detection, retained original records and durable import reports.
- Source email headers and attachment metadata are retained; HTML is displayed
  only as text, and imported outgoing messages are labeled historical replies.
- Explicit offline message replay, header-based threading, duplicate suppression,
  reopen rules and separate review/spam/automatic queues. See [the replay contract](REPLAY.md).
- Locally signed fake channel captures, durable intake jobs with bounded retries,
  receipt observations, redacted monitoring and recovery holds. See the
  [channel design and fake adapter contract](CHANNELS.md). No live receiver exists.
- Reviewed and confirmed fake replies, durable delivery records, duplicate and
  stale-review protection, explicit receipt reconciliation and reviewed retries.
  See [fake delivery instructions](DELIVERY.md). No email is sent.
- Explicit local attachment-file import with checksums, private deduplicated
  storage, consistent backups and verified restore into new workspaces. See
  [attachment and recovery instructions](RECOVERY.md).
- Private source/import comparison packets for [owner review](OWNER-REVIEW.md),
  with separately recorded automated evidence and the accepted D7 sample decision.
- The existing Inbox shell connected in isolation: shared ticket creation, notes,
  details editing, conversation/search/queue views and reviewed fake replies.

## Offline boundary

- No provider clients, credentials, dotenv loader, live webhook handler, mailbox
  poller, real sending adapter, AI call, notification worker or production integration.
- No environment variable or UI switch enables those capabilities.
- The CLI installs a process-wide Python audit hook denying outgoing connections,
  DNS, datagrams and subprocess launches. This also blocks calls to live APIs on
  localhost. It is defense in depth, not an OS sandbox against malicious code.
- HTTP binds only `127.0.0.1`. Host/Origin checks, individual session cookies and
  per-session CSRF tokens protect API calls. Roles are enforced on the server. Do not use this server for shared production access.
- Browser assets are local. CSP disables remote images, fonts, frames and network
  calls. Imported bodies use text nodes; attachment URLs are never fetched, linked
  or rendered. There are no analytics or external fonts.
- No production files, services, release inventory or routing are changed by this
  application. `intake/` is outside the deployment allowlist.

## Data storage and handling

All runtime state is in `intake/.local/<workspace>/intake.sqlite3`, ignored by Git.
Directories are private (`0700`); databases are `0600`; the entry point uses umask
`077`. There is no configurable arbitrary database path. Unrecognized databases,
database symlinks/hardlinks and symlinked workspace directories are refused.

Place supplied real exports in ignored `intake/exports/`. Do not commit exports,
database copies or screenshots containing customer data. Raw source records are
retained inside the private database for reconciliation. API content is no-store;
request URLs and bodies are not logged. Only newly created local test passwords
are needed; production/provider credentials are never loaded.

This sandbox may hold personal data locally after an explicitly requested import.
Its private notes and edits never change the saved source record or live Gorgias.
Attachment bytes require an explicit local file bundle; source URLs are never
accessed. The current real export has metadata only. Synthetic file recovery is
verified, but real attachment fidelity remains unproven. Backups include all
database state and imported bytes; separate export files and reports are excluded.

## Supported export contract

Upload through **Import export**, or use the CLI. Accepts UTF-8 JSON as:

1. An array of ticket objects.
2. `{"tickets": [ticket, ...]}`.
3. `{"data": [ticket, ...], "meta": {"next_cursor": null}}`.

Each ticket requires `id`, `status` (`open`/`closed`), `created_datetime` (with a
timezone), and an explicit `messages` array (or complete `data` envelope).
Each message requires `id`, `public`, `from_agent`, and `created_datetime`.
`body_text` is preferred, otherwise `body_html` is converted to display text.
Unknown source fields, sender/receiver data and source headers are preserved in
private original JSON records. `urgent` priority maps to `critical` for the UI.

Nonempty `next_cursor`, mismatched `messages_count`, duplicate IDs within a file,
missing visibility flags, and malformed data reject the entire batch. Missing
`messages_count` is a warning: the importer cannot prove source completeness.
CSV summaries and ticket-list responses without full messages are unsupported.
Limits: 20 MiB, 2,000 tickets per file. Split a larger export on ticket boundaries.

The source-account name namespaces external IDs: use the same stable name for
every export from one Gorgias account. Different account names intentionally create
different records. Existing source IDs with changed content are reported as
conflicts, not overwritten; incremental mutable source synchronization is not yet
implemented. Use a fresh workspace to inspect a changed snapshot.

Preview is required before commit. The preview digest identifies the source
snapshot; commit revalidates it and current database contents inside one transaction.
Repeated imports and concurrent requests produce one set of records. Imports
cannot schedule delivery; the fake simulator requires a separate reviewed confirmation.

Synthetic example (contains no real customers):

```bash
python3 -m intake --workspace demo preview intake/fixtures/gorgias-synthetic.json --account synthetic-demo
python3 -m intake --workspace demo import intake/fixtures/gorgias-synthetic.json --account synthetic-demo --expected-digest DIGEST_FROM_PREVIEW
python3 -m intake --workspace demo serve --port 8891
```

The UI file picker can also load that synthetic file. The intake application has
no live export command. The separately authorized [read-only snapshot utility](../intake_export/README.md)
saves private local files through the existing MCP; the sandbox only reads the
saved files. It never imports that utility or gains provider access.

To verify a completed snapshot in a fresh offline workspace:

```bash
python3 -m intake.rehearse intake/exports/gorgias-TIMESTAMP --workspace export-proof --account buttonsbebe
```

This checks file hashes, imports with an interruption/rollback rehearsal,
compares original records and normalized fields, then reopens the database and
repeats every import to prove stable IDs and counts. It requires an empty
workspace. Private reports stay in `.local/<workspace>/reconciliation.json`.
Source pagination and optional source counts determine capture completeness;
matching the saved snapshot is not independent proof of whole-account completeness.

## Verification

```bash
bash intake/verify.sh
```

Browser integration verification uses an existing Playwright installation and
Chromium; it starts its own empty sandbox, imports only the synthetic fixture,
checks real backend behavior and writes synthetic screenshots in ignored storage:

```bash
PLAYWRIGHT_MODULE=/path/to/playwright node intake/tests/browser.mjs
```

The test run records a private test workspace so failures can be inspected. It
never opens the live support site. Read `INTAKE-TASKLIST.md` for outstanding
owner review, broader export and real-attachment coverage, authentication, AI
integration and future cutover gates.
