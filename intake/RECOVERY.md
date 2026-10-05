# Private attachments and recovery (D6)

These commands operate only on saved local files and private sandbox databases.
They run under the same outgoing network, DNS and subprocess denial as the rest
of intake. Nothing downloads attachment URLs, delivers mail or changes Gorgias.

## Import saved attachment files

The JSON ticket importer preserves attachment metadata. File bytes require a
separate, explicit local bundle. Start by saving a private attachment index:

```bash
python3 -m intake --workspace review attachment-index
```

The command prints counts and the path of a private index inside the workspace.
Use its attachment IDs to match files to their original message metadata. Keep
this index private: it contains source filenames and identifiers. Matching a
checksum proves the supplied file was preserved; it does not independently prove
that an operator selected the right source file.

Place the saved bytes and a `manifest.json` in a private, Git-ignored directory,
such as `intake/exports/local-attachment-bundle/`. Use plain local filenames:

```json
{
  "mode": "offline_attachment_import",
  "files": [
    {
      "attachment_id": "ID-FROM-PRIVATE-INDEX",
      "file": "payload.bin",
      "sha256": "REPLACE-WITH-LOWERCASE-SHA256-OF-PAYLOAD"
    }
  ]
}
```

Compute the SHA-256 locally, for example with `sha256sum` on the supplied payload.
Protect bundle directories with mode `0700` and files with `0600`. Preview, then
use the exact returned digest to commit:

```bash
python3 -m intake --workspace review attachment-preview intake/exports/local-attachment-bundle
python3 -m intake --workspace review attachment-import intake/exports/local-attachment-bundle --expected-digest DIGEST_FROM_PREVIEW
```

Preview validates files and mappings without importing them. Commit repeats those
checks and atomically stores bytes, attachment links, a batch report and audit
events. The recorded source size is also checked when available. A changed bundle,
unknown attachment ID, wrong checksum or conflicting existing copy rejects the
whole batch. Repeating the same bundle changes no rows. Identical bytes share one
SHA-256-addressed blob; separate attachment IDs keep their original relationships.

Limits: 25 files per bundle, 25 MiB per file, 100 MiB per bundle and a 1 MiB JSON
manifest. Paths, URLs, symbolic/hard links, FIFOs and device files are refused.
The manifest contains no download or execution instructions.

The UI shows **Private copy saved** with its byte count. Bytes stay opaque inside
SQLite: no image rendering, MIME parsing, file scanning or browser download route.
Original metadata and source records remain intact. To recover one file locally:

```bash
python3 -m intake --workspace review attachment-copy --attachment-id ID_FROM_INDEX --name recovered.bin
```

The command verifies the stored checksum and writes a new `0600` file under
`intake/.local/review/copies/`. It never overwrites or opens a file. Imported file
availability advances the ticket revision, so any earlier reply review is stale.

## Create and verify a backup

```bash
python3 -m intake --workspace review backup --name checkpoint-1
python3 -m intake --workspace review backup-verify --name checkpoint-1
```

The backup lives at `intake/.local/review/backups/checkpoint-1/`. It contains only
`intake.sqlite3` and a private `manifest.json`. SQLite's online backup API captures
a consistent committed snapshot, including committed WAL data; the resulting
backup needs no WAL/SHM sidecars. Concurrent writers wait during the snapshot.

Validation checks the database identity/version, integrity, foreign keys, expected
schema objects/columns/index definitions, attachment hashes and delivery-ledger
relationships. The manifest records counts and a SHA-256 for the database. A
completed backup is published by directory rename after validation and file
syncs. Existing backup names are refused. Failed staging work is never presented
as a completed backup. A hard process termination may leave a private staging
directory; verification and restore accept only completed named backups.

Backups include tickets, messages, original source records, notes, edits, import
reports stored in SQLite, attachment blobs and links, audit events, replay
receipts, reply reviews, delivery attempts and fake acceptance/rejection evidence.
They do not include the separate source export files, JSON rehearsal reports,
attachment indexes, extracted file copies or screenshots. Retain those separately
when needed. The supported database size is at most 512 MiB.

## Restore into a new workspace

Use the digest printed by `backup-verify`, and choose a workspace that does not
already exist:

```bash
python3 -m intake --workspace recovered-review restore --from-workspace review --backup checkpoint-1 --expected-digest DIGEST_FROM_BACKUP_VERIFY
python3 -m intake --workspace recovered-review verify-workspace
python3 -m intake --workspace recovered-review serve --port 8891
```

Restore does not require the source's working database, only the completed backup
directory. It checks the expected manifest digest, copies and verifies the
database in private staging, then publishes the new workspace. Corrupt, missing,
linked or incomplete files are refused. Existing workspaces are never replaced.
Stop any test server using port 8891 before starting another there.

All business rows and IDs are preserved at the checkpoint. For schemas 5–7, users,
password hashes, assignments and per-user read watermarks are preserved; sessions
and login throttle records are deleted. Sign in again after restore. Historical
schema-4/5/6 backups remain supported. Schema 6 also preserves assistance fixtures,
inputs, outcomes and linked reviews; old suggestions are unusable after recovery
until explicitly rerun against current context. See [ASSISTANCE](ASSISTANCE.md). Restore adds recovery
provenance and a new review generation to sandbox metadata. **Unconfirmed reviews
from before recovery cannot be confirmed:** create a fresh review against current
context. Existing attempts retain their IDs and can still be inspected or
explicitly reconciled. A recorded fake acceptance can recover its outgoing
message once; missing evidence remains uncertain and blocks retry. Restoring does
not dispatch anything or automatically reconcile an attempt.

Schema 7 also preserves signed fake captures, intake jobs and delivery
observations. Restore revokes fake signing keys, fences old leases and holds
both new admission and job processing. Inspect `jobs-health`, `verify-workspace`
and the `jobs-resume` plan before explicitly resuming with its digest. Pending
jobs retain their captured evidence; new fixtures require new generated keys.
See [CHANNELS](CHANNELS.md) for the exact local recovery contract. Older restores
also carry the hold, enforced when upgraded to E4.

Activity after the checkpoint is absent from the restored copy. Recovery does
not establish one sending owner across independent copies or real providers;
that belongs to the future production recovery/cutover design. All workspaces
remain offline. These local backups have filesystem privacy, but no encryption,
offsite durability or cryptographic authenticity against deliberate tampering.

## Verified rehearsal and remaining gap

```bash
python3 -m intake.recovery_rehearse --source-workspace gorgias-delivery-proof-20260928-v3 --workspace NEW_RECOVERY_PROOF_NAME
```

The rehearsal clones the saved-data workspace, upgrades only the clone, adds
separate synthetic attachment fixtures, backs up and restores, rejects a corrupt
copy, compares every non-metadata table, checks recovered file bytes and repeats
delivery confirmation/reconciliation across restart. It keeps the original source
database unchanged and saves `recovery-reconciliation.json` privately. Both the
proof name and its `-restored` suffix must be unused (at most 48 characters).

The completed rehearsal passed **95 assertions** against the D5 workspace with
50 saved tickets and 211 original source messages. All 261 original source
records were preserved. Two added synthetic attachment references shared one
40-byte blob and recovered byte-for-byte. The 11 original unresolved delivery
attempts stayed blocked; an additional synthetic accepted-but-timed-out attempt
recovered exactly one message without another dispatch.

**The saved export contains 22 real attachment references and no real attachment
bytes.** Those references remain metadata-only. This proves the local file and
recovery mechanism with synthetic bytes, not customer attachment completeness or
fidelity. Representative owner review and broader source coverage remain D7 and
later work; nothing in D6 authorizes live operation.
