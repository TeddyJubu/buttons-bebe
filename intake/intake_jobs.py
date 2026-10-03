"""Bounded, one-shot fake intake worker with durable leases and atomic application."""
from collections import Counter
import json
import sqlite3
import time

from . import channel_adapter as adapter, replay
from .mail import Context, metadata
from .policy import Conflict, Invalid
from .private_files import checksum
from .records import canonical, digest, now, text, uid

LEASE_SECONDS = 60
MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 120)


class LeaseLost(Conflict):
    pass


def held(db):
    return db.execute("SELECT value FROM sandbox_meta WHERE key='intake_hold'").fetchone()


def claim(store):
    adapter.local_only()
    at = int(time.time())
    with store.connection(write=True) as db:
        if held(db):
            raise Conflict('Recovered intake is held. Inspect and explicitly resume locally.')
        db.execute("INSERT OR REPLACE INTO sandbox_meta VALUES ('intake_worker_at',?)", (str(at),))
        # A process can die without recording failure. Expired claims consume the
        # same bounded budget as handled transient failures.
        exhausted = list(db.execute('''SELECT id FROM intake_jobs WHERE attempts>=3 AND
            ((state='leased' AND lease_until<=?) OR (state IN ('queued','retry') AND available_at<=?))''', (at, at)))
        for row in exhausted:
            db.execute("UPDATE intake_jobs SET state='dead',lease_token=NULL,lease_until=NULL,updated_at=?,error_code='lease_exhausted' WHERE id=?", (at, row['id']))
            store.event(db, None, 'intake_job_dead', {'job_id': row['id'], 'code': 'lease_exhausted'})
        # Avoid letting another worker overtake a currently processing parent in
        # the same channel. Other channels remain independently claimable.
        row = db.execute('''SELECT j.* FROM intake_jobs j WHERE j.attempts<3 AND
            ((j.state IN ('queued','retry') AND j.available_at<=?) OR (j.state='leased' AND j.lease_until<=?))
            AND NOT EXISTS (SELECT 1 FROM intake_jobs active WHERE active.channel_id=j.channel_id
                AND active.state='leased' AND active.lease_until>?)
            ORDER BY j.sequence LIMIT 1''', (at, at, at)).fetchone()
        if not row:
            return None
        token = uid()
        db.execute("UPDATE intake_jobs SET state='leased',attempts=attempts+1,lease_token=?,lease_until=?,updated_at=? WHERE id=?",
                   (token, at + LEASE_SECONDS, at, row['id']))
        return {'id': row['id'], 'token': token}


def fence(db, claim):
    row = db.execute('SELECT * FROM intake_jobs WHERE id=?', (claim['id'],)).fetchone()
    if (held(db) or not row or row['state'] != 'leased' or row['lease_token'] != claim['token']
        or row['lease_until'] <= int(time.time())):
        raise LeaseLost('Intake lease was replaced, expired or held; its result was discarded.')
    return row


def receipt(db, row, channel, value):
    evidence = db.execute('''SELECT d.*,r.envelope_json,r.digest AS review_digest FROM fake_dispatches d
        JOIN delivery_attempts a ON a.id=d.attempt_id JOIN delivery_reviews r ON r.id=a.review_id
        WHERE d.attempt_id=?''', (value['attempt_id'],)).fetchone()
    if not evidence:
        # A callback can arrive before its dispatch evidence is durable. Retry
        # this read only; no path here can submit or resubmit a reply.
        raise TimeoutError('Receipt evidence is not yet available.')
    target = json.loads(evidence['envelope_json'])
    if (channel['provider'] != 'fake-delivery' or target['account'] != channel['account']
        or target['from'].casefold() != channel['mailbox'] or evidence['outcome'] != 'accepted'
        or evidence['receipt_id'] != value['receipt_id'] or evidence['review_digest'] != value['review_digest']):
        raise Invalid('Receipt does not match durable fake acceptance evidence.')
    db.execute('INSERT INTO delivery_observations VALUES (?,?,?,?,?)',
               (row['id'], value['attempt_id'], value['receipt_id'], value['status'], int(time.time())))
    # Accepted means the fake transport took responsibility; delivered is a
    # separate observation. These facts never authorize a retry or change D5.
    return {'outcome': 'receipt_recorded', 'attempt_id': value['attempt_id'], 'status': value['status']}


