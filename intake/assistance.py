"""Durable assistance rehearsal keyed only by independent ticket/message IDs."""
from datetime import datetime, timezone
import json
import os

from . import assistance_contract as contract, auth, delivery_context, fixture_assistant
from .policy import Conflict, Invalid
from .private_files import json_bytes, private_write, regular_directory
from .records import canonical, digest, now, text, uid

MODE = contract.MODE


def generation(db):
    row = db.execute("SELECT value FROM sandbox_meta WHERE key='review_generation'").fetchone()
    return row[0] if row else ''


def current_fixture(db, tid):
    return db.execute('SELECT * FROM assistance_fixtures WHERE ticket_id=? ORDER BY sequence DESC LIMIT 1', (tid,)).fetchone()


def basis_problem(source, saved):
    if digest(source) != saved['input_digest']:
        return 'Conversation changed. Import a fixture for the current ticket before running it again.'
    value = json.loads(saved['payload_json'])
    if datetime.fromisoformat(value['context']['expires_at']) <= datetime.now(timezone.utc):
        return 'Saved test context expired. Import a fresh fixture.'
    return ''


def read_input(store, tid):
    with store.connection() as db:
        db.execute('BEGIN')
        value = contract.snapshot(db, tid)
        return {'input': value, 'input_digest': digest(value)}


def export_input(store, tid):
    result = read_input(store, tid)
    directory = store.path.parent / 'assistance-inputs'
    directory.mkdir(mode=0o700, exist_ok=True)
    regular_directory(directory)
    os.chmod(directory, 0o700)
    destination = directory / (uid() + '.json')
    private_write(destination, canonical(result).encode())
    return {'path': str(destination), 'input_digest': result['input_digest'],
            'messages': len(result['input']['messages']), 'outboundActions': 0}


def import_fixture(store, payload, expected_digest=None, *, preview=False):
    if len(payload) > contract.MAX_BYTES:
        raise Invalid('Assistance fixture exceeds 2 MiB.')
    value = json_bytes(payload)
    if not isinstance(value, dict):
        raise Invalid('Expected an assistance fixture object.')
    with store.connection(write=True) as db:
        source = contract.snapshot(db, value.get('ticket_id'))
        contract.fixture(value, source)
        fingerprint = digest(value)
        old = db.execute('SELECT * FROM assistance_fixtures WHERE id=?', (value['fixture_id'],)).fetchone()
        if old and old['digest'] != fingerprint:
            raise Conflict('Fixture ID was already used for different content. Use a new ID.')
        if not preview and fingerprint != expected_digest:
            raise Conflict('Review this exact fixture digest before importing it.')
        if not preview and not old:
            db.execute('''INSERT INTO assistance_fixtures(id,ticket_id,input_digest,digest,payload_json,imported_at)
                VALUES (?,?,?,?,?,?)''', (value['fixture_id'], value['ticket_id'], value['input_digest'], fingerprint, canonical(value), now()))
            store.event(db, value['ticket_id'], 'assistance_fixture_imported', {'fixture_id':value['fixture_id'], 'digest':fingerprint}, 'Local fixture operator')
        return {'digest': fingerprint, 'alreadyImported': bool(old), 'imported': not preview,
                'evidenceRecords': sum(len(section['records']) for section in value['context']['sections'].values()),
                'outcome': value['outcome'], 'outboundActions': 0}


def _run_problem(db, run, source, saved):
    if not saved or saved['id'] != run['fixture_id']:
        return 'A newer test fixture replaced this suggestion. Run the current fixture.'
    if run['generation'] != generation(db):
        return 'This suggestion predates workspace recovery. Run the fixture again.'
    return basis_problem(source, saved)


