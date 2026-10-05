"""Review -> reserve -> fake dispatch -> reconcile. No automatic retries or I/O."""
from datetime import datetime, timedelta
import json

from . import fake_delivery, auth, assistance
from .delivery_context import attempt_result, available
from .policy import Conflict, Invalid
from .records import canonical, choice, digest, now, text, uid

MODE = 'offline_simulation'


def validate(data, fields):
    if not isinstance(data, dict) or set(data) - set(fields) or data.get('mode') != MODE:
        raise Invalid('Use the offline_simulation contract without extra fields.')


def confirm_flag(data):
    if data.get('confirmed') is not True:
        raise Invalid('Review and explicitly confirm this simulation first.')


def eligible(db, tid, content_key, retry_of):
    # Protect semantic duplicates too: new review/operation IDs cannot bypass an
    # accepted reply to the same incoming message, even after a page reload.
    previous = db.execute('SELECT * FROM delivery_attempts WHERE ticket_id=? AND content_key=? ORDER BY rowid DESC LIMIT 1',
                          (tid, content_key)).fetchone()
    if previous and previous['state'] != 'failed':
        raise Conflict('This reply was already simulated or is unresolved. Inspect its delivery record.')
    if previous and retry_of != previous['id']:
        raise Conflict('This reply was rejected. Use its explicit retry action and review again.')
    if retry_of and (not previous or retry_of != previous['id'] or previous['state'] != 'failed'):
        raise Conflict('Retry must reference the latest proven rejection of this exact reply.')


def review(store, tid, data):
    validate(data, ('mode', 'operation_id', 'revision', 'body', 'scenario', 'retry_of', 'assistance_run_id'))
    body = text(data.get('body'), 'reply body', 100_000, True)
    scenario = choice(data.get('scenario'), fake_delivery.SCENARIOS, 'simulation outcome')
    retry_of = text(data.get('retry_of') or '', 'retry_of', 128)
    def apply(db):
        ticket = store.require_revision(db, tid, data.get('revision'))
        if data.get('assistance_run_id') is not None:
            assistance.usable(db,tid,data['assistance_run_id'])
        target = available(db, tid)
        content_key = digest({'ticket_id': tid, 'envelope': target, 'body': body})
        eligible(db, tid, content_key, retry_of)
        rid, at = uid(), now()
        expires = (datetime.fromisoformat(at) + timedelta(minutes=10)).isoformat().replace('+00:00', 'Z')
        result = {'review_id': rid, 'ticket_id': tid, 'revision': ticket['revision'], 'envelope': target,
                  'body': body, 'scenario': scenario, 'retry_of': retry_of,
                  'created_at': at, 'expires_at': expires, 'mode': MODE, 'outboundActions': 0}
        generation = db.execute("SELECT value FROM sandbox_meta WHERE key='review_generation'").fetchone()
        result['generation'] = generation[0] if generation else ''
        result['digest'] = digest(result)
        db.execute('INSERT INTO delivery_reviews VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (rid, tid, ticket['revision'], canonical(target), body, scenario, retry_of,
                    content_key, at, expires, result['digest'], result['generation']))
        if data.get('assistance_run_id') is not None:
            db.execute('INSERT INTO assistance_reviews VALUES (?,?)',(rid,data['assistance_run_id']))
        if auth.current.get():
            db.execute('INSERT INTO review_owners VALUES (?,?)', (rid, auth.current.get()['id']))
        store.event(db, tid, 'reply_simulation_reviewed', {'review_id': rid}, 'Sandbox operator')
        return result
    return store.operation('simulation_review:' + tid, data, apply)


def reserve(store, tid, data):
    validate(data, ('mode', 'review_id', 'digest', 'confirmed'))
    confirm_flag(data)
    rid = text(data.get('review_id'), 'review_id', 128, True)
    fingerprint = text(data.get('digest'), 'digest', 128, True)
    with store.connection(write=True) as db:
        saved = db.execute('SELECT * FROM delivery_reviews WHERE id=? AND ticket_id=?', (rid, tid)).fetchone()
        if not saved or saved['digest'] != fingerprint:
            raise Conflict('Review does not match this ticket or confirmation. Review again.')
        if auth.current.get():
            owner = db.execute('SELECT user_id FROM review_owners WHERE review_id=?', (rid,)).fetchone()
            if not owner or owner[0] != auth.current.get()['id']:
                raise auth.Denied('Only the teammate who reviewed this reply may confirm it.')
        old = db.execute('SELECT id FROM delivery_attempts WHERE review_id=?', (rid,)).fetchone()
        if old:
            return attempt_result(db, old['id']), None
        linked = db.execute('SELECT run_id FROM assistance_reviews WHERE review_id=?',(rid,)).fetchone()
        if linked:
            assistance.usable(db,tid,linked['run_id'])
        generation = db.execute("SELECT value FROM sandbox_meta WHERE key='review_generation'").fetchone()
        if saved['generation'] != (generation[0] if generation else ''):
            raise Conflict('This review predates workspace recovery. Review the restored ticket again.')
        at = now()
        if datetime.fromisoformat(at) >= datetime.fromisoformat(saved['expires_at']):
            raise Conflict('This review expired. Review the current ticket again.')
        store.require_revision(db, tid, saved['revision'])
        target = available(db, tid)
        if canonical(target) != saved['envelope_json']:
            raise Conflict('The reply recipient or conversation changed. Review again.')
        eligible(db, tid, saved['content_key'], saved['retry_of'])
        aid = uid()
        db.execute("""INSERT INTO delivery_attempts(id,review_id,ticket_id,content_key,state,created_at,updated_at)
                      VALUES (?,?,?,?,'attempting',?,?)""", (aid, rid, tid, saved['content_key'], at, at))
        db.execute('UPDATE tickets SET revision=revision+1 WHERE id=?', (tid,))
        store.event(db, tid, 'reply_simulation_started', {'attempt_id': aid}, 'Sandbox operator')
        return attempt_result(db, aid), saved['scenario']