def replay_key(channel, event_id):
    # D4 receipts predate provider namespaces. E4 keeps them disjoint while the
    # common message/RFC identity engine still deduplicates imported history.
    return 'e4:' + digest([channel['provider'], event_id])


def process(store, claimed):
    adapter.local_only()
    with store.connection(write=True) as db:
        row = fence(db, claimed)
        channel = db.execute('SELECT * FROM adapter_channels WHERE id=?', (row['channel_id'],)).fetchone()
        value = json.loads(row['payload_json'])
        if digest(value) != row['digest']:
            raise Invalid('Job payload checksum mismatch.')
        if row['kind'] == 'receipt':
            result = receipt(db, row, channel, value)
        else:
            data = canonical({'mode': 'offline_replay', 'account': channel['account'], 'mailbox': channel['mailbox'], 'events': [value]}).encode()
            account, mailbox, events, _ = replay.prepare(data)
            event = events[0]
            if event['provider'] != channel['provider']:
                raise Invalid('Job namespace mismatch.')
            key = (account, mailbox, replay_key(channel, event['id']))
            previous = db.execute('SELECT * FROM replay_receipts WHERE account=? AND mailbox=? AND event_id=?', key).fetchone()
            if previous:
                if previous['digest'] != row['digest']:
                    raise Conflict('Intake receipt identity collision.')
                result = {**json.loads(previous['result_json']), 'outcome': 'duplicate_event', 'reopened': False}
            else:
                result = replay.receive(db, store, Context(db), account, mailbox, event)
                db.execute('INSERT INTO replay_receipts VALUES (?,?,?,?,?,?,?)',
                           (*key, row['digest'], canonical(result), row['payload_json'], now()))
        fence(db, claimed)
        db.execute("UPDATE intake_jobs SET state='done',lease_token=NULL,lease_until=NULL,error_code='',result_json=?,updated_at=? WHERE id=?",
                   (canonical(result), int(time.time()), row['id']))
        store.event(db, result.get('ticket_id'), 'intake_job_done', {'job_id': row['id'], 'outcome': result['outcome']})
        return result


def fail(store, claimed, code, *, transient):
    adapter.local_only()
    if code not in ('temporary_failure', 'invalid_event', 'identity_conflict', 'worker_error'):
        raise Invalid('Use a redacted worker failure code.')
    with store.connection(write=True) as db:
        row = fence(db, claimed)
        retry = transient and row['attempts'] < MAX_ATTEMPTS
        state = 'retry' if retry else 'dead'
        at = int(time.time())
        due = at + RETRY_DELAYS[row['attempts'] - 1] if retry else at
        db.execute('''UPDATE intake_jobs SET state=?,available_at=?,lease_token=NULL,lease_until=NULL,
            updated_at=?,error_code=? WHERE id=?''', (state, due, at, code, row['id']))
        store.event(db, None, 'intake_job_' + state, {'job_id': row['id'], 'code': code})
        return {'outcome': state, 'code': code}


def run_once(store):
    claimed = claim(store)
    if not claimed:
        return {'outcome': 'idle'}
    try:
        return process(store, claimed)
    except LeaseLost:
        return {'outcome': 'lease_lost'}
    except Exception as exc:
        if isinstance(exc, Conflict):
            code, transient = 'identity_conflict', False
        elif isinstance(exc, Invalid):
            code, transient = 'invalid_event', False
        elif isinstance(exc, (TimeoutError, sqlite3.OperationalError)):
            code, transient = 'temporary_failure', True
        else:
            code, transient = 'worker_error', False
        try:
            return fail(store, claimed, code, transient=transient)
        except LeaseLost:
            return {'outcome': 'lease_lost'}
        # If even recording failure cannot commit, the durable lease expires.


def run(store, limit=100):
    if type(limit) is not int or not 1 <= limit <= 500:
        raise Invalid('Run between 1 and 500 local jobs per explicit invocation.')
    outcomes = Counter()
    for _ in range(limit):
        result = run_once(store)
        if result['outcome'] == 'idle':
            break
        outcomes[result['outcome']] += 1
    return {'mode': 'offline_sandbox', 'outcomes': dict(outcomes), 'outboundActions': 0}


