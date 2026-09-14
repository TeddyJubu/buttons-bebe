# Gorgias HTTP Integration body — ticket state fields

The inbox at `https://support.buttonsbebe.com/inbox/` can only show a ticket's
status and assignment when the Gorgias webhook that created the observed event
carried them. Nothing in the pipeline calls the Gorgias REST API, and the
projection exporter holds no credentials, so `webhook_events.raw_payload` is the
only place those fields can come from.

Until the Integration body below is applied, every projected ticket is
`status: "unknown"` with no assignee, and the Assigned to me / Unassigned /
Snoozed / Closed / Trash / Spam views are honestly empty.

## 1. Edit the existing Integration body

In Gorgias: **Settings → Integrations → HTTP Integrations →** the integration
posting to `https://srv1766050.hstgr.cloud/webhook/gorgias/...`.

**Keep every existing key exactly as it is** — `trigger`, `ticket.id`,
`ticket.subject`, `ticket.customer`, `ticket.channel`, `message.id`,
`message.from_agent`, `message.sender`, `message.channel`,
`message.created_datetime`, the message body fields and `message.intents`.
Intake, dedupe and drafting all depend on them. Only **add** these keys inside
the existing `ticket` object:

```json
  "status": "{{ ticket.status }}",
  "snooze_datetime": "{{ ticket.snooze_datetime }}",
  "spam": "{{ ticket.spam }}",
  "trashed_datetime": "{{ ticket.trashed_datetime }}",
  "assignee_user": {{ ticket.assignee_user | tojson }},
```

Add only the keys the body is missing. On the integration live in September 2026,
`spam` was already being sent on every event and the other four were absent on
all 200 sampled events, so re-pasting `spam` would duplicate a JSON key. Check
with the presence query below before editing rather than assuming.

Notes on the renderings, all of which the parser already expects:

- Gorgias renders every scalar as a string. `spam` arrives as `"True"` /
  `"False"`, and an unset datetime arrives as `""` or `"None"`. The webhook
  parser normalizes these at ingest; an unset value is never stored as an
  observation.
- `assignee_user` may be sent as a JSON object (above) or, matching the quoting
  style the body already uses for `message.sender`, as a JSON-encoded string:
  `"assignee_user": "{{ ticket.assignee_user | tojson }}"`. Both are accepted.
- If `| tojson` on the whole object is awkward in your body, the flat form is
  enough — only `id` and `email` are read:

  ```json
  "assignee_user": {"id": "{{ ticket.assignee_user.id }}", "email": "{{ ticket.assignee_user.email }}"},
  ```

- If your Gorgias account exposes trash as a boolean rather than a datetime,
  send `"trashed": "{{ ticket.trashed }}"` instead; the parser accepts either.

Save the integration. Do not change the URL, the shared secret, or the trigger
list (`Ticket created`, `Ticket message created`).

## 2. Verify one new event carries the keys

Have a customer message land on any ticket (or send yourself a test message
through the same channel), then check **presence only** — this prints flags, not
customer content:

```sh
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" "
SELECT ticket_id,
       json_extract(raw_payload,'\$.ticket.status') IS NOT NULL   AS has_status,
       json_extract(raw_payload,'\$.ticket.snooze_datetime') IS NOT NULL AS has_snooze,
       json_extract(raw_payload,'\$.ticket.spam') IS NOT NULL     AS has_spam,
       (json_extract(raw_payload,'\$.ticket.trashed_datetime') IS NOT NULL
        OR json_extract(raw_payload,'\$.ticket.trashed') IS NOT NULL) AS has_trash,
       json_extract(raw_payload,'\$.ticket.assignee_user') IS NOT NULL AS has_assignee
FROM webhook_events ORDER BY received_at DESC LIMIT 1;"
```

All five flags must be `1`. If `has_assignee` is `1` but the inbox still shows
no assignment, confirm the nested address privately (it is the agent's address,
not a customer's, but treat it as private anyway):

```sh
sqlite3 "/root/Buttonsbebe Agent/webhook/data/webhook.db" "
SELECT coalesce(
         json_extract(raw_payload,'\$.ticket.assignee_user.email'),                     -- nested object form
         json_extract(json_extract(raw_payload,'\$.ticket.assignee_user'),'\$.email'))  -- tojson string form
FROM webhook_events ORDER BY received_at DESC LIMIT 1;"
```

Do not paste either output into a ticket, chat, or commit.

**There is no backfill.** A ticket keeps `status: "unknown"` until it receives a
new message event after this change. Re-running the exporter does not invent
state for older events; it only re-reads whatever each ticket's latest stored
payload already contained. That is deliberate — an invented status is worse than
an honest unknown.

## 3. Operator identity for "Assigned to me"

The exporter never decides who "me" is. The inbox service does, from
`INBOX_OPERATOR_GORGIAS_EMAIL` in
[`deploy/systemd/helpdesk-inbox.service`](systemd/helpdesk-inbox.service). It is
compared case-insensitively against the observed `assignee_user.email`, and an
empty or malformed value leaves Assigned to me permanently empty.

The committed unit carries `<REDACTED>`, matching the sibling drop-ins that hold
WhatsApp values: an operator address is host-local and is not kept in a public
repository. Put the real address in the applied unit on the host only.

```sh
systemctl show helpdesk-inbox --property=Environment | tr ' ' '\n' | grep INBOX_OPERATOR
systemctl daemon-reload && systemctl restart helpdesk-inbox
systemctl start buttonsbebe-inbox-projection.service   # refresh the snapshot
curl -fsS http://127.0.0.1:8766/ready
```

Editing the unit changes both fingerprints CD verifies, so re-record them in
`/etc/buttonsbebe-deploy-approved-config.sha256` or the next deploy stops at
`assert_config_approved` — see [the receiver contract](cd/README.md):

```sh
sha256sum /etc/systemd/system/helpdesk-inbox.service      # applied-file entry, path first
cd "$RELEASE/deploy/systemd" && find . -type f -print0 | LC_ALL=C sort -z \
  | xargs -0 sha256sum | sha256sum                        # `deploy/systemd` source entry
```

Then open `/inbox/`, use the filter control in the list header, and check the
view counts against Gorgias. Views backed by fields Gorgias has not sent yet
read `0`; that is the expected state until step 2 passes on real traffic.

This change adds no write path. The Send lock, the disabled bridge and the
read-only capability allowlist are untouched — see
[the operator runbook](PRODUCTION-OPERATOR-RUNBOOK.md) for the lock tests.
