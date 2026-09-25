from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest.mock import patch

import customer_details as details
import shop_worker
import export_shop_rail as exporter
import live_api

TICKET = {'id': 'gorgias:123', 'fromEmail': 'person@example.com', 'subject': 'Order #10312345',
          'messages': [{'body': 'Please check order #10312345.'}]}
CUSTOMER = {'id': 'gid://shopify/Customer/1', 'defaultEmailAddress': {'emailAddress': 'person@example.com'}, 'displayName': 'Example Customer'}
ORDER = {'id': 'gid://shopify/Order/1', 'name': '#10312345', 'customer': CUSTOMER, 'email': 'person@example.com', 'returns': {'nodes': []}}


class DetailsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Path(self.temp.name) / 'queue.sqlite3'
        self.snapshot = Path(self.temp.name) / 'rail.sqlite3'
        with closing(self.db()) as db, db:
            details.initialize(db)
        self.calls = []

    def db(self):
        return sqlite3.connect(self.queue)

    def graphql(self, env, token, document, variables):
        self.calls.append(document)
        if document == exporter.CUSTOMER_BY_EMAIL:
            return {'customers': {'nodes': [CUSTOMER]}}
        if document == exporter.ORDER_BY_NAME:
            return {'orders': {'nodes': [ORDER]}}
        if document == exporter.PAST_ORDERS:
            return {'customer': {'orders': {'nodes': [ORDER]}}}
        raise AssertionError('Only allowlisted read queries are permitted')

    def worker(self, **kwargs):
        return shop_worker.Worker({}, self.snapshot, graphql=kwargs.get('graphql', self.graphql), mint=kwargs.get('mint', lambda _: 'test-token'))

    def enqueue(self, ticket=None, now=None):
        return details.attach(copy.deepcopy(ticket or TICKET), self.db, self.snapshot, now=now)

    def test_live_ticket_missing_from_legacy_projection_gets_details(self):
        self.assertEqual(self.enqueue()['shopifyRail']['status'], 'loading')
        requests = shop_worker.read_requests(self.queue)
        self.assertEqual(len(requests), 1)
        self.assertEqual(self.worker().process(requests), 1)
        rail = self.enqueue()['shopifyRail']
        self.assertEqual(rail['customer']['id'], CUSTOMER['id'])
        self.assertEqual(rail['order']['id'], ORDER['id'])
        self.assertEqual(len(rail['history']), 1)
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn('messages', requests[0][0])
        with self.db() as db:
            self.assertEqual(db.execute('select count(*) from shop_requests').fetchone()[0], 1)

    def test_duplicate_reads_use_cache(self):
        self.enqueue()
        worker = self.worker()
        requests = shop_worker.read_requests(self.queue)
        worker.process(requests)
        self.assertEqual(worker.process(requests), 0)
        self.assertEqual(len(self.calls), 3)

    def test_legacy_customer_only_snapshot_does_not_skip_live_order_lookup(self):
        ticket = {**TICKET, 'shopifyRail': {'status': 'found', 'customer': CUSTOMER, 'stale': False}}
        rail = self.enqueue(ticket)['shopifyRail']
        self.assertTrue(rail['refreshing'])
        self.worker().process(shop_worker.read_requests(self.queue))
        self.assertEqual(self.enqueue(ticket)['shopifyRail']['order']['id'], ORDER['id'])

    def test_identity_change_never_reuses_prior_customer(self):
        self.enqueue()
        self.worker().process(shop_worker.read_requests(self.queue))
        changed = {**TICKET, 'fromEmail': 'other@example.com'}
        rail = self.enqueue(changed)['shopifyRail']
        self.assertEqual(rail['status'], 'loading')
        self.assertNotIn('customer', rail)
        request = shop_worker.read_requests(self.queue)[0]
        result, _ = exporter.lookup_ticket({}, '', request[0], self.worker().caches)
        self.assertIsNone(result['customer'])
        self.assertIsNone(result['order'])

    def test_conflicting_or_invalid_email_never_queues(self):
        for ticket in [{**TICKET, 'customerContext': {'conflict': True}}, {**TICKET, 'fromEmail': 'x" OR email:*@example.com'}]:
            self.assertEqual(self.enqueue(ticket)['shopifyRail']['status'], 'unavailable')
        self.assertEqual(shop_worker.read_requests(self.queue), [])

    def test_failed_refresh_retains_details_and_original_timestamp(self):
        start = time.time()
        self.enqueue(now=start)
        worker = self.worker()
        worker.process(shop_worker.read_requests(self.queue), now=start)
        original = details.read_snapshot(TICKET['id'], self.snapshot)
        later = start + 22000
        rail = self.enqueue(now=later)['shopifyRail']
        self.assertTrue(rail['refreshing'])
        def failure(*_):
            raise RuntimeError('unavailable')
        worker.graphql = failure
        worker.process(shop_worker.read_requests(self.queue), now=later)
        rail = self.enqueue(now=later + 1)['shopifyRail']
        self.assertTrue(rail['refreshError'])
        self.assertEqual(rail['customer']['id'], CUSTOMER['id'])
        self.assertEqual(rail['fetchedAtEpoch'], original['fetchedAtEpoch'])
        self.assertEqual(worker.process(shop_worker.read_requests(self.queue), now=later + 2), 0)

    def test_token_failure_is_bounded_and_visible(self):
        self.enqueue()
        def failure(_):
            raise RuntimeError('token unavailable')
        worker = self.worker(mint=failure)
        worker.process(shop_worker.read_requests(self.queue))
        self.assertEqual(self.enqueue()['shopifyRail']['status'], 'error')
        self.assertEqual(worker.caches['lookups'], exporter.MAX_LOOKUPS)
        self.assertEqual(self.calls, [])

    def test_provider_miss_is_explicit_and_cached(self):
        self.enqueue()
        worker = self.worker(graphql=lambda *_: {})
        worker.process(shop_worker.read_requests(self.queue))
        self.assertEqual(self.enqueue()['shopifyRail']['status'], 'missing')
        self.assertEqual(worker.process(shop_worker.read_requests(self.queue)), 0)

    def test_snapshot_replacement_keeps_other_opened_tickets(self):
        for number in (123, 456):
            self.enqueue({**TICKET, 'id': f'gorgias:{number}'})
        self.worker().process(shop_worker.read_requests(self.queue))
        for number in (123, 456):
            self.assertIsNotNone(details.read_snapshot(f'gorgias:{number}', self.snapshot))

    def test_lookup_budget_blocks_further_network_calls(self):
        self.enqueue()
        worker = self.worker()
        worker.window_at = time.time()
        worker.caches['lookups'] = exporter.MAX_LOOKUPS
        self.assertEqual(worker.process(shop_worker.read_requests(self.queue)), 0)
        self.assertEqual(self.calls, [])

    def test_cached_gorgias_detail_reads_new_snapshot_without_refetch(self):
        ticket = copy.deepcopy(TICKET)
        with patch.object(live_api, 'DETAIL_CACHE', {'123': (time.time(), ticket)}), \
             patch.object(live_api, 'attach_customer_details', side_effect=lambda t: {**t, 'shopifyRail': {'status': 'found'}}) as attach, \
             patch.object(live_api, 'MCP', side_effect=AssertionError('Unexpected Gorgias request')):
            result = live_api.get_ticket(123)
        self.assertEqual(result['shopifyRail']['status'], 'found')
        attach.assert_called_once()


if __name__ == '__main__':
    unittest.main()