def delivery_states(db):
    states = {}
    for row in db.execute("SELECT attempt_id,outcome FROM fake_dispatches WHERE outcome='accepted'"):
        states[row['attempt_id']] = {'accepted'}
    for row in db.execute('SELECT attempt_id,status FROM delivery_observations'):
        states.setdefault(row['attempt_id'], set()).add(row['status'])
    def reduce(values):
        if {'delivered', 'bounced'} <= values:
            return 'conflict'
        for status in ('complained', 'bounced', 'delivered', 'deferred'):
            if status in values:
                return status
        return 'accepted'
    return {aid: reduce(values) for aid, values in states.items()}


def health(store):
    """Read-only aggregate report: no subjects, mailbox addresses, IDs or errors."""
    with store.connection() as db:
        db.execute('BEGIN')
        at = int(time.time())
        counts = {r[0]: r[1] for r in db.execute('SELECT state,count(*) FROM intake_jobs GROUP BY state')}
        oldest = db.execute("SELECT min(created_at) FROM intake_jobs WHERE state IN ('queued','retry','leased')").fetchone()[0]
        expired = db.execute("SELECT count(*) FROM intake_jobs WHERE state='leased' AND lease_until<=?", (at,)).fetchone()[0]
        unresolved = db.execute("SELECT count(*) FROM delivery_attempts WHERE state IN ('attempting','uncertain')").fetchone()[0]
        receipts = dict(Counter(delivery_states(db).values()))
        worker = db.execute("SELECT value FROM sandbox_meta WHERE key='intake_worker_at'").fetchone()
        lag = max(0, at - oldest) if oldest is not None else 0
        worker_age = max(0, at - int(worker[0])) if worker else None
        problems = []
        for code, present in (('recovery_hold', bool(held(db))), ('dead_jobs', counts.get('dead', 0)),
            ('expired_leases', expired), ('backlog_over_5m', lag > 300),
            ('worker_stale', oldest is not None and lag > 300 and (worker_age is None or worker_age > 120)),
            ('unresolved_delivery', unresolved), ('receipt_conflict', receipts.get('conflict', 0)),
            ('bounced', receipts.get('bounced', 0)), ('complained', receipts.get('complained', 0))):
            if present:
                problems.append(code)
        return {'mode': 'offline_sandbox', 'status': 'attention' if problems else 'ok', 'problems': problems,
                'jobs': counts, 'oldest_pending_seconds': lag, 'worker_age_seconds': worker_age,
                'expired_leases': expired, 'unresolved_attempts': unresolved, 'receipt_states': receipts,
                'failure_codes': {r[0]: r[1] for r in db.execute("SELECT error_code,count(*) FROM intake_jobs WHERE error_code!='' GROUP BY error_code")},
                'outboundActions': 0}


def recovery_plan(db):
    hold = held(db)
    jobs = [dict(r) for r in db.execute('SELECT id,state,attempts,lease_token,digest,updated_at FROM intake_jobs ORDER BY sequence')]
    return {'held': bool(hold), 'jobs': dict(Counter(j['state'] for j in jobs)),
            'digest': digest({'hold': hold[0] if hold else '', 'jobs': jobs}), 'outboundActions': 0}


def resume(store, expected_digest=None):
    adapter.local_only()
    with store.connection(write=True) as db:
        plan = recovery_plan(db)
        if expected_digest is None:
            return plan
        if not plan['held'] or plan['digest'] != expected_digest:
            raise Conflict('Recovery plan changed or intake is not held. Inspect it again.')
        db.execute("DELETE FROM sandbox_meta WHERE key='intake_hold'")
        store.event(db, None, 'intake_resumed_locally', {'plan_digest': expected_digest})
        return {'held': False, 'outboundActions': 0}


