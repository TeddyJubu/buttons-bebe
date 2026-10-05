"""Prepare a private, inert source/import comparison for actual owner review."""
import argparse
from collections import Counter
from html import escape
import json
import os
from pathlib import Path
import re
import sqlite3

from .mail import metadata
from .policy import Invalid, install_offline_guard
from .private_files import private_write
from .records import canonical, digest, now
from .recovery import workspace_path
from .rehearse import read_snapshot, rehearse, utc
from .store import Store


WORKFLOWS = [
    ('Keep conversations separate', 'Source IDs and email headers identify conversations. '
     'A shared subject or customer alone does not merge tickets. Missing or ambiguous evidence needs review.'),
    ('Reopen on a new customer reply', 'A qualifying newer simulated reply reopens closed, waiting or snoozed Inbox work. '
     'Delayed history, staff echoes, spam and automatic responses do not reopen it. Timed unsnooze is not built.'),
    ('Keep staff work local', 'Manual test tickets, notes, status and assignment edits persist in the sandbox. '
     'They do not update the saved original records or Gorgias. Simultaneous stale edits are refused.'),
    ('Review before every simulated reply', 'Sender and recipient come from saved evidence. '
     'Review freezes the exact reply, then a separate confirmation records the simulation. '
     'Changed tickets require a fresh review; repeated confirmation cannot duplicate a recorded reply.'),
    ('Block uncertain delivery', 'Unknown acceptance blocks another attempt, including after restart. '
     'Only a recorded rejection permits a newly reviewed retry. A stored fake acceptance can be reconciled once.'),
    ('Recover into a new workspace', 'Backups preserve stored history, files and delivery evidence at the checkpoint. '
     'Restore requires a new workspace and invalidates old unconfirmed reviews. Post-checkpoint activity is absent.'),
]

FUTURE_GAPS = [
    'Real authentication, team permissions, AI/context integration, channel adapters, outgoing attachments, '
    'CC/BCC and alternate-recipient routing remain future work.',
    'Backups are local and unencrypted; offsite recovery and one sending owner across restored copies remain future work.',
]


def coverage(records):
    messages = [m for r in records for m in r['source']['messages']]
    missing_headers = sum(not metadata(m)['rfc_id'] or bool(metadata(m)['header_issue']) for m in messages)
    withheld = sum(not r['ticket']['reply_context']['available'] for r in records)
    attachments = sum(len(m.get('attachments') or []) for m in messages)
    notes = sum(not m['public'] for m in messages)
    gaps = [
        f'Only {len(records)} saved tickets are covered; this is not a whole-account migration.',
        f'{attachments} attachment references have no imported file bytes. File recovery has separate synthetic coverage.',
        f'{notes} private source notes are present. Spam/trash and larger samples still need independent coverage review.',
        f'{missing_headers} source messages lack usable email identifiers/headers; {withheld} conversations are withheld from reply simulation.',
        f'The largest saved thread has {max(len(r["source"]["messages"]) for r in records)} messages. '
        f'{sum(r["source"].get("messages_count") is None for r in records)} tickets lack a source messages_count. '
        'Saved pagination exhaustion is not an independent total or proof of multi-page long-thread coverage.',
    ]
    return gaps + FUTURE_GAPS


