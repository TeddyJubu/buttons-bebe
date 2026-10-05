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
CUSTOMER = {'id': 'gid://shopify/Customer/1', 'defaultEmailAddress': {'emailAddress': 'person@example.com'},
            'displayName': 'Example Customer', 'amountSpent': {'amount': '0.00', 'currencyCode': 'CAD'}}
ORDER = {'id': 'gid://shopify/Order/1', 'name': '#10312345', 'customer': CUSTOMER,
         'email': 'person@example.com', 'currentTotalPriceSet': {'shopMoney': {'amount': '0.00', 'currencyCode': 'CAD'}},
         'lineItems': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
         'returns': {'nodes': [], 'pageInfo': {'hasNextPage': False}}}


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
            return {'orders': {'nodes': [ORDER], 'pageInfo': {'hasNextPage': False}}}
        if document == exporter.PAST_ORDERS:
            return {'customer': {'orders': {'nodes': [ORDER], 'pageInfo': {'hasNextPage': False}}}}
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
        ticket = {**TICKET, 'shopifyRail': {'status': 'found',
                  'customer': {**CUSTOMER, 'amountSpent': {'amount': '0.0', 'currencyCode': 'USD'}},
                  'stale': False}}
        rail = self.enqueue(ticket)['shopifyRail']
        self.assertTrue(rail['refreshing'])
        self.assertTrue(rail['legacyMoneyUnverified'])
        self.assertIsNone(rail['customer']['amountSpent'])
        self.worker().process(shop_worker.read_requests(self.queue))
        self.assertEqual(self.enqueue(ticket)['shopifyRail']['order']['id'], ORDER['id'])

    def test_saved_worker_snapshot_version_invalidates_and_refreshes(self):
        now = time.time()
        request = details.request_ticket(TICKET)
        key = details.request_key(request)
        legacy = {
            'status': 'found', 'email': TICKET['fromEmail'], 'requestKey': key,
            'fetchedAtEpoch': now, 'attemptedAt': now - 1,
            'customer': {**CUSTOMER, 'amountSpent': {'amount': '0.0', 'currencyCode': 'USD'}},
            'order': {**ORDER, 'currentTotalPriceSet': {'shopMoney': {'amount': '0.0', 'currencyCode': 'USD'}}},
            'history': [],
        }
        shop_worker.publish({TICKET['id']: {'payload': legacy, 'updated_at': now}}, self.snapshot)
        rail = self.enqueue(now=now)['shopifyRail']
        self.assertTrue(rail['refreshing'])
        self.assertTrue(rail['legacyMoneyUnverified'])
        self.assertIsNone(rail['customer']['amountSpent'])
        worker = self.worker()
        self.assertEqual(worker.process(shop_worker.read_requests(self.queue), now=now + 1), 1)
        refreshed = self.enqueue(now=now + 2)['shopifyRail']
        self.assertEqual(refreshed['payloadVersion'], exporter.PAYLOAD_VERSION)
        self.assertEqual(refreshed['customer']['amountSpent'], {'amount': '0.00', 'currencyCode': 'CAD'})

    def test_failed_legacy_refresh_preserves_backoff_across_inbox_polls(self):
        for offset, version in enumerate((None, 'older-version')):
            with self.subTest(version=version):
                start = time.time() + offset * 1000
                request = details.request_ticket(TICKET)
                key = details.request_key(request)
                legacy = {
                    'status': 'found', 'email': TICKET['fromEmail'], 'requestKey': key,
                    'fetchedAtEpoch': start - 22000, 'attemptedAt': start - 1,
                    'refreshError': True, 'failures': 2, 'retryAt': start - 1,
                    'customer': {**CUSTOMER, 'amountSpent': {'amount': '0.0', 'currencyCode': 'USD'}},
                    'order': {**ORDER, 'currentTotalPriceSet': {'shopMoney': {'amount': '0.0', 'currencyCode': 'USD'}}},
                    'history': [copy.deepcopy(ORDER)],
                }
                if version is not None:
                    legacy['payloadVersion'] = version
                shop_worker.publish({TICKET['id']: {'payload': legacy, 'updated_at': start}}, self.snapshot)
                self.enqueue(now=start)
                calls = []

                def unavailable(*args):
                    calls.append(args)
                    raise RuntimeError('Shopify unavailable')

                worker = shop_worker.Worker({'SHOPIFY_SHOP': 'current-synthetic.myshopify.com'},
                                           self.snapshot, graphql=unavailable, mint=lambda _: 'test-token')
                self.assertEqual(worker.process(shop_worker.read_requests(self.queue), now=start + 1), 1)
                failed = details.read_snapshot(TICKET['id'], self.snapshot)
                self.assertEqual(failed['payloadVersion'], exporter.PAYLOAD_VERSION)
                self.assertEqual(failed['failures'], 3)
                self.assertEqual(failed['retryAt'], start + 121)
                self.assertEqual(failed['attemptedAt'], start + 1)
                self.assertTrue(failed['refreshError'])
                self.assertEqual(failed['shop'], 'current-synthetic.myshopify.com')
                self.assertEqual(failed['fetchedAtEpoch'], legacy['fetchedAtEpoch'])
                self.assertEqual(failed['customer']['id'], CUSTOMER['id'])
                self.assertEqual(failed['order']['id'], ORDER['id'])
                self.assertEqual(failed['history'][0]['id'], ORDER['id'])
                self.assertTrue(failed['legacyMoneyUnverified'])
                self.assertIsNone(failed['customer']['amountSpent'])
                self.assertIsNone(failed['order']['currentTotalPriceSet'])
                self.assertIsNone(failed['history'][0]['currentTotalPriceSet'])
                for poll in (31, 61, 91):
                    rail = self.enqueue(now=start + poll)['shopifyRail']
                    self.assertTrue(rail['refreshError'])
                    requests = shop_worker.read_requests(self.queue)
                    self.assertEqual(requests[0][2], start)
                    self.assertEqual(worker.process(requests, now=start + poll), 0)
                self.assertEqual(len(calls), 1)
                self.enqueue(now=failed['retryAt'] + 1)
                self.assertEqual(worker.process(shop_worker.read_requests(self.queue), now=failed['retryAt'] + 1), 1)
                self.assertEqual(len(calls), 2)
                self.assertEqual(details.read_snapshot(TICKET['id'], self.snapshot)['failures'], 4)

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

    def store_snapshot(self, shop, now):
        request = details.request_ticket(TICKET)
        payload = {'payloadVersion': exporter.PAYLOAD_VERSION, 'status': 'found',
                   'email': TICKET['fromEmail'], 'requestKey': details.request_key(request),
                   'fetchedAtEpoch': now, 'attemptedAt': now + 100,
                   'customer': copy.deepcopy(CUSTOMER), 'order': copy.deepcopy(ORDER),
                   'history': [{'id': 'prior-history'}],
                   'returns': {'returns': {'nodes': [{'id': 'prior-return'}]}}}
        if shop:
            payload['shop'] = shop
        return {'payload': payload, 'updated_at': now}

    def test_worker_startup_removes_other_store_and_keeps_matching_and_legacy_rows(self):
        now = time.time()
        current = 'current-synthetic.myshopify.com'
        old = self.store_snapshot('old-synthetic.myshopify.com', now)
        matching = self.store_snapshot(current, now)
        legacy = self.store_snapshot(None, now)
        shop_worker.publish({TICKET['id']: old, 'gorgias:matching': matching,
                             'gorgias:legacy': legacy}, self.snapshot)
        worker = shop_worker.Worker({'SHOPIFY_SHOP': current}, self.snapshot,
                                   graphql=self.graphql, mint=lambda _: 'test-token')
        self.assertNotIn(TICKET['id'], worker.cache)
        self.assertIsNone(details.read_snapshot(TICKET['id'], self.snapshot))
        self.assertEqual(details.read_snapshot('gorgias:matching', self.snapshot), matching['payload'])
        self.assertEqual(details.read_snapshot('gorgias:legacy', self.snapshot), legacy['payload'])
        self.assertEqual(worker.process([], now=now), 0)
        self.assertEqual(self.calls, [])

    def test_old_store_startup_removal_does_not_require_available_lookup_budget(self):
        now = time.time()
        shop_worker.publish({TICKET['id']: self.store_snapshot('old-synthetic.myshopify.com', now)}, self.snapshot)
        worker = shop_worker.Worker({'SHOPIFY_SHOP': 'current-synthetic.myshopify.com'}, self.snapshot,
                                   graphql=self.graphql, mint=lambda _: 'test-token')
        self.assertIsNone(details.read_snapshot(TICKET['id'], self.snapshot))
        worker.window_at = now
        worker.caches['lookups'] = exporter.MAX_LOOKUPS
        request = details.request_ticket(TICKET)
        self.assertEqual(worker.process([(request, details.request_key(request), now)], now=now), 0)
        self.assertIsNone(details.read_snapshot(TICKET['id'], self.snapshot))
        self.assertEqual(self.calls, [])

    def test_matching_store_fresh_snapshot_is_retained_without_lookup_or_republication(self):
        now = time.time()
        current = 'current-synthetic.myshopify.com'
        original = self.store_snapshot(current, now)
        shop_worker.publish({TICKET['id']: original}, self.snapshot)
        with patch.object(shop_worker, 'publish', wraps=shop_worker.publish) as publish:
            worker = shop_worker.Worker({'SHOPIFY_SHOP': current}, self.snapshot,
                                       graphql=self.graphql, mint=lambda _: 'test-token')
            request = details.request_ticket(TICKET)
            self.assertEqual(worker.process([(request, details.request_key(request), now)], now=now), 0)
            publish.assert_not_called()
        self.assertEqual(details.read_snapshot(TICKET['id'], self.snapshot), original['payload'])
        self.assertEqual(self.calls, [])

    def test_old_store_fresh_snapshot_cannot_survive_outage_as_current_store_data(self):
        now = time.time()
        current = 'current-synthetic.myshopify.com'
        shop_worker.publish({TICKET['id']: self.store_snapshot('old-synthetic.myshopify.com', now)}, self.snapshot)
        calls = []
        def unavailable(*args):
            calls.append(args)
            raise TimeoutError('synthetic outage')
        worker = shop_worker.Worker({'SHOPIFY_SHOP': current}, self.snapshot,
                                   graphql=unavailable, mint=lambda _: 'test-token')
        self.assertIsNone(details.read_snapshot(TICKET['id'], self.snapshot))
        request = details.request_ticket(TICKET)
        self.assertEqual(worker.process([(request, details.request_key(request), now)], now=now), 1)
        payload = details.read_snapshot(TICKET['id'], self.snapshot)
        self.assertEqual(payload['shop'], current)
        self.assertEqual(payload['status'], 'error')
        self.assertTrue(payload['refreshError'])
        self.assertEqual(payload['failures'], 1)
        self.assertEqual(payload['retryAt'], now + 30)
        for field in ('customer', 'order', 'history', 'returns', 'fetchedAtEpoch'):
            self.assertNotIn(field, payload)
        self.assertEqual(len(calls), 1)

    def test_process_rejects_reintroduced_other_store_before_freshness_or_budget(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                now = time.time()
                calls = []
                def unavailable(*args):
                    calls.append(args)
                    raise TimeoutError('synthetic outage')
                worker = shop_worker.Worker({'SHOPIFY_SHOP': 'current-synthetic.myshopify.com'}, self.snapshot,
                                           graphql=unavailable, mint=lambda _: 'test-token')
                # Exercise the per-request guard independently of startup.
                worker.cache[TICKET['id']] = self.store_snapshot('old-synthetic.myshopify.com', now)
                shop_worker.publish(worker.cache, self.snapshot)
                worker.window_at = now
                if exhausted:
                    worker.caches['lookups'] = exporter.MAX_LOOKUPS
                request = details.request_ticket(TICKET)
                self.assertEqual(worker.process([(request, details.request_key(request), now)], now=now),
                                 0 if exhausted else 1)
                payload = details.read_snapshot(TICKET['id'], self.snapshot)
                if exhausted:
                    self.assertIsNone(payload)
                    self.assertEqual(calls, [])
                else:
                    self.assertEqual(payload['shop'], 'current-synthetic.myshopify.com')
                    self.assertEqual(payload['status'], 'error')
                    for field in ('customer', 'order', 'history', 'returns'):
                        self.assertNotIn(field, payload)
                    self.assertEqual(len(calls), 1)

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
