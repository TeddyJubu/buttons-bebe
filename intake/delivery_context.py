"""Derive a simulated reply envelope from saved conversation evidence only."""
import json

from .mail import Context, addresses, header, metadata
from .policy import Conflict, Invalid


def envelope(db, tid):
    ticket = db.execute('SELECT * FROM tickets WHERE id=?', (tid,)).fetchone()
    if not ticket:
        raise Invalid('Ticket not found.')
    if ticket['queue'] != 'inbox':
        raise Invalid('Reply simulation requires an Inbox ticket; review this queue first.')
    parent = db.execute("""SELECT * FROM messages WHERE ticket_id=? AND kind='incoming'
                           ORDER BY rtrim(created_at,'Z') DESC,sequence DESC LIMIT 1""", (tid,)).fetchone()
    if not parent or parent['channel'] != 'email':
        raise Invalid('A saved incoming email is required for reply simulation.')
    sources = list(db.execute("SELECT account,payload_json FROM source_records WHERE kind='message' AND internal_id=?", (parent['id'],)))
    sources += list(db.execute('SELECT account,payload_json FROM replay_messages WHERE message_id=?', (parent['id'],)))
    if len(sources) != 1:
        raise Invalid('The incoming message has no unambiguous source evidence.')
    source = sources[0]
    message = metadata(json.loads(source['payload_json']))
    if message['header_issue'] or not message['rfc_id'] or message['automatic'] or len(message['recipients']) != 1:
        raise Invalid('The latest incoming email needs review of its sender, mailbox or headers before a reply.')
    mailbox = message['recipients'][0]
    if not message['sender'] or message['sender'] == mailbox:
        raise Invalid('The latest incoming email has no distinct customer recipient.')
    reply_to = header(message['headers'], 'Reply-To')
    if reply_to and addresses(reply_to) != {message['sender']}:
        raise Invalid('Reply-To differs from the source sender or is ambiguous. Review the recipient first.')
    owners = Context(db).by_rfc.get((source['account'], mailbox, message['rfc_id']), [])
    if {row['message_id'] for row in owners} != {parent['id']}:
        raise Invalid('The parent message ID is ambiguous. Review the conversation first.')
    subject = ticket['subject'] if ticket['subject'].lower().startswith('re:') else 'Re: ' + ticket['subject']
    references = [value for value in dict.fromkeys(message['references']) if value != message['rfc_id']]
    references = references[-99:] + [message['rfc_id']]
    return {'account': source['account'], 'from': mailbox, 'to': message['sender'], 'subject': subject,
            'parent_id': parent['id'], 'in_reply_to': '<' + message['rfc_id'] + '>',
            'references': ' '.join('<' + value + '>' for value in references)}


def unresolved(db, tid):
    return db.execute("SELECT 1 FROM delivery_attempts WHERE ticket_id=? AND state IN ('attempting','uncertain')", (tid,)).fetchone()


def available(db, tid):
    if unresolved(db, tid):
        raise Conflict('A simulation is unresolved. Check its fake receipt before another attempt.')
    return envelope(db, tid)


def context(db, tid):
    try:
        return {'available': True, **available(db, tid)}
    except Invalid as exc:
        return {'available': False, 'reason': str(exc)}


def attempt_result(db, attempt_id):
    row = db.execute("""SELECT a.*,r.body,r.envelope_json,r.retry_of FROM delivery_attempts a
                        JOIN delivery_reviews r ON r.id=a.review_id WHERE a.id=?""", (attempt_id,)).fetchone()
    if not row:
        raise Invalid('Simulation attempt not found.')
    saved = json.loads(row['envelope_json'])
    link = db.execute('SELECT run_id FROM assistance_reviews WHERE review_id=?',(row['review_id'],)).fetchone()
    return {'attempt_id': row['id'], 'review_id': row['review_id'], 'ticket_id': row['ticket_id'],
            'state': row['state'], 'reason': row['reason'], 'message_id': row['message_id'],
            'from': saved['from'], 'to': saved['to'], 'subject': saved['subject'], 'body': row['body'],
            'parent_id': saved['parent_id'], 'assistance_run_id': link['run_id'] if link else None,
            'created_at': row['created_at'], 'updated_at': row['updated_at'], 'retry_of': row['retry_of'],
            'mode': 'offline_simulation', 'outboundActions': 0}


def history(db, tid):
    return [attempt_result(db, row[0]) for row in db.execute(
        'SELECT id FROM delivery_attempts WHERE ticket_id=? ORDER BY rowid DESC', (tid,))]
