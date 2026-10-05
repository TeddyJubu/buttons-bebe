"""Reproducible D4 proof using private saved history and fabricated follow-ups."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3

from . import import_store, replay
from .imports import account_name
from .mail import metadata
from .policy import Invalid, install_offline_guard, workspace_directory
from .records import canonical
from .rehearse import fidelity, read_snapshot
from .store import Store


def snapshot(store):
    """Compare all durable rows, not just counts, across preview and repeat."""
    with store.connection() as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {table: [tuple(r) for r in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] for table in tables}


def run_groups(store, groups, account, preview=False):
    reports = []
    for mailbox, events in groups.items():
        for start in range(0, len(events), 500):
            data = canonical({'mode': 'offline_replay', 'account': account, 'mailbox': mailbox,
                              'events': events[start:start + 500]}).encode()
            planned = replay.run(store, data, preview=True)
            reports.append(planned if preview else replay.run(store, data, planned['digest']))
    return reports


def rehearse(directory, workspace, account):
    account = account_name(account)
    manifest, files = read_snapshot(directory)
    store = Store(workspace_directory(workspace))
    with store.connection() as db:
        if db.execute('SELECT count(*) FROM tickets').fetchone()[0] or db.execute('SELECT count(*) FROM replay_receipts').fetchone()[0]:
            raise Invalid('Use a new empty workspace for the replay rehearsal.')
    for data in files:
        report = import_store.preview(store, data, account)
        import_store.apply_import(store, data, account, report['digest'])
    checks, failures = Counter(), Counter()
    def check(name, condition):
        checks[name] += 1
        if not condition:
            failures[name] += 1
    check('initial_import_fidelity', fidelity(store, files, account)['passed'])
    historical, probes = defaultdict(list), defaultdict(list)
    expected, skipped = {}, Counter()
    tickets = [t for data in files for t in json.loads(data)['tickets']]
    future = max(datetime.fromisoformat(t['updated_datetime']) for t in tickets) + timedelta(days=1)
    with store.connection() as db:
        for ticket in tickets:
            tid = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND account=? AND external_id=?",
                             (account, str(ticket['id']))).fetchone()[0]
            for raw in ticket['messages']:
                msg = metadata(raw)
                mailboxes = [msg['sender']] if raw['from_agent'] else msg['recipients']
                if msg['channel'] != 'email' or not mailboxes or not mailboxes[0]:
                    skipped['unsupported_channel_or_missing_mailbox'] += 1
                    continue
                mailbox = mailboxes[0]
                key = 'historical-' + str(raw['id'])
                historical[mailbox].append({'event_id': key, 'provider': 'gorgias',
                    'subject': ticket.get('subject') or 'Untitled ticket', 'message': raw})
                participants = msg['recipients'] if raw['from_agent'] else [msg['sender']]
                if not msg['rfc_id'] or msg['header_issue'] or not participants or not participants[0] or msg['kind'] == 'note':
                    skipped['no_usable_thread_anchor'] += 1
                    continue
                if participants[0] == mailbox:
                    skipped['self_addressed_message_not_customer_anchor'] += 1
                    continue
                name = 'probe-' + str(len(expected) + 1)
                generated = {'event_id': name, 'provider': 'simulation', 'subject': 'Offline follow-up probe',
                    'message': {'id': name, 'channel': 'email', 'public': True, 'from_agent': False,
                        'created_datetime': future.isoformat(), 'body_text': 'Fabricated local test message. Never delivered.',
                        'sender': {'name': 'Offline test participant', 'email': participants[0]},
                        'source': {'from': {'address': participants[0]}, 'to': [{'address': mailbox}]},
                        'headers': {'Message-ID': '<d4-' + name + '@example.test>', 'In-Reply-To': '<' + msg['rfc_id'] + '>'},
                        'attachments': []}}
                probes[mailbox].append(generated)
                expected[name] = tid
    before = snapshot(store)
    run_groups(store, historical, account, preview=True)
    check('historical_preview_no_writes', before == snapshot(store))
    historical_reports = run_groups(store, historical, account)
    historical_outcomes = Counter()
    for report in historical_reports:
        historical_outcomes.update(report['outcomes'])
    check('historical_delivery_no_new_messages', set(historical_outcomes) <= {'duplicate_message', 'ignored'})
    check('historical_delivery_no_reopen', not any(r['reopened'] for r in historical_reports))
    check('historical_delivery_preserves_source', fidelity(store, files, account)['passed'])
    before = snapshot(store)
    run_groups(store, probes, account, preview=True)
    check('followup_preview_no_writes', before == snapshot(store))
    reports = run_groups(store, probes, account)
    results = [result for report in reports for result in report['results']]
    events = [event for events in probes.values() for event in events]
    touched = set()
    for candidate, result in zip(events, results):
        target = expected[candidate['event_id']]
        check('followup_threads_to_original_ticket', result['outcome'] == 'appended' and result['ticket_id'] == target)
        touched.add(target)
    with store.connection() as db:
        check('ticket_count_unchanged', db.execute('SELECT count(*) FROM tickets').fetchone()[0] == len(tickets))
        check('message_count_matches', db.execute('SELECT count(*) FROM messages').fetchone()[0] == manifest['counts']['messages'] + len(expected))
        for tid in touched:
            check('followed_ticket_open', db.execute('SELECT status FROM tickets WHERE id=?', (tid,)).fetchone()[0] == 'open')
    before = snapshot(store)
    store = Store(workspace_directory(workspace))
    repeated = run_groups(store, historical, account) + run_groups(store, probes, account)
    check('restart_repeat_rows_identical', before == snapshot(store))
    check('restart_repeat_all_duplicate_events', all(set(r['outcomes']) == {'duplicate_event'} for r in repeated))
    report = {'passed': not failures, 'checks': dict(checks), 'failures': dict(failures),
              'source_counts': manifest['counts'], 'historical_outcomes': dict(historical_outcomes),
              'followup_probes': len(expected), 'original_tickets_threaded': len(touched),
              'reopened': sum(r['reopened'] for r in reports), 'coverage_gaps': dict(skipped), 'outboundActions': 0,
              'limitations': ['Fabricated follow-ups against saved source headers; no real delivery.',
                              'Bounded sample; missing headers cannot prove threading.',
                              'Sender/mailbox checks do not establish email authenticity.']}
    report_path = workspace_directory(workspace) / 'replay-reconciliation.json'
    report_path.write_text(json.dumps(report, indent=2))
    os.chmod(report_path, 0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description='Offline replay proof against a saved snapshot, with no provider access.')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--account', required=True)
    args = parser.parse_args()
    install_offline_guard()
    os.umask(0o077)
    try:
        report = rehearse(args.snapshot, args.workspace, args.account)
    except (Invalid, OSError, ValueError, KeyError, sqlite3.Error):
        print('Replay rehearsal failed. Inspect private inputs locally; no live operation was attempted.')
        return 1
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