def choose_cases(records):
    """Deterministic structural sampling, without inventing semantic case labels."""
    selected = {}
    def add(row, reason):
        selected.setdefault(row['ticket']['id'], {'record': row, 'reasons': []})['reasons'].append(reason)
    def pick(label, predicate, key=lambda r: len(r['source']['messages']), largest=False):
        candidates = [r for r in records if predicate(r)]
        if candidates:
            add(sorted(candidates, key=key, reverse=largest)[0], label)

    pick('Longest saved conversation', lambda r: True, largest=True)
    pick('Open ticket with usable reply evidence', lambda r: r['ticket']['status'] == 'open' and r['ticket']['reply_context']['available'])
    pick('Closed conversation with historical staff replies', lambda r: r['ticket']['status'] == 'closed'
         and any(m['from_agent'] and m['public'] for m in r['source']['messages']))
    pick('Attachment metadata', lambda r: any(m.get('attachments') for m in r['source']['messages']),
         key=lambda r: sum(len(m.get('attachments') or []) for m in r['source']['messages']), largest=True)
    pick('Missing or unusable latest-message evidence', lambda r: not r['ticket']['reply_context']['available']
         and 'headers' in r['ticket']['reply_context']['reason'])
    pick('Self-addressed incoming message', lambda r: any(
         (lambda info: info['sender'] and info['sender'] in info['recipients'])(metadata(m))
         for m in r['source']['messages'] if not m['from_agent'] and m['public']))
    for field, label in [('subject', 'Same subject, separate tickets'), ('customer', 'Same customer, separate tickets')]:
        groups = {}
        for row in records:
            value = row['source'].get(field)
            if field == 'customer':
                value = (value or {}).get('id')
            if value:
                groups.setdefault(str(value), []).append(row)
        for group in groups.values():
            if len(group) > 1:
                for row in group[:2]:
                    add(row, label)
                break
    return list(selected.values())


def extra_checks(records):
    counts, failures = Counter(), Counter()
    def check(label, condition):
        counts[label] += 1
        if not condition:
            failures[label] += 1
    for row in records:
        source, ticket = row['source'], row['ticket']
        customer = source.get('customer') or {}
        check('displayed_contact_name', ticket['name'] == (customer.get('name') or 'Unknown contact').strip())
        check('displayed_contact_email', ticket['email'] == (customer.get('email') or '').strip())
        check('displayed_message_count', len(ticket['messages']) == len(source['messages']))
        check('chronological_display', [utc(m['created_at']) for m in ticket['messages']] ==
              sorted(utc(m['created_datetime']) for m in source['messages']))
        check('historical_messages_not_simulations', all(m['origin'] == 'gorgias_export' for m in ticket['messages']))
        check('no_simulated_deliveries', ticket['deliveries'] == [])
    return {'checks': dict(counts), 'failures': dict(failures), 'passed': not failures}


def text(value):
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    return escape(value, quote=True)


def comparison(label, source, imported):
    return '<section class="comparison"><h4>' + text(label) + '</h4><div class="columns">' + \
        '<div><small>Saved source</small><pre>' + text(source) + '</pre></div>' + \
        '<div><small>Imported workspace</small><pre>' + text(imported) + '</pre></div></div></section>'


def fenced(value):
    """Keep customer Markdown/HTML inert even when it contains backtick fences."""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2)
    fence = '`' * max(3, 1 + max((len(run) for run in re.findall(r'`+', value)), default=0))
    return fence + 'text\n' + value + '\n' + fence + '\n'