def retry_dead(store, job_id, expected_digest=None, reason=''):
    adapter.local_only()
    with store.connection(write=True) as db:
        row = db.execute('SELECT * FROM intake_jobs WHERE id=?', (job_id,)).fetchone()
        if not row or row['state'] != 'dead':
            raise Conflict('Only a dead intake job may be explicitly retried.')
        fingerprint = digest(dict(row))
        if expected_digest is None:
            return {'digest': fingerprint, 'code': row['error_code'], 'attempts': row['attempts']}
        if held(db) or fingerprint != expected_digest:
            raise Conflict('Job changed or intake is held. Inspect it again.')
        reason = text(reason, 'retry reason', 500, True)
        db.execute("UPDATE intake_jobs SET state='queued',attempts=0,available_at=?,updated_at=?,error_code='' WHERE id=?",
                   (int(time.time()), int(time.time()), job_id))
        store.event(db, None, 'intake_job_manual_retry', {'job_id': job_id, 'previous_digest': fingerprint, 'reason': reason})
        return {'queued': 1, 'outboundActions': 0}


def check_inbound_result(db, channel, value, result):
    """A completed receipt must still resolve to its authenticated native data."""
    message = metadata(value['message'])
    outcome = result.get('outcome')
    ignored = message['kind'] != 'incoming' or message['sender'] == channel['mailbox']
    if ignored:
        if result != {'outcome': 'ignored', 'reason': 'agent_echo_or_internal_note', 'reopened': False}:
            raise Invalid('Ignored intake result does not match its authenticated event.')
        return
    if outcome not in ('created', 'appended', 'duplicate_message'):
        raise Invalid('Completed intake result has an unsupported native outcome.')
    native = db.execute('SELECT * FROM messages WHERE id=?', (result.get('message_id'),)).fetchone()
    if (not native or native['ticket_id'] != result.get('ticket_id')
        or native['kind'] != 'incoming' or native['channel'] != 'email'
        or not db.execute('SELECT 1 FROM tickets WHERE id=?', (result.get('ticket_id'),)).fetchone()):
        raise Invalid('Completed intake result is missing its native message or ticket.')
    replayed = db.execute('SELECT * FROM replay_messages WHERE message_id=?', (native['id'],)).fetchone()
    sources = []
    if outcome in ('created', 'appended'):
        if (not replayed or replayed['account'] != channel['account'] or replayed['mailbox'] != channel['mailbox']
            or replayed['provider'] != channel['provider'] or replayed['external_id'] != message['external_id']
            or json.loads(replayed['payload_json']) != value['message']):
            raise Invalid('Completed intake message lost its authenticated replay provenance.')
        sources.append((message, 'offline_replay'))
    else:
        alias = db.execute('''SELECT * FROM replay_aliases WHERE account=? AND mailbox=? AND provider=?
            AND external_id=?''', (channel['account'], channel['mailbox'], channel['provider'], message['external_id'])).fetchone()
        if not alias or alias['message_id'] != native['id'] or alias['signature'] != message['signature']:
            raise Invalid('Duplicate intake result is missing its durable identity alias.')
        # Aliases do not replace original provenance. Require a surviving source
        # in this account/mailbox as well, including imported Gorgias history.
        if replayed and replayed['account'] == channel['account'] and replayed['mailbox'] == channel['mailbox']:
            original = metadata(json.loads(replayed['payload_json']))
            if original['external_id'] == replayed['external_id']:
                sources.append((original, 'offline_replay'))
        for source in db.execute('''SELECT * FROM source_records WHERE kind='message' AND internal_id=?
            AND account=? AND ticket_id=?''', (native['id'], channel['account'], native['ticket_id'])):
            original = metadata(json.loads(source['payload_json']))
            if (original['external_id'] == source['external_id'] and digest(original['raw']) == source['digest']
                and channel['mailbox'] in original['recipients']):
                sources.append((original, 'gorgias_export'))
    for original, origin in sources:
        if (original['kind'] == 'incoming' and original['channel'] == 'email'
            and original['signature'] == message['signature']
            and native['origin'] == origin and native['author_name'] == original['author_name']
            and native['author_email'] == (original['sender'] if origin == 'offline_replay' else original['author_email'])
            and native['body'] == original['body'] and native['created_at'] == original['created_at']
            and json.loads(native['headers_json']) == original['headers']):
            return
    raise Invalid('Completed intake message does not match its retained source provenance.')


