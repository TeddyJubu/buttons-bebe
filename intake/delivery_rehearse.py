"""Prove fake delivery behavior against saved exports; prints aggregate counts only."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sqlite3

from . import delivery, import_store
from .policy import Conflict, Invalid, install_offline_guard, workspace_directory
from .rehearse import fidelity, read_snapshot
from .replay_rehearse import snapshot
from .store import Store


def rehearse(directory, workspace, account):
    manifest, files = read_snapshot(directory)
    store = Store(workspace_directory(workspace))
    with store.connection() as db:
        if db.execute('SELECT count(*) FROM tickets').fetchone()[0]:
            raise Invalid('Use a fresh empty workspace for fake delivery proof.')
    for data in files:
        planned = import_store.preview(store, data, account)
        import_store.apply_import(store, data, account, planned['digest'])
    checks, failures, gaps, scenarios = Counter(), Counter(), Counter(), Counter()
    def check(label, condition):
        checks[label] += 1
        if not condition:
            failures[label] += 1
    def table_count(table):
        with store.connection() as db:
            return db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
    def source_rows():
        with store.connection() as db:
            return [tuple(r) for r in db.execute('SELECT * FROM source_records ORDER BY kind,account,external_id')]
    check('initial_fidelity', fidelity(store, files, account)['passed'])
    sources = source_rows()
    with store.connection() as db:
        originals = [dict(r) for r in db.execute('SELECT id,status FROM tickets ORDER BY number')]
    confirmations, attempts = [], []
    eligible = 0
    for index, original in enumerate(originals):
        tid = original['id']
        ticket = store.get_ticket(tid)
        if not ticket['reply_context']['available']:
            gaps[ticket['reply_context']['reason']] += 1
            continue
        eligible += 1
        body = 'Fabricated D5 reply for offline verification. No customer delivery.'
        def review(suffix, scenario='accepted', retry_of=''):
            return delivery.review(store, tid, {'mode': delivery.MODE, 'operation_id': f'proof-{index}-{suffix}',
                'revision': store.get_ticket(tid)['revision'], 'body': body, 'scenario': scenario, 'retry_of': retry_of})
        def confirmation(reviewed):
            return {'mode': delivery.MODE, 'review_id': reviewed['review_id'], 'digest': reviewed['digest'], 'confirmed': True}
        old_count = table_count('fake_dispatches')
        stale = review('stale')
        check('review_has_no_dispatch', table_count('fake_dispatches') == old_count)
        store.add_note(tid, {'operation_id': f'proof-note-{index}', 'revision': ticket['revision'],
                            'body': 'Synthetic context change to prove stale-review rejection.'})
        try:
            delivery.confirm(store, tid, confirmation(stale))
        except Conflict:
            check('stale_review_blocked', True)
        else:
            check('stale_review_blocked', False)
        check('stale_review_no_dispatch', table_count('fake_dispatches') == old_count)
        scenario = ('accepted', 'accepted_timeout', 'rejected', 'unknown')[(eligible - 1) % 4]
        scenarios[scenario] += 1
        reviewed = review('active', scenario)
        request = confirmation(reviewed)
        result = delivery.confirm(store, tid, request)
        confirmations.append((tid, request))
        check('one_initial_dispatch', table_count('fake_dispatches') == old_count + 1)
        if scenario in ('accepted_timeout', 'unknown'):
            check('uncertain_is_visible', result['state'] == 'uncertain')
            try:
                review('blocked-uncertain')
            except Conflict:
                check('uncertain_blocks_new_attempt', True)
            else:
                check('uncertain_blocks_new_attempt', False)
            result = delivery.reconcile(store, tid, {'mode': delivery.MODE, 'attempt_id': result['attempt_id'], 'confirmed': True})
            check('receipt_resolves_only_known_acceptance', result['state'] == ('simulated_delivered' if scenario == 'accepted_timeout' else 'uncertain'))
        elif scenario == 'rejected':
            check('definite_rejection', result['state'] == 'failed')
            try:
                review('blocked-retry')
            except Conflict:
                check('retry_requires_explicit_reference', True)
            else:
                check('retry_requires_explicit_reference', False)
            attempts.append((tid, result['attempt_id']))
            retried = review('explicit-retry', retry_of=result['attempt_id'])
            retry_request = confirmation(retried)
            result = delivery.confirm(store, tid, retry_request)
            confirmations.append((tid, retry_request))
            check('reviewed_retry_succeeds', result['state'] == 'simulated_delivered')
        else:
            check('success_recorded', result['state'] == 'simulated_delivered')
        attempts.append((tid, result['attempt_id']))
        if result['state'] == 'simulated_delivered':
            try:
                review('blocked-duplicate')
            except Conflict:
                check('new_operation_cannot_duplicate_reply', True)
            else:
                check('new_operation_cannot_duplicate_reply', False)
        check('status_preserved', store.get_ticket(tid)['status'] == original['status'])
    before = snapshot(store)
    store = Store(workspace_directory(workspace))
    for tid, request in confirmations:
        delivery.confirm(store, tid, request)
    for tid, aid in attempts:
        delivery.reconcile(store, tid, {'mode': delivery.MODE, 'attempt_id': aid, 'confirmed': True})
    check('restart_repeat_every_row_unchanged', before == snapshot(store))
    check('original_source_records_unchanged', source_rows() == sources)
    with store.connection() as db:
        states = dict(Counter(r[0] for r in db.execute('SELECT state FROM delivery_attempts')))
        outgoing = db.execute("SELECT count(*) FROM messages WHERE origin='offline_simulation'").fetchone()[0]
        check('one_message_per_fake_acceptance', outgoing == states.get('simulated_delivered', 0))
        check('source_ticket_count_preserved', table_count('tickets') == manifest['counts']['tickets'])
        check('all_messages_accounted_for', table_count('messages') == manifest['counts']['messages'] + eligible + outgoing)
    report = {'passed': not failures, 'checks': dict(checks), 'failures': dict(failures),
        'source_counts': manifest['counts'], 'eligible_conversations': eligible, 'coverage_gaps': dict(gaps),
        'scenarios': dict(scenarios), 'attempt_states': states, 'fake_dispatches': table_count('fake_dispatches'),
        'simulated_outgoing_messages': outgoing, 'outboundActions': 0,
        'limitations': ['Automated confirmation exercises the local UI/API contract; it is not proof of human identity.',
                        'SQLite fake receipts are not real provider delivery guarantees.',
                        'Unproven outcomes remain blocked; no automatic retry or live adapter exists.']}
    report_path = workspace_directory(workspace) / 'delivery-reconciliation.json'
    report_path.write_text(json.dumps(report, indent=2))
    os.chmod(report_path, 0o600)
    return report


def main():
    parser = argparse.ArgumentParser(description='Offline fake reply-delivery proof against a saved snapshot.')
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--account', required=True)
    args = parser.parse_args()
    install_offline_guard()
    os.umask(0o077)
    try:
        result = rehearse(args.snapshot, args.workspace, args.account)
    except (Invalid, OSError, ValueError, KeyError, sqlite3.Error):
        print('Fake-delivery rehearsal failed. Inspect private inputs locally; no external operation was attempted.')
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