def _usable(db, tid, run_id):
    run = db.execute('SELECT * FROM assistance_runs WHERE id=? AND ticket_id=?', (run_id,tid)).fetchone()
    latest = db.execute('SELECT id FROM assistance_runs WHERE ticket_id=? ORDER BY sequence DESC LIMIT 1', (tid,)).fetchone()
    if not run or not latest or latest[0] != run_id:
        raise Conflict('This suggestion is no longer current. Refresh the ticket.')
    source = contract.snapshot(db, tid)
    reason = _run_problem(db, run, source, current_fixture(db,tid))
    if reason:
        raise Conflict(reason)
    if run['state'] != 'ready' or db.execute('SELECT 1 FROM assistance_dismissals WHERE run_id=?', (run_id,)).fetchone():
        raise Conflict('Only a current ready suggestion can be used. Staff-only, failed or dismissed results cannot become replies.')
    facing = [m for m in source['messages'] if m['kind'] != 'note']
    if not facing or facing[-1]['kind'] != 'incoming' or source['ticket']['status'] in ('closed','snoozed'):
        raise Conflict('A current open customer message is required before using this suggestion.')
    delivery_context.available(db, tid)
    return run, json.loads(run['result_json'])


def usable(db, tid, run_id):
    run_id = text(run_id, 'assistance_run_id', 128, True)
    return _usable(db, tid, run_id)


def view(db, tid):
    saved = current_fixture(db, tid)
    blank = {'mode': MODE, 'can_run': False, 'can_use': False, 'fixture': None,
             'context': None, 'draft': None, 'reason': 'No saved assistance fixture. AI and provider access remain disabled.'}
    if not saved:
        return blank
    value = json.loads(saved['payload_json'])
    try:
        source = contract.snapshot(db, tid)
        problem = basis_problem(source, saved)
    except Invalid:
        source = None
        problem = 'Conversation exceeds the offline assistance limit; no partial input was used.'
    result = {**blank, 'fixture': {'id':saved['id'],'label':value['label'],'digest':saved['digest'],'mode':MODE},
              'can_run': not problem, 'reason': problem,
              'context': None if problem else value['context']}
    run = db.execute('SELECT * FROM assistance_runs WHERE ticket_id=? ORDER BY sequence DESC LIMIT 1', (tid,)).fetchone()
    if not run:
        return result
    issue = problem or _run_problem(db, run, source, saved)
    dismissed = db.execute('SELECT 1 FROM assistance_dismissals WHERE run_id=?', (run['id'],)).fetchone()
    data = json.loads(run['result_json'])
    result['draft'] = {'id':run['id'], 'state': 'stale' if issue else 'dismissed' if dismissed else run['state'],
                       'created_at':run['created_at'], 'source_message_id':json.loads(run['request_json'])['input']['source_message_id'],
                       'body': '', 'reason': issue or data.get('reason','Fixture run has not completed. Run again explicitly if interrupted.'),
                       'missing_facts': [], 'staff_next_step': '', 'cited_evidence_ids': [],
                       'priority': source['ticket']['priority'] if source else '', 'review_required': True}
    if not issue and not dismissed:
        result['draft'].update(data)
        if run['state'] == 'ready':
            try:
                usable(db, tid, run['id'])
                result['can_use'] = True
            except (Invalid, Conflict) as exc:
                result['reason'] = str(exc)
    return result


def reserve(store, tid, data):
    contract.fields(data, ('mode','operation_id','revision','fixture_digest'), 'fixture run')
    if data['mode'] != MODE:
        raise Invalid('Only offline fixture runs are supported.')
    created = False
    def apply(db):
        nonlocal created
        store.require_revision(db, tid, data['revision'])
        source = contract.snapshot(db, tid)
        saved = current_fixture(db,tid)
        if not saved or saved['digest'] != data['fixture_digest']:
            raise Conflict('The fixture changed or is missing. Refresh before running it.')
        problem = basis_problem(source,saved)
        if problem:
            raise Conflict(problem)
        value = json.loads(saved['payload_json'])
        contract.fixture(value,source)
        rid = uid()
        request = {'format':'intake-assistance-request-v1','mode':MODE,'run_id':rid,'request_token':uid(),
                   'input':source,'input_digest':digest(source),'fixture_digest':saved['digest'],'context':value['context']}
        actor = auth.current.get()
        db.execute('''INSERT INTO assistance_runs(id,ticket_id,fixture_id,input_digest,request_json,state,generation,created_at,actor_user_id)
            VALUES (?,?,?,?,?,'running',?,?,?)''', (rid,tid,saved['id'],request['input_digest'],canonical(request),generation(db),now(),actor['id'] if actor else None))
        store.event(db,tid,'assistance_run_started',{'run_id':rid,'fixture_id':saved['id'],'mode':MODE})
        created = True
        return {'run_id':rid,'outboundActions':0}
    result = store.operation('assistance_run:'+tid,data,apply)
    return result, created