def render_markdown(cases):
    parts = ['# Private D7 conversation comparison\n\nCustomer information — keep this document local. '
             'This review does not change data or record acceptance. Source and imported fields are shown below. '
             'Leading/trailing text whitespace is trimmed, dates use UTC and internal IDs differ from source IDs.\n']
    for i, chosen in enumerate(cases, 1):
        row = chosen['record']
        source, ticket = row['source'], row['ticket']
        parts += [f'## Case {i}\n', '; '.join(chosen['reasons']) + '\n',
                  '### Saved ticket fields\n', fenced({k: source.get(k) for k in ('id', 'subject', 'status', 'priority', 'assignee_user', 'tags', 'customer', 'created_datetime', 'updated_datetime')}),
                  '### Imported ticket fields\n', fenced({k: ticket[k] for k in ('id', 'number', 'subject', 'status', 'priority', 'assignee', 'tags_json', 'name', 'email', 'created_at', 'updated_at')}),
                  '### Reply eligibility\n', fenced(ticket['reply_context'])]
        by_id = {m['id']: m for m in ticket['messages']}
        for mi, (raw, local_id) in enumerate(sorted(row['messages'], key=lambda pair: utc(pair[0]['created_datetime'])), 1):
            message = by_id[local_id]
            parts += [f'### Case {i} · Message {mi}\n', 'Saved source message fields:\n',
                      fenced({k: raw.get(k) for k in ('id', 'public', 'from_agent', 'sender', 'receiver', 'source', 'created_datetime', 'channel')}),
                      'Imported message fields:\n', fenced({k: message[k] for k in ('id', 'kind', 'author_name', 'author_email', 'created_at', 'channel', 'origin')}),
                      'Saved source text:\n', fenced(raw.get('body_text') if raw.get('body_text') is not None else raw.get('body_html', '')),
                      'Imported text:\n', fenced(message['body']),
                      'Saved source headers:\n', fenced(raw.get('headers') or {}),
                      'Imported headers:\n', fenced(json.loads(message['headers_json'])),
                      'Saved source attachment metadata:\n', fenced(raw.get('attachments') or []),
                      'Imported attachment metadata:\n', fenced(message['attachments'])]
    return '\n'.join(parts)


