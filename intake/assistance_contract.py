"""Versioned native-ID assistance contracts. All content stays local and is data."""
from datetime import datetime, timedelta, timezone
import json
import re

from .policy import Invalid
from .records import PRIORITIES, canonical, choice, digest, email, text, timestamp

MODE = 'offline_fixture'
SECTIONS = ('customer', 'orders', 'returns', 'products', 'knowledge')
MAX_BYTES = 2 * 1024 * 1024


def fields(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise Invalid(label + ': unsupported or missing fields.')


def identifier(value, label):
    value = text(value, label, 100, True)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:._-]{0,99}', value):
        raise Invalid(label + ': expected a plain stable identifier.')
    return value


def strings(value, label, limit=20):
    if not isinstance(value, list) or len(value) > limit:
        raise Invalid(label + ': expected a bounded text list.')
    return [text(item, label, 1000, True) for item in value]


def snapshot(db, tid):
    tid = text(tid, 'ticket_id', 128, True)
    ticket = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
    if not ticket:
        raise Invalid('Ticket not found by its independent ID.')
    contact = db.execute('SELECT id,name,email FROM contacts WHERE id=?', (ticket['contact_id'],)).fetchone()
    messages = db.execute('SELECT * FROM messages WHERE ticket_id=? ORDER BY rtrim(created_at,\'Z\'),sequence LIMIT 1001', (tid,)).fetchall()
    if len(messages) > 1000:
        raise Invalid('Conversation exceeds the offline assistance limit; no partial input was used.')
    contact = dict(contact) if contact else {'id': None, 'name': '', 'email': ''}
    incoming = [m for m in messages if m['kind'] == 'incoming']
    address = contact['email'].strip().casefold()
    try:
        email(address)
        identity_state = 'missing' if not address or not incoming else 'consistent'
    except Invalid:
        identity_state = 'missing'
    participants = {m['author_email'].strip().casefold() for m in incoming}
    if identity_state == 'consistent' and participants != {address}:
        identity_state = 'conflict' if participants - {'', address} else 'missing'
    assignment = db.execute('SELECT user_id FROM ticket_assignments WHERE ticket_id=?', (tid,)).fetchone()
    result = {'format': 'intake-assistance-input-v1', 'mode': MODE,
              'ticket': {k: ticket[k] for k in ('id','number','subject','contact_id','status','priority','queue','channel','origin','revision')},
              'contact': contact, 'tags': json.loads(ticket['tags_json']),
              'identity': {'contact_id': contact['id'], 'email': address, 'state': identity_state},
              'assignee_user_id': assignment[0] if assignment else None,
              'source_references': [dict(row) for row in db.execute("SELECT account,external_id FROM source_records WHERE ticket_id=? AND kind='ticket' ORDER BY account,external_id", (tid,))],
              'source_message_id': incoming[-1]['id'] if incoming else None,
              'messages': [], 'content_is_untrusted': True,
              'attachment_bytes_included': False, 'external_actions_allowed': False}
    for message in messages:
        item = {k: message[k] for k in ('id','kind','body','author_name','author_email','created_at','channel','origin')}
        item['content_is_untrusted'] = True
        item['headers_digest'] = digest(json.loads(message['headers_json']))
        item['attachments'] = []
        for a in db.execute('''SELECT a.id,a.metadata_json,a.availability,f.sha256 FROM attachments a
            LEFT JOIN attachment_files f ON f.attachment_id=a.id WHERE a.message_id=? ORDER BY a.id''', (message['id'],)):
            metadata = json.loads(a['metadata_json'])
            item['attachments'].append({'id': a['id'], 'name': metadata.get('name') or metadata.get('filename') or 'Attachment',
                                        'availability': a['availability'], 'sha256': a['sha256'], 'metadata_digest': digest(metadata)})
        result['messages'].append(item)
    if len(canonical(result).encode()) > MAX_BYTES:
        raise Invalid('Conversation exceeds the offline assistance limit; no partial input was used.')
    return result