def finish(store, run_id, output=None, *, failure=False):
    with store.connection(write=True) as db:
        run = db.execute('SELECT * FROM assistance_runs WHERE id=?', (run_id,)).fetchone()
        if not run:
            raise Invalid('Fixture run not found.')
        if run['state'] != 'running':
            return {'run_id':run_id,'outboundActions':0}
        request = json.loads(run['request_json'])
        saved = current_fixture(db,run['ticket_id'])
        latest = db.execute('SELECT id FROM assistance_runs WHERE ticket_id=? ORDER BY sequence DESC LIMIT 1', (run['ticket_id'],)).fetchone()[0]
        source = contract.snapshot(db,run['ticket_id'])
        issue = _run_problem(db,run,source,saved)
        if issue or latest != run_id:
            result = {'state':'stale','body':'','reason':issue or 'A newer fixture run superseded this result.'}
        elif failure:
            result = {'state':'failed','body':'','reason':'The offline fixture timed out. No usable suggestion was produced.'}
        else:
            try:
                contract.fields(output, ('format','mode','run_id','request_token','ticket_id','input_digest','fixture_digest','source_message_id','response'), 'fixture result')
                expected = {'format':'intake-assistance-result-v1','mode':MODE,'run_id':run_id,
                            'request_token':request['request_token'],'ticket_id':run['ticket_id'],
                            'input_digest':request['input_digest'],'fixture_digest':request['fixture_digest'],
                            'source_message_id':request['input']['source_message_id']}
                if any(output.get(k) != v for k,v in expected.items()):
                    raise Invalid('Fixture output binding mismatch.')
                result = contract.response(output['response'],source,request['context'])
            except (Invalid, TypeError, ValueError):
                result = {'state':'failed','body':'','reason':'The offline fixture returned invalid output. No usable suggestion was produced.'}
        db.execute('UPDATE assistance_runs SET state=?,result_json=?,completed_at=? WHERE id=?', (result['state'],canonical(result),now(),run_id))
        store.event(db,run['ticket_id'],'assistance_run_'+result['state'],{'run_id':run_id,'mode':MODE})
        return {'run_id':run_id,'outboundActions':0}


def run(store, tid, data):
    result, created = reserve(store,tid,data)
    if not created:
        return result  # A lost response never starts an implicit rerun.
    with store.connection() as db:
        saved = db.execute('SELECT request_json,fixture_id FROM assistance_runs WHERE id=?',(result['run_id'],)).fetchone()
        value = json.loads(db.execute('SELECT payload_json FROM assistance_fixtures WHERE id=?',(saved['fixture_id'],)).fetchone()[0])
        request = json.loads(saved['request_json'])
    try:
        output = fixture_assistant.evaluate(request,value)
    except TimeoutError:
        return finish(store,result['run_id'],failure=True)
    return finish(store,result['run_id'],output)


def use(store, tid, data):
    contract.fields(data, ('operation_id','run_id'), 'use suggestion')
    def apply(db):
        saved, result = usable(db,tid,data['run_id'])
        store.event(db,tid,'assistance_draft_used',{'run_id':saved['id']})
        return {'run_id':saved['id'],'body':result['body'], 'revision':json.loads(saved['request_json'])['input']['ticket']['revision'],'outboundActions':0}
    return store.operation('assistance_use:'+tid,data,apply,validate=lambda db: usable(db,tid,data['run_id']))


def dismiss(store, tid, data):
    contract.fields(data, ('operation_id','run_id'), 'dismiss suggestion')
    def apply(db):
        saved = db.execute('SELECT id FROM assistance_runs WHERE id=? AND ticket_id=?',(data['run_id'],tid)).fetchone()
        if not saved:
            raise Invalid('Suggestion does not belong to this ticket.')
        actor = auth.current.get()
        db.execute('INSERT OR IGNORE INTO assistance_dismissals VALUES (?,?,?)',(saved['id'],actor['id'] if actor else None,now()))
        store.event(db,tid,'assistance_draft_dismissed',{'run_id':saved['id']})
        return {'run_id':saved['id'],'outboundActions':0}
    return store.operation('assistance_dismiss:'+tid,data,apply)