def render(cases, facts):
    cards = []
    for index, chosen in enumerate(cases, 1):
        row = chosen['record']
        source, ticket, mapped = row['source'], row['ticket'], row['messages']
        source_fields = {k: source.get(k) for k in ('id', 'subject', 'status', 'priority', 'assignee_user', 'tags', 'customer', 'created_datetime', 'updated_datetime')}
        local_fields = {k: ticket[k] for k in ('id', 'number', 'subject', 'status', 'priority', 'assignee', 'tags_json', 'name', 'email', 'created_at', 'updated_at')}
        content = comparison('Ticket fields', source_fields, local_fields)
        context = ticket['reply_context']
        content += '<h4>Reply eligibility</h4><pre>' + text(context) + '</pre>'
        by_id = {m['id']: m for m in ticket['messages']}
        for mi, (raw, local_id) in enumerate(sorted(mapped, key=lambda pair: utc(pair[0]['created_datetime'])), 1):
            message = by_id[local_id]
            source_meta = {k: raw.get(k) for k in ('id', 'public', 'from_agent', 'sender', 'receiver', 'source', 'created_datetime', 'channel')}
            local_meta = {k: message[k] for k in ('id', 'kind', 'author_name', 'author_email', 'created_at', 'channel', 'origin')}
            body = comparison('Message fields', source_meta, local_meta)
            body += comparison('Message text', raw.get('body_text') if raw.get('body_text') is not None else raw.get('body_html', ''), message['body'])
            body += comparison('Headers', raw.get('headers') or {}, json.loads(message['headers_json']))
            body += comparison('Attachment metadata', raw.get('attachments') or [], message['attachments'])
            content += '<details class="message"><summary>Message ' + str(mi) + ' · ' + text(message['kind']) + ' · ' + text(message['created_at']) + '</summary>' + body + '</details>'
        cards.append('<article id="case-' + str(index) + '"><h2>Case ' + str(index) + '</h2><p class="reason">' +
                     text(' · '.join(chosen['reasons'])) + '</p><h3>' + text(ticket['subject']) + '</h3><p>' +
                     str(len(ticket['messages'])) + ' saved messages · ' + text(ticket['status']) +
                     '</p><p>Review the participants, conversation order, message text and reply eligibility. '
                     'Report any mismatch by case and message number.</p><details><summary>Compare this conversation</summary>' + content + '</details></article>')
    nav = ''.join('<a href="#case-' + str(i) + '">Case ' + str(i) + '</a>' for i in range(1, len(cases) + 1))
    workflows = ''.join('<li><strong>' + text(title) + '</strong><p>' + text(body) + '</p></li>' for title, body in WORKFLOWS)
    gaps = ''.join('<li>' + text(gap) + '</li>' for gap in facts['limitations'])
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'none'; img-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Private D7 conversation review</title><style>
*{box-sizing:border-box}body{margin:0;background:#f4f1ea;color:#292820;font:16px/1.55 system-ui,sans-serif;overflow-wrap:anywhere}
header,main{max-width:1180px;margin:auto;padding:28px}header{padding-bottom:8px}h1{font-size:32px;line-height:1.2}
article,.intro{background:#fffdf9;border:1px solid #d9d3c7;border-radius:10px;padding:24px;margin:20px 0}
.badge{display:inline-block;background:#e8ddc4;border-radius:6px;padding:6px 12px;font-weight:650}
nav{display:flex;gap:12px;flex-wrap:wrap}a{color:#49482b}.columns{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.columns>div{min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f6f3ed;padding:14px;border-radius:5px;font:13px/1.5 ui-monospace,monospace}
summary{cursor:pointer;font-weight:650;padding:12px 0}.message{border-top:1px solid #ddd5c8}h4{margin-bottom:8px}
small,.reason{color:#685f50}li{margin-bottom:12px}h2{margin-top:0}.note{border-left:3px solid #a56c39;padding-left:16px}
@media(max-width:700px){header,main{padding:16px}.columns{grid-template-columns:1fr}article,.intro{padding:16px}h1{font-size:26px}}
</style></head><body><header><span class="badge">Private saved-data review · Offline</span>
<h1>D7: review the imported conversations</h1><p>This file contains customer information. Keep it private.
It is an inert local comparison with no scripts, remote content or submission controls.</p></header><main>
<section class="intro"><h2>What you are reviewing</h2><p>''' + text(str(facts['tickets']) + ' tickets / ' + str(facts['messages']) + ' messages were checked against the saved export. '
        + str(facts['automated_checks']) + ' checks passed. The ' + str(len(cases)) + ' cases below are selected for structural coverage, not as a random sample.') + '''</p>
<p>Start with ticket fields, then expand messages to compare saved text, authors, visibility, dates, headers and attachments.
Source IDs map to new internal IDs; timestamps display in UTC; leading/trailing text whitespace is trimmed.
An original urgent priority maps to critical. Recipient/source fields remain in the retained original records;
reply eligibility below shows the current derived envelope.</p>
<p class="note"><strong>Decision pending.</strong> Automated verification is complete. Your acceptance of the displayed
sample and workflows is still needed. An acceptance applies only to this offline sample with the gaps below;
it does not activate live intake, sending or deployment.</p><nav>''' + nav + '</nav></section>' + ''.join(cards) + \
        '<article><h2>Workflow review</h2><ol>' + workflows + '</ol></article><article><h2>Unresolved coverage</h2><ul>' + gaps + \
        '</ul></article><article><h2>Return your decision in the chat</h2><p>Use “Accept D7 for the offline sample with the listed gaps”, '
        'or identify the case, message or workflow that needs changing. Owner acceptance has not been recorded.</p><p>Packet digest: <code>' + \
        facts['packet_digest'] + '</code></p></article></main></body></html>')


def prepare(snapshot, workspace, account):
    destination = workspace_path(workspace)
    if destination.exists():
        raise Invalid('Owner review requires a new workspace; previous evidence is never replaced.')
    manifest, payloads = read_snapshot(snapshot)
    report, _ = rehearse(snapshot, workspace, account)
    if not report['passed']:
        raise Invalid('Source reconciliation failed. Owner acceptance cannot proceed.')
    store = Store(destination)
    records = []
    with store.connection() as db:
        for payload in payloads:
            for source in json.loads(payload)['tickets']:
                tid = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND account=? AND external_id=?", (account, str(source['id']))).fetchone()[0]
                messages = [(raw, db.execute("SELECT internal_id FROM source_records WHERE kind='message' AND account=? AND external_id=?", (account, str(raw['id']))).fetchone()[0]) for raw in source['messages']]
                records.append({'source': source, 'ticket': store.get_ticket(tid), 'messages': messages})
    additional = extra_checks(records)
    if not additional['passed']:
        # Category/count only: never log customer content or identifiers.
        private_write(destination / 'owner-review-failures.json', canonical(additional).encode())
        raise Invalid('Additional display checks failed. Inspect the private report before owner review.')
    cases = choose_cases(records)
    facts = {'format': 'offline-owner-review-v1', 'created_at': now(), 'workspace': workspace,
             'tickets': len(records), 'messages': sum(len(r['source']['messages']) for r in records),
             'automated_checks': sum(report['fidelity']['checks'].values()) + sum(additional['checks'].values()),
             'automated_passed': True, 'owner_acceptance': 'pending', 'live_activation_authorized': False,
             'source_manifest_digest': digest(manifest), 'cases': [
                 {'case': i, 'reasons': c['reasons'], 'source_id': c['record']['source']['id'],
                  'internal_id': c['record']['ticket']['id'], 'messages': len(c['record']['source']['messages'])}
                 for i, c in enumerate(cases, 1)],
             'additional_checks': additional, 'limitations': coverage(records), 'workflows': WORKFLOWS}
    facts['packet_digest'] = digest({'facts': facts, 'cases': cases})
    private_write(destination / 'owner-review.html', render(cases, facts).encode())
    private_write(destination / 'owner-review.md', render_markdown(cases).encode())
    private_write(destination / 'owner-review.json', canonical(facts).encode())
    # This Markdown entry point has no customer content; case text stays in HTML.
    brief = ('# D7 review — owner decision pending\n\n' + \
        f'**{facts["tickets"]} tickets, {facts["messages"]} messages, {facts["automated_checks"]} checks passed.**\n\n' + \
        f'[Open the private comparison in Codex]({destination / "owner-review.md"}) — {len(cases)} selected conversations.\n\n' + \
        f'[HTML comparison]({destination / "owner-review.html"}) is also available for a browser on the workspace machine. '
        'Expand a case, then its messages. Source and imported values appear side by side.\n\n' + \
        'The comparison contains private customer data and must remain local. No live data was fetched or changed.\n\n' + \
        '## Cases\n\n' + '\n'.join(f'- Case {i}: {"; ".join(c["reasons"])} ({len(c["record"]["source"]["messages"])} messages).' for i, c in enumerate(cases, 1)) + \
        '\n\n## Workflows for acceptance\n\n' + '\n'.join(f'- **{title}:** {body}' for title, body in WORKFLOWS) + \
        '\n\n## Open gaps\n\n' + '\n'.join('- ' + gap for gap in facts['limitations']) + \
        '\n\nReply in the chat with **“Accept D7 for the offline sample with the listed gaps”**, or identify a case/message/workflow to change.\n\n' + \
        'D7 remains unchecked until your decision. This is acceptance of the bounded offline sample, not authorization for live operation.\n')
    private_write(destination / 'START-HERE.md', brief.encode())
    return {'passed': True, 'tickets': facts['tickets'], 'messages': facts['messages'],
            'automated_checks': facts['automated_checks'], 'selected_cases': len(cases),
            'owner_acceptance': 'pending', 'review': str(destination / 'START-HERE.md'), 'outboundActions': 0}


def main():
    parser = argparse.ArgumentParser(description='Prepare a private offline source/import review. Never records owner acceptance.')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--account', required=True)
    args = parser.parse_args()
    install_offline_guard()
    os.umask(0o077)
    try:
        print(json.dumps(prepare(args.snapshot, args.workspace, args.account), indent=2))
    except (Invalid, OSError, ValueError, KeyError, sqlite3.Error):
        print('Owner review preparation failed. Inspect private evidence; no acceptance or live action was recorded.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