def context(value, source):
    fields(value, ('identity','captured_at','expires_at','sections'), 'context')
    if value['identity'] != source['identity']:
        raise Invalid('Fixture identity must match the independent contact and conversation exactly.')
    captured = timestamp(value['captured_at'], 'captured_at')
    expires = timestamp(value['expires_at'], 'expires_at')
    if not datetime.fromisoformat(captured) < datetime.fromisoformat(expires):
        raise Invalid('Context expiration must follow capture time.')
    if datetime.fromisoformat(captured) > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise Invalid('Context capture time cannot be in the future.')
    fields(value['sections'], SECTIONS, 'context sections')
    evidence = set()
    for section in value['sections'].values():
        fields(section, ('state','reason','records'), 'context section')
        choice(section['state'], ('available','empty','unavailable','failed'), 'context state')
        text(section['reason'], 'context reason', 1000, section['state'] in ('unavailable','failed'))
        if not isinstance(section['records'], list) or len(section['records']) > 20:
            raise Invalid('Context records must be a bounded list.')
        if bool(section['records']) != (section['state'] == 'available'):
            raise Invalid('Available context needs records; unavailable/empty context cannot claim records.')
        if section['records'] and source['identity']['state'] != 'consistent':
            raise Invalid('Customer context is withheld until identity is consistent.')
        for record in section['records']:
            fields(record, ('id','title','text'), 'context record')
            ident = identifier(record['id'], 'evidence id')
            if ident in evidence:
                raise Invalid('Context evidence IDs must be unique.')
            evidence.add(ident)
            text(record['title'], 'evidence title', 300, True)
            text(record['text'], 'evidence text', 10_000, True)
    return evidence


def response(value, source, saved_context):
    fields(value, ('state','body','reason','missing_facts','staff_next_step','cited_evidence_ids','priority','review_required'), 'assistance response')
    state = choice(value['state'], ('ready','needs_staff','no_reply','failed'), 'response state')
    body = text(value['body'], 'draft body', 30_000, state == 'ready')
    reason = text(value['reason'], 'draft reason', 1000, True)
    missing = strings(value['missing_facts'], 'missing facts')
    step = text(value['staff_next_step'], 'staff next step', 2000, state == 'needs_staff')
    citations = strings(value['cited_evidence_ids'], 'evidence references', 100)
    available = {r['id'] for section in saved_context['sections'].values() for r in section['records']}
    if len(set(citations)) != len(citations) or set(citations) - available:
        raise Invalid('A suggestion may cite only unique evidence in this fixture.')
    if state != 'ready' and body:
        raise Invalid('Only a ready suggestion may contain customer-facing text.')
    if (state == 'needs_staff' and not missing) or (state != 'needs_staff' and (missing or step)):
        raise Invalid('Missing facts and staff next steps belong to needs_staff results only.')
    if state == 'ready' and (not citations or source['identity']['state'] != 'consistent'):
        raise Invalid('Ready suggestions need cited fixture evidence and a consistent customer identity.')
    priority = choice(value['priority'], PRIORITIES, 'suggested priority')
    if type(value['review_required']) is not bool:
        raise Invalid('review_required must be a boolean.')
    # Suggestion metadata never lowers a sensitive ticket or mutates its status.
    priority = max((priority, source['ticket']['priority']), key=PRIORITIES.index)
    return {'state': state, 'body': body, 'reason': reason, 'missing_facts': missing,
            'staff_next_step': step, 'cited_evidence_ids': citations, 'priority': priority,
            'review_required': value['review_required'] or priority in ('high','critical') or state == 'needs_staff'}


def fixture(value, source):
    fields(value, ('format','mode','fixture_id','label','ticket_id','input_digest','context','outcome','response'), 'fixture')
    if value['format'] != 'intake-assistance-fixture-v1' or value['mode'] != MODE:
        raise Invalid('Only the offline assistance fixture contract is supported.')
    identifier(value['fixture_id'], 'fixture_id')
    text(value['label'], 'fixture label', 100, True)
    if value['ticket_id'] != source['ticket']['id'] or value['input_digest'] != digest(source):
        raise Invalid('Fixture does not match the independent ticket ID and current input digest.')
    context(value['context'], source)
    choice(value['outcome'], ('result','timeout','malformed'), 'fixture outcome')
    if value['outcome'] == 'result':
        response(value['response'], source, value['context'])
    elif value['response'] is not None:
        raise Invalid('Failure fixtures must not carry a usable draft.')
    return value
