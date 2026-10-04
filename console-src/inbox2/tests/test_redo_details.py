from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import redo_details as details
import redo_worker as worker

EMAIL = 'person@example.com'
RETURN = {'id': 'r1', 'status': 'open', 'order_name': '#10312345', 'created_at': '2026-10-01', 'tags': ['secret']}


class RedoTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.queue = Path(temp.name) / 'queue.sqlite3'
        self.snapshot = Path(temp.name) / 'redo.sqlite3'
        with closing(self.db()) as db, db:
            details.initialize(db)
        self.calls = []
        self.response = {'returns': [RETURN]}
        self.failing = set()

    def db(self):
        return sqlite3.connect(self.queue)

    def call(self, order):
        self.calls.append(order)
        if isinstance(self.response, Exception) or order in self.failing:
            raise self.response if isinstance(self.response, Exception) else RuntimeError('down')
        if self.response == 'echo':
            return {'returns': [{**RETURN, 'order_name': '#' + order}]}
        return self.response

    def attach(self, orders=('#10312345',), email=EMAIL, ticket_id='gorgias:1', now=1000):
        ticket = {'id': ticket_id}
        return details.attach(ticket, email, list(orders), self.db, self.snapshot, now)['redoDetails']

    def run_worker(self, w=None, now=1001):
        w = w or worker.Worker(self.snapshot, call=self.call)
        rows = worker.read_requests(self.queue, now)
        w.process(rows, now)
        return w

    def test_identity_is_required_and_exact(self):
        for orders, email in [((), EMAIL), (('#1031',), 'bad'), (('order 1234',), EMAIL), (('1', '2', '3', '4'), EMAIL)]:
            self.assertEqual(self.attach(orders, email)['status'], 'unavailable')
        self.assertEqual(worker.read_requests(self.queue, 1001), [])

    def test_pending_then_observed_with_allowlisted_fields(self):
        self.assertEqual(self.attach()['status'], 'pending')
        self.run_worker()
        self.assertEqual(self.calls, ['10312345'])
        view = self.attach(now=1002)
        self.assertEqual(view['status'], 'observed')
        ret = view['orders']['10312345']['returns'][0]
        self.assertNotIn('tags', ret)
        self.assertIn('tracking_url', ret['missing'])
        self.assertTrue(view['fetchedAt'])

    def test_all_rejected_is_unavailable_not_empty(self):
        self.response = {'returns': [{**RETURN, 'order_name': '#99999'}, {'id': 'x'}]}
        self.attach()
        self.run_worker()
        view = self.attach(now=1002)
        self.assertEqual(view['status'], 'unavailable')
        self.assertEqual(view['incomplete'], ['10312345'])

    def test_valid_plus_rejected_is_partial(self):
        self.response = {'returns': [RETURN, {**RETURN, 'order_name': '#99999'}]}
        self.attach()
        self.run_worker()
        view = self.attach(now=1002)
        self.assertEqual((view['status'], view['incomplete']), ('partial', ['10312345']))
        self.assertEqual(len(view['orders']['10312345']['returns']), 1)

    def test_true_empty_is_empty(self):
        self.response = {'returns': []}
        self.attach()
        self.run_worker()
        self.assertEqual(self.attach(now=1002)['status'], 'empty')

    def test_multi_order_partial_failure_keeps_per_order_time(self):
        orders, self.response = ('#10312345', '#10312346'), 'echo'
        self.attach(orders)
        w = self.run_worker()
        later = 1000 + details.FOUND_TTL + 2
        self.failing = {'10312346'}
        self.attach(orders, now=later - 1)
        self.run_worker(w, now=later)
        view = self.attach(orders, now=later + 1)
        self.assertEqual(view['status'], 'stale')
        good, bad = view['orders']['10312345'], view['orders']['10312346']
        self.assertEqual((good['observedAtEpoch'], good.get('refreshFailed')), (later, None))
        self.assertEqual((bad['observedAtEpoch'], bad['refreshFailed'], bad['refreshFailedAt']), (1001, True, later))

    def test_new_order_failing_without_prior_is_partial(self):
        orders, self.response, self.failing = ('#10312345', '#10312346'), 'echo', {'10312346'}
        self.attach(orders)
        self.run_worker()
        view = self.attach(orders, now=1002)
        self.assertEqual((view['status'], view['incomplete']), ('partial', ['10312346']))
        self.assertNotIn('10312346', view['orders'])

    def test_nested_structures_are_bounded_and_private_keys_dropped(self):
        nested = {**RETURN, 'refunds': [{'amount': '5.00', 'customer_email': 'x@y.z'}] * 20,
                  'totals': {'refund': '5.00', 'shipping_address': {'line1': 'secret'}},
                  'exchange': {'itemCount': 1, 'name': 'a' * 500, 'unknown': {'private': 'hidden'}},
                  'shipments': [{'tracking': {'carrier': 'USPS'}}], 'notes': 'private', 'order': {'customer': 'x'}}
        self.response = {'returns': [nested]}
        self.attach()
        self.run_worker()
        ret = self.attach(now=1002)['orders']['10312345']['returns'][0]
        self.assertEqual(ret['refunds'][0], {'amount': '5.00'})
        self.assertEqual(len(ret['refunds']), worker.MAX_LIST)
        self.assertEqual(ret['totals'], {'refund': '5.00'})
        self.assertEqual(ret['exchange']['itemCount'], 1)
        self.assertEqual(len(ret['exchange']['name']), worker.MAX_STR)
        self.assertNotIn('unknown', ret['exchange'])
        self.assertEqual(ret['shipments'], [{}])
        self.assertNotIn('notes', ret)
        self.assertNotIn('order', ret)
        self.assertIn('compensation_methods', ret['missing'])
        self.assertNotIn('refunds', ret['missing'])

    def test_lookup_cap_reserves_whole_request(self):
        w = worker.Worker(self.snapshot, call=self.call, publish=lambda *a: None)
        w.window_at, w.lookups = 1001, worker.MAX_LOOKUPS_PER_MINUTE - 1
        request = details.make_request('gorgias:1', EMAIL, ['#10312345', '#10312346'])
        self.assertEqual(w.process([(request, details.request_key(request), 1000)], 1001), 0)
        self.assertEqual((self.calls, w.lookups), ([], worker.MAX_LOOKUPS_PER_MINUTE - 1))

    def test_publish_never_creates_parent(self):
        worker.publish({}, self.snapshot)  # existing empty directory is fine
        self.assertTrue(self.snapshot.exists())
        missing = self.snapshot.parent / 'absent' / 'redo.sqlite3'
        with self.assertRaises(OSError):
            worker.publish({}, missing)
        self.assertFalse(missing.parent.exists())

    def test_defaults_resolve_paths_at_call_time(self):
        self.attach()
        self.run_worker()
        original = details.SNAPSHOT
        self.addCleanup(setattr, details, 'SNAPSHOT', original)
        details.SNAPSHOT = self.snapshot
        self.assertIsNotNone(details.read_snapshot('gorgias:1'))

    def test_failure_without_snapshot_is_unavailable_not_empty(self):
        self.response = ConnectionError('down')
        self.attach()
        self.run_worker()
        self.assertEqual(self.attach(now=1002)['status'], 'unavailable')
        self.response = {'error': 'Redo API 500'}
        self.run_worker(now=2000)
        self.assertEqual(self.attach(now=2001)['status'], 'unavailable')

    def test_failure_with_snapshot_is_stale_with_timestamp(self):
        self.attach()
        w = self.run_worker()
        self.response = RuntimeError('down')
        self.attach(now=1000 + details.FOUND_TTL + 1)
        self.run_worker(w, now=1000 + details.FOUND_TTL + 2)
        view = self.attach(now=1000 + details.FOUND_TTL + 3)
        self.assertEqual(view['status'], 'stale')
        self.assertEqual(view['fetchedAtEpoch'], 1001)

    def test_snapshot_never_crosses_identity(self):
        self.attach()
        self.run_worker()
        self.assertEqual(self.attach(email='other@example.com', now=1002)['status'], 'pending')
        self.assertEqual(self.attach(orders=('#55555',), now=1002)['status'], 'pending')
        self.assertEqual(self.attach(ticket_id='gorgias:2', now=1002)['status'], 'pending')

    def test_bounds_and_tampered_queue(self):
        self.response = {'returns': [{**RETURN, 'id': str(i)} for i in range(15)]}
        self.attach()
        self.run_worker()
        order = self.attach(now=1002)['orders']['10312345']
        self.assertEqual((len(order['returns']), order['truncated']), (worker.MAX_RETURNS, True))
        with closing(self.db()) as db, db:
            db.execute("UPDATE redo_requests SET request='{\"ticketId\":\"gorgias:1\",\"email\":\"x@y.z\",\"orders\":[\"1234\"]}'")
        self.assertEqual(worker.read_requests(self.queue, 1001), [])

    def test_restart_keeps_snapshot(self):
        self.attach()
        self.run_worker()
        self.assertIn('gorgias:1', worker.Worker(self.snapshot, call=self.call).cache)

    def test_worker_has_fixed_target(self):
        self.assertEqual((worker.MCP_URL, worker.TOOL), ('http://127.0.0.1:8078/mcp', 'get_returns_for_order'))
        source = (Path(__file__).resolve().parents[3] / 'tools' / 'redo_mcp.py').read_text()
        self.assertIn('def get_returns_for_order(order_name: str)', source)
        for field in worker.FIELDS + worker.STRUCTURED:
            self.assertIn(f'"{field}"', source)

if __name__ == '__main__':
    unittest.main()