def check_integrity(db):
    """Validate signed capture -> immutable job -> receipt/observation bindings."""
    envelopes = {}
    for row in db.execute('SELECT * FROM adapter_envelopes'):
        frame = {'format': adapter.FORMAT, **{k: row[k] for k in
                 ('channel_id','key_id','delivery_id','purpose','signed_at','body','signature')}}
        frame, channel = adapter.verify_frame(db, canonical(frame).encode(), freshness=False)
        if checksum(row['body'].encode()) != row['digest']:
            raise Invalid('Saved fake envelope checksum mismatch.')
        events = adapter.normalize(frame, channel)
        for event_id, value in events:
            # Redelivery may refer to a job captured earlier. Every authenticated
            # event must nevertheless retain that global job and exact payload.
            job = db.execute('SELECT digest,payload_json FROM intake_jobs WHERE channel_id=? AND kind=? AND event_id=?',
                             (channel['id'], frame['purpose'], event_id)).fetchone()
            if not job or job['digest'] != digest(value) or json.loads(job['payload_json']) != value:
                raise Invalid('Authenticated capture is missing an intact durable intake job.')
        envelopes[row['id']] = (channel, frame['purpose'], dict(events))
    for row in db.execute('SELECT * FROM intake_jobs'):
        channel, purpose, events = envelopes[row['envelope_id']]
        value = json.loads(row['payload_json'])
        if (channel['id'] != row['channel_id'] or purpose != row['kind']
            or events.get(row['event_id']) != value or digest(value) != row['digest']
            or (row['state'] == 'leased') != (row['lease_token'] is not None and row['lease_until'] is not None)
            or (row['state'] != 'leased' and (row['lease_token'] is not None or row['lease_until'] is not None))
            or (row['state'] in ('leased','retry','done','dead') and row['attempts'] < 1)
            or (row['state'] != 'done' and row['result_json'] != '{}')):
            raise Invalid('Intake job does not match its authenticated capture or lease state.')
        if row['state'] == 'done' and row['kind'] == 'inbound':
            saved = db.execute('SELECT * FROM replay_receipts WHERE account=? AND mailbox=? AND event_id=?',
                               (channel['account'], channel['mailbox'], replay_key(channel, row['event_id']))).fetchone()
            result = json.loads(row['result_json'])
            if not saved or saved['digest'] != row['digest'] or json.loads(saved['payload_json']) != value or result not in (
                json.loads(saved['result_json']), {**json.loads(saved['result_json']), 'outcome':'duplicate_event', 'reopened':False}):
                raise Invalid('Completed intake job is missing its atomic replay receipt.')
            check_inbound_result(db, channel, value, json.loads(saved['result_json']))
        if row['kind'] == 'receipt':
            observation = db.execute('SELECT * FROM delivery_observations WHERE job_id=?', (row['id'],)).fetchone()
            if (row['state'] == 'done') != bool(observation):
                raise Invalid('Receipt job and observation completion disagree.')
            if observation:
                evidence = db.execute('''SELECT d.*,r.digest AS review_digest,r.envelope_json FROM fake_dispatches d
                    JOIN delivery_attempts a ON a.id=d.attempt_id JOIN delivery_reviews r ON r.id=a.review_id WHERE d.attempt_id=?''',
                    (observation['attempt_id'],)).fetchone()
                target = json.loads(evidence['envelope_json']) if evidence else {}
                if (not evidence or evidence['outcome'] != 'accepted' or channel['provider'] != 'fake-delivery'
                    or target.get('account') != channel['account'] or target.get('from','').casefold() != channel['mailbox']
                    or observation['attempt_id'] != value['attempt_id'] or observation['receipt_id'] != value['receipt_id']
                    or evidence['receipt_id'] != value['receipt_id'] or evidence['review_digest'] != value['review_digest']
                    or observation['status'] != value['status'] or json.loads(row['result_json']) != {
                        'outcome':'receipt_recorded','attempt_id':value['attempt_id'],'status':value['status']}):
                    raise Invalid('Fake receipt observation does not match its dispatch and signed capture.')
