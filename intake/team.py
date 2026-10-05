"""Shared assignment and private, versioned read watermarks for local users."""
from . import auth
from .policy import Invalid, Conflict


def members(store):
    with store.connection() as db:
        return [auth.public(row) for row in db.execute('SELECT * FROM users ORDER BY active DESC,name,id')]


def assign(store, db, tid, user_id):
    if not isinstance(user_id, str):
        raise Invalid('Choose an active local teammate or Unassigned.')
    old = db.execute('SELECT user_id FROM ticket_assignments WHERE ticket_id=?', (tid,)).fetchone()
    if user_id:
        row = db.execute("SELECT id FROM users WHERE id=? AND active=1 AND role IN ('admin','agent')", (user_id,)).fetchone()
        if not row:
            raise Invalid('Choose an active admin or agent as the assignee.')
        db.execute('INSERT INTO ticket_assignments VALUES (?,?) ON CONFLICT(ticket_id) DO UPDATE SET user_id=excluded.user_id', (tid, user_id))
    else:
        db.execute('DELETE FROM ticket_assignments WHERE ticket_id=?', (tid,))
    if (old[0] if old else '') != user_id:
        store.event(db, tid, 'ticket_assigned', {'before_user_id': old[0] if old else '', 'after_user_id': user_id})


def decorate(db, ticket):
    actor = auth.current.get()
    if not actor:
        return ticket
    assigned = db.execute('SELECT u.* FROM users u JOIN ticket_assignments a ON a.user_id=u.id WHERE a.ticket_id=?', (ticket['id'],)).fetchone()
    ticket['team_assignee'] = auth.public(assigned) if assigned else None
    read = db.execute('SELECT * FROM ticket_reads WHERE ticket_id=? AND user_id=?', (ticket['id'], actor['id'])).fetchone()
    last = db.execute('SELECT coalesce(max(sequence),0) FROM messages WHERE ticket_id=?', (ticket['id'],)).fetchone()[0]
    ticket['read_state'] = {'unread': bool((read and read['forced_unread']) or last > (read['seen_sequence'] if read else 0)),
                            'version': read['version'] if read else 0, 'through_sequence': last}
    return ticket


def filter_view(view, where, args):
    if view not in ('all', 'mine', 'unassigned', 'unread'):
        raise Invalid('Unknown team view.')
    actor = auth.current.get()
    if view == 'all':
        return
    if not actor:
        raise Invalid('Team views require an authenticated user.')
    if view == 'mine':
        where.append('EXISTS(SELECT 1 FROM ticket_assignments a WHERE a.ticket_id=t.id AND a.user_id=?)')
        args.append(actor['id'])
    elif view == 'unassigned':
        where.append('NOT EXISTS(SELECT 1 FROM ticket_assignments a WHERE a.ticket_id=t.id)')
    else:
        where.append('''(EXISTS(SELECT 1 FROM ticket_reads r WHERE r.ticket_id=t.id AND r.user_id=? AND r.forced_unread=1)
            OR EXISTS(SELECT 1 FROM messages m WHERE m.ticket_id=t.id AND m.sequence > coalesce(
            (SELECT seen_sequence FROM ticket_reads r WHERE r.ticket_id=t.id AND r.user_id=?),0)))''')
        args.extend([actor['id'], actor['id']])


def mark(store, tid, data):
    if type(data.get('unread')) is not bool or type(data.get('version')) is not int or type(data.get('through_sequence')) is not int:
        raise Invalid('Supply the displayed read-state version and message watermark.')
    def apply(db):
        user_id = auth.current.get()['id']
        read = db.execute('SELECT * FROM ticket_reads WHERE ticket_id=? AND user_id=?', (tid, user_id)).fetchone()
        if data['version'] != (read['version'] if read else 0):
            raise Conflict('Your read state changed in another tab. Refresh before marking it again.')
        through = data['through_sequence']
        if through < 0 or (through and not db.execute('SELECT 1 FROM messages WHERE ticket_id=? AND sequence=?', (tid, through)).fetchone()):
            raise Invalid('Read watermark must belong to a displayed message in this ticket.')
        if not db.execute('SELECT 1 FROM tickets WHERE id=?', (tid,)).fetchone():
            raise Invalid('Ticket not found.')
        seen = max(read['seen_sequence'] if read else 0, through if not data['unread'] else 0)
        db.execute('''INSERT INTO ticket_reads VALUES (?,?,?,?,?) ON CONFLICT(ticket_id,user_id)
            DO UPDATE SET seen_sequence=excluded.seen_sequence,forced_unread=excluded.forced_unread,version=excluded.version''',
            (tid, user_id, seen, int(data['unread']), data['version'] + 1))
        return {'id': tid}
    return store.operation('read_state:' + tid, data, apply)


def claim(store, tid, data):
    def apply(db):
        ticket = store.require_revision(db, tid, data.get('revision'))
        if db.execute('SELECT 1 FROM ticket_assignments WHERE ticket_id=?', (tid,)).fetchone():
            raise Conflict('This ticket is already assigned. Refresh before changing its assignment.')
        assign(store, db, tid, auth.current.get()['id'])
        db.execute('UPDATE tickets SET revision=revision+1 WHERE id=?', (tid,))
        return {'id': tid, 'revision': ticket['revision'] + 1}
    return store.operation('claim:' + tid, data, apply)
