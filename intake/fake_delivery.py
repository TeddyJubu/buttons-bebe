"""SQLite-only fake transport. There is intentionally no configurable provider."""
from .policy import Conflict
from .records import now, uid

SCENARIOS = ('accepted', 'rejected', 'accepted_timeout', 'unknown')


def dispatch(store, attempt_id, fingerprint, scenario):
    # A separate durable transaction models acceptance surviving a lost response
    # or a crash before the application's conversation/ledger commit.
    with store.connection(write=True) as db:
        previous = db.execute('SELECT * FROM fake_dispatches WHERE attempt_id=?', (attempt_id,)).fetchone()
        if previous:
            if previous['digest'] != fingerprint:
                raise Conflict('Fake transport identity was reused with changed content.')
            return previous['outcome']
        outcome = {'accepted': 'accepted', 'rejected': 'rejected',
                   'accepted_timeout': 'accepted', 'unknown': 'unknown'}[scenario]
        db.execute('INSERT INTO fake_dispatches VALUES (?,?,?,?,?)',
                   (attempt_id, fingerprint, outcome, 'fake-' + uid() if outcome != 'unknown' else '', now()))
    if scenario in ('accepted_timeout', 'unknown'):
        raise TimeoutError('Simulated acknowledgement unavailable.')
    return outcome