def finalize(store, attempt_id, *, uncertain=False):
    with store.connection(write=True) as db:
        attempt = db.execute('SELECT * FROM delivery_attempts WHERE id=?', (attempt_id,)).fetchone()
        if not attempt:
            raise Invalid('Simulation attempt not found.')
        if attempt['state'] in ('simulated_delivered', 'failed'):
            return attempt_result(db, attempt_id)
        saved = db.execute('SELECT * FROM delivery_reviews WHERE id=?', (attempt['review_id'],)).fetchone()
        receipt = db.execute('SELECT * FROM fake_dispatches WHERE attempt_id=?', (attempt_id,)).fetchone()
        if receipt and receipt['digest'] != saved['digest']:
            raise Conflict('Fake receipt does not match the reviewed reply. Leave this attempt unresolved.')
        evidence = receipt['outcome'] if receipt and not uncertain else 'unknown'
        state = {'accepted': 'simulated_delivered', 'rejected': 'failed', 'unknown': 'uncertain'}[evidence]
        reason = {'accepted': 'fake_receipt_accepted', 'rejected': 'fake_receipt_rejected',
                  'unknown': 'acceptance_unproven_no_retry'}[evidence]
        if attempt['state'] == state:
            return attempt_result(db, attempt_id)
        at, mid = now(), None
        if evidence == 'accepted':
            target = json.loads(saved['envelope_json'])
            headers = {'Message-ID': '<sandbox-' + attempt_id + '@intake.invalid>',
                       'In-Reply-To': target['in_reply_to'], 'References': target['references']}
            mid = uid()
            owner = db.execute('SELECT u.name FROM users u JOIN review_owners r ON r.user_id=u.id WHERE r.review_id=?', (saved['id'],)).fetchone()
            author = owner[0] if owner else 'Sandbox operator'
            raw = {'id': attempt_id, 'channel': 'email', 'public': True, 'from_agent': True,
                   'created_datetime': receipt['recorded_at'], 'headers': headers,
                   'body_text': saved['body'], 'sender': {'name': author, 'email': target['from']},
                   'source': {'from': {'address': target['from']}, 'to': [{'address': target['to']}]}, 'attachments': []}
            db.execute("""INSERT INTO messages(id,ticket_id,kind,author_name,author_email,body,created_at,channel,headers_json,origin)
                          VALUES (?,?,'outgoing',?,?,?,?,'email',?,'offline_simulation')""",
                       (mid, attempt['ticket_id'], author, target['from'], saved['body'], receipt['recorded_at'], canonical(headers)))
            db.execute('INSERT INTO simulated_outgoing VALUES (?,?,?,?,?)',
                       (mid, attempt_id, target['account'], target['from'], canonical(raw)))
            ticket = db.execute('SELECT updated_at FROM tickets WHERE id=?', (attempt['ticket_id'],)).fetchone()
            latest = max((ticket['updated_at'], receipt['recorded_at']), key=datetime.fromisoformat)
            db.execute('UPDATE tickets SET updated_at=? WHERE id=?', (latest, attempt['ticket_id']))
        db.execute('UPDATE delivery_attempts SET state=?,reason=?,message_id=?,updated_at=? WHERE id=?',
                   (state, reason, mid, at, attempt_id))
        db.execute('UPDATE tickets SET revision=revision+1 WHERE id=?', (attempt['ticket_id'],))
        store.event(db, attempt['ticket_id'], 'reply_simulation_' + state,
                    {'attempt_id': attempt_id, 'message_id': mid, 'reason': reason}, 'Offline simulator')
        return attempt_result(db, attempt_id)


def confirm(store, tid, data):
    result, scenario = reserve(store, tid, data)
    if scenario is None:
        return result  # Repeat confirmations never dispatch, including after a crash.
    try:
        fake_delivery.dispatch(store, result['attempt_id'], data['digest'], scenario)
    except TimeoutError:
        return finalize(store, result['attempt_id'], uncertain=True)
    return finalize(store, result['attempt_id'])


def reconcile(store, tid, data):
    validate(data, ('mode', 'attempt_id', 'confirmed'))
    confirm_flag(data)
    aid = text(data.get('attempt_id'), 'attempt_id', 128, True)
    with store.connection() as db:
        result = attempt_result(db, aid)
        if result['ticket_id'] != tid:
            raise Invalid('Simulation attempt belongs to another ticket.')
    # Reads only durable fake evidence. Missing/unknown evidence cannot prove a
    # rejection and cannot release a retry, even if no dispatch row was committed.
    return finalize(store, aid)
