"""Rail lookup contracts: ownership, retained data, bounded reads and refresh."""
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import export_shop_rail as exporter
from shop_rail import attach, display_payload

CUSTOMER = {'id': 'gid://shopify/Customer/1', 'displayName': 'Test Customer',
            'defaultEmailAddress': {'emailAddress': 'qa@example.com'}, 'numberOfOrders': 2,
            'amountSpent': {'amount': '0.00', 'currencyCode': 'CAD'}}
ORDER = {'id': 'gid://shopify/Order/2', 'name': '#10319148', 'email': 'qa@example.com',
         'customer': CUSTOMER, 'currentTotalPriceSet': {'shopMoney': {'amount': '0.00', 'currencyCode': 'CAD'}},
         'lineItems': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
         'returns': {'nodes': [{'id': 'r1', 'status': 'OPEN', 'totalQuantity': 1,
                               'returnLineItems': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
                               'exchangeLineItems': {'nodes': []}}],
                     'pageInfo': {'hasNextPage': False}}}
TICKET = {'id': 'gorgias:1', 'subject': 'Re: Order 10319148', 'fromEmail': 'qa@example.com'}


class ShopRailTests(unittest.TestCase):
    def caches(self, customer=CUSTOMER, order=ORDER, history=None, order_page=False, history_page=False):
        self.calls = []
        def graphql(env, token, document, variables):
            self.calls.append((document, variables))
            if document == exporter.CUSTOMER_BY_EMAIL:
                return {'customers': {'nodes': [customer] if customer else []}}
            if document == exporter.ORDER_BY_NAME:
                return {'orders': {'nodes': [order] if order else [], 'pageInfo': {'hasNextPage': order_page}}}
            return {'customer': {'orders': {'nodes': history or [], 'pageInfo': {'hasNextPage': history_page}}}}
        return {'graphql': graphql, 'customers': {}, 'orders': {}, 'history': {}, 'lookups': 0}

    def test_exact_order_is_retained_with_returns_and_customer(self):
        result, _ = exporter.lookup_ticket({}, '', TICKET, self.caches())
        self.assertEqual(result['order']['name'], '#10319148')
        self.assertEqual(result['returns']['returns']['nodes'][0]['status'], 'OPEN')
        self.assertEqual(result['customer']['displayName'], 'Test Customer')
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.calls[0][1]['query'], 'email:"qa@example.com"')

    def test_payload_names_the_store_scope_it_was_built_for(self):
        """#48: the snapshot must name the one store it was built for."""
        result, _ = exporter.lookup_ticket({'SHOPIFY_SHOP': 'buttons-bebe.myshopify.com'}, '', TICKET, self.caches())
        self.assertEqual(result['shop'], 'buttons-bebe.myshopify.com')

    def test_returns_export_queried_item_details_and_exchanges(self):
        order = {**ORDER, 'returns': {'nodes': [{
            'id': 'r1', 'name': '#1001-1', 'status': 'OPEN', 'totalQuantity': 2,
            'createdAt': '2026-09-01T00:00:00Z',
            'returnLineItems': {'nodes': [{
                '__typename': 'ReturnLineItem', 'id': 'rli1', 'quantity': 2,
                'returnReasonDefinition': {'name': 'Wrong size'},
                'returnReasonNote': 'too small',
                'fulfillmentLineItem': {'lineItem': {'title': 'Teal button 2-pack'}},
            }], 'pageInfo': {'hasNextPage': False}},
            'exchangeLineItems': {'nodes': []},
        }], 'pageInfo': {'hasNextPage': False}}}
        result, _ = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order))
        exported = result['returns']['returns']['nodes'][0]
        self.assertEqual(exported['createdAt'], '2026-09-01T00:00:00Z')
        self.assertEqual(exported['returnType'], 'RETURN')
        self.assertEqual(exported['items'][0]['title'], 'Teal button 2-pack')
        self.assertEqual(exported['items'][0]['quantity'], 2)
        self.assertNotIn('price', exported['items'][0])
        self.assertEqual(exported['items'][0]['reason'], 'Wrong size')
        self.assertEqual(exported['items'][0]['note'], 'too small')
        # An exchange shows as an exchange, not a plain return.
        order_exchange = {**ORDER, 'returns': {'nodes': [{
            'id': 'r1', 'status': 'OPEN', 'totalQuantity': 1, 'createdAt': None,
            'returnLineItems': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
            'exchangeLineItems': {'nodes': [{'id': 'x1'}, {'id': 'x2'}]},
        }], 'pageInfo': {'hasNextPage': False}}}
        exchanged = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order_exchange))[0]['returns']['returns']['nodes'][0]
        self.assertEqual(exchanged['returnType'], 'EXCHANGE')
        self.assertEqual(exchanged['items'], [])

    def test_returns_export_marks_truncated_item_lists(self):
        page = [{'__typename': 'ReturnLineItem', 'id': f'rli{i}'} for i in range(25)]
        order = {**ORDER, 'returns': {'nodes': [{
            'id': 'r1', 'status': 'OPEN', 'totalQuantity': 31, 'createdAt': None,
            'returnLineItems': {'nodes': page, 'pageInfo': {'hasNextPage': True}},
            'exchangeLineItems': {'nodes': []},
        }], 'pageInfo': {'hasNextPage': False}}}
        exported = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order))[0]['returns']['returns']['nodes'][0]
        self.assertEqual(len(exported['items']), 25)
        self.assertTrue(exported['itemsTruncated'])
        order_full = {**ORDER, 'returns': {'nodes': [{
            'id': 'r1', 'status': 'OPEN', 'totalQuantity': 2, 'createdAt': None,
            'returnLineItems': {'nodes': page[:2], 'pageInfo': {'hasNextPage': False}},
            'exchangeLineItems': {'nodes': []},
        }], 'pageInfo': {'hasNextPage': False}}}
        full = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order_full))[0]['returns']['returns']['nodes'][0]
        self.assertFalse(full['itemsTruncated'])
        order_unknown = {**ORDER, 'returns': {'nodes': [{
            'id': 'r1', 'status': 'OPEN', 'totalQuantity': 2,
            'returnLineItems': {'nodes': page[:2]}, 'exchangeLineItems': {'nodes': []},
        }], 'pageInfo': {'hasNextPage': False}}}
        unknown = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order_unknown))[0]['returns']['returns']['nodes'][0]
        self.assertIsNone(unknown['itemsTruncated'])

    def test_money_requires_observed_amount_and_currency_and_keeps_real_zero(self):
        history = [{'id': 'history', 'name': '#1000',
                    'currentTotalPriceSet': {'shopMoney': {'amount': '0', 'currencyCode': 'CAD'}}}]
        order = {
            **ORDER,
            'lineItems': {'nodes': [
                {'title': 'Observed zero', 'originalUnitPriceSet': {'shopMoney': {'amount': 0, 'currencyCode': 'CAD'}}},
                {'title': 'Missing currency', 'originalUnitPriceSet': {'shopMoney': {'amount': '4.00'}}},
            ], 'pageInfo': {'hasNextPage': False}},
        }
        payload = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order, history=history))[0]
        self.assertEqual(payload['customer']['amountSpent'], {'amount': '0.00', 'currencyCode': 'CAD'})
        self.assertEqual(payload['order']['currentTotalPriceSet'], {'shopMoney': {'amount': '0.00', 'currencyCode': 'CAD'}})
        self.assertEqual(payload['order']['lineItems']['nodes'][0]['originalUnitPriceSet'], {'shopMoney': {'amount': '0', 'currencyCode': 'CAD'}})
        self.assertIsNone(payload['order']['lineItems']['nodes'][1]['originalUnitPriceSet'])
        self.assertEqual(payload['history'][0]['currentTotalPriceSet'], {'shopMoney': {'amount': '0', 'currencyCode': 'CAD'}})

        missing_customer = {**CUSTOMER, 'numberOfOrders': None, 'amountSpent': {'amount': 'invalid', 'currencyCode': 'USD'}}
        missing_order = {
            **ORDER,
            'currentTotalPriceSet': {'shopMoney': {'currencyCode': 'USD'}},
            'lineItems': {'nodes': [{'title': 'No price'}], 'pageInfo': {'hasNextPage': False}},
        }
        missing_history = [{'id': 'history', 'currentTotalPriceSet': {'shopMoney': {'amount': '3.00'}}}]
        missing = exporter.lookup_ticket({}, '', TICKET, self.caches(
            customer=missing_customer, order=missing_order, history=missing_history))[0]
        self.assertIsNone(missing['customer']['numberOfOrders'])
        self.assertIsNone(missing['customer']['amountSpent'])
        self.assertIsNone(missing['order']['currentTotalPriceSet'])
        self.assertIsNone(missing['order']['lineItems']['nodes'][0]['originalUnitPriceSet'])
        self.assertIsNone(missing['history'][0]['currentTotalPriceSet'])

    def test_partial_flags_preserve_true_false_and_unknown(self):
        order = {
            **ORDER,
            'lineItems': {'nodes': [], 'pageInfo': {'hasNextPage': True}},
            'returns': {'nodes': [], 'pageInfo': {'hasNextPage': False}},
        }
        payload = exporter.lookup_ticket({}, '', TICKET, self.caches(
            order=order, history=[], order_page=True, history_page=False))[0]
        self.assertEqual(payload['partial'], {
            'orderSearch': True,
            'orderItems': True,
            'returns': False,
            'history': False,
        })
        unknown_order = {**order, 'lineItems': {'nodes': []}, 'returns': {'nodes': []}}
        unknown = exporter.lookup_ticket({}, '', TICKET, self.caches(
            order=unknown_order, history=[], order_page=False, history_page=False))[0]
        self.assertIsNone(unknown['partial']['orderItems'])
        self.assertIsNone(unknown['partial']['returns'])

    def test_the_order_query_caps_return_fan_out(self):
        """cubic: 20 returns x 50 nested line items inflates the Admin GraphQL
        requested-cost of a single order lookup toward the 1,000-point hard
        cap. The pane renders a handful — the query reads only that."""
        for banned in ('returns(first: 20)', 'returnLineItems(first: 50)', 'exchangeLineItems(first: 50)'):
            self.assertNotIn(banned, exporter.ORDER_BY_NAME)
        for capped in ('returns(first: 5)', 'returnLineItems(first: 25)', 'exchangeLineItems(first: 5)'):
            self.assertIn(capped, exporter.ORDER_BY_NAME)
        self.assertIn('orders(first: 1, query: $query) {\n    pageInfo { hasNextPage }', exporter.ORDER_BY_NAME)
        self.assertIn('lineItems(first: 50) { pageInfo { hasNextPage }', exporter.ORDER_BY_NAME)

    def test_wrong_owner_or_missing_first_match_still_reports_incomplete_search(self):
        wrong = {**ORDER, 'email':'other@example.com', 'customer':{'id':'other'}}
        for order in (wrong, None):
            with self.subTest(order=order):
                payload = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order, order_page=True))[0]
                self.assertIsNone(payload['order'])
                self.assertTrue(payload['partial']['orderSearch'])
        self.assertIn('returns(first: 5) { pageInfo { hasNextPage }', exporter.ORDER_BY_NAME)
        self.assertIn('returnLineItems(first: 25) { pageInfo { hasNextPage }', exporter.ORDER_BY_NAME)
        self.assertIn('orders(first: 50, sortKey: CREATED_AT, reverse: true) {\n      pageInfo { hasNextPage }', exporter.PAST_ORDERS)

    def test_the_upstream_error_fallback_still_names_the_store_scope(self):
        """cubic: every exported snapshot names the store, including the
        error fallback a failed refresh writes for an uncached ticket."""
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            def failed(*args):
                raise RuntimeError('upstream unavailable')
            with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                 patch.object(exporter, 'load_shopify_env', return_value={'SHOPIFY_SHOP': 'buttons-bebe.myshopify.com'}):
                exporter.export('', dest, '', now=1000, graphql_call=failed, mint=lambda _: '')
                entry = exporter.load_cache(dest)[TICKET['id']]
                self.assertEqual(entry['payload']['status'], 'error')
                self.assertTrue(entry['payload']['refreshError'])
                self.assertEqual(entry['payload']['shop'], 'buttons-bebe.myshopify.com')

    def test_wrong_customer_order_and_fuzzy_order_name_are_not_attached(self):
        for changes in ({'name': '#103191480'}, {'customer': {'id': 'other'}, 'email': 'other@example.com'}):
            order = {**ORDER, **changes}
            result, _ = exporter.lookup_ticket({}, '', TICKET, self.caches(order=order))
            self.assertIsNone(result['order'])
            self.assertIsNone(result['returns'])

    def test_conflicting_identity_and_query_injection_fail_closed(self):
        for ticket in ({**TICKET, 'customerContext': {'conflict': True}},
                       {**TICKET, 'fromEmail': 'x" OR email:*@example.com'}):
            result, _ = exporter.lookup_ticket({}, '', ticket, self.caches())
            self.assertEqual(result['status'], 'missing')
            self.assertEqual(self.calls, [])

    def test_no_fabricated_customer_when_only_guest_order_matches(self):
        result, _ = exporter.lookup_ticket({}, '', TICKET, self.caches(customer=None))
        self.assertIsNotNone(result['order'])
        self.assertIsNone(result['customer'])
        self.assertEqual(result['history'], [])

    def test_request_budget_counts_failures_and_prevents_extra_call(self):
        cache = self.caches()
        cache['lookups'] = exporter.MAX_LOOKUPS
        with self.assertRaises(exporter.LookupBudgetExceeded):
            exporter.lookup_ticket({}, '', TICKET, cache)
        self.assertEqual(self.calls, [])

    def test_only_fixed_read_queries_can_reach_network(self):
        with self.assertRaises(RuntimeError):
            exporter.graphql({}, '', 'query Bad { shop { id } } mutation Bad { x }', {})

    def test_refresh_preserves_old_timestamp_and_identity_change_invalidates(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                 patch.object(exporter, 'load_shopify_env', return_value={}):
                cache = self.caches()
                exporter.export('', dest, '', now=1000, graphql_call=cache['graphql'], mint=lambda _: '')
                exporter.export('', dest, '', now=1100, graphql_call=cache['graphql'], mint=lambda _: '')
                self.assertEqual(exporter.load_cache(dest)[TICKET['id']]['updated_at'], 1000)
                self.assertEqual(len(self.calls), 3)
                exporter.export('', dest, '', now=23000, graphql_call=cache['graphql'], mint=lambda _: '')
                self.assertEqual(exporter.load_cache(dest)[TICKET['id']]['updated_at'], 23000)
                self.assertEqual(len(self.calls), 6)
                ticket = copy.deepcopy(TICKET)
                self.assertIn('shopifyRail', attach(ticket, dest))
                changed = {**TICKET, 'fromEmail': 'other@example.com'}
                self.assertNotIn('shopifyRail', attach(changed, dest))

    def test_legacy_snapshot_hides_unverified_money_and_refreshes_with_current_version(self):
        legacy = {
            'status': 'found', 'email': TICKET['fromEmail'], 'keysHash': exporter.keys_hash(TICKET),
            'fetchedAtEpoch': 1000,
            'customer': {**CUSTOMER, 'amountSpent': {'amount': '0.0', 'currencyCode': 'USD'}},
            'order': {**ORDER, 'currentTotalPriceSet': {'shopMoney': {'amount': '0.0', 'currencyCode': 'USD'}},
                      'lineItems': {'nodes': [{'originalUnitPriceSet': {'shopMoney': {'amount': '0.0', 'currencyCode': 'USD'}}}]}},
            'history': [{'id': 'old', 'currentTotalPriceSet': {'shopMoney': {'amount': '0.0', 'currencyCode': 'USD'}}}],
        }
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            with sqlite3.connect(dest) as db:
                db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
                db.execute('INSERT INTO rail VALUES(?,?,?)', (TICKET['id'], json.dumps(legacy), 1000))
            displayed = attach(copy.deepcopy(TICKET), dest)['shopifyRail']
            self.assertTrue(displayed['legacyMoneyUnverified'])
            self.assertIsNone(displayed['customer']['amountSpent'])
            self.assertIsNone(displayed['order']['currentTotalPriceSet'])
            self.assertIsNone(displayed['order']['lineItems']['nodes'][0]['originalUnitPriceSet'])
            self.assertIsNone(displayed['history'][0]['currentTotalPriceSet'])

            with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                 patch.object(exporter, 'load_shopify_env', return_value={}):
                cache = self.caches()
                exporter.export('', dest, '', now=1100, graphql_call=cache['graphql'], mint=lambda _: '')
            refreshed = exporter.load_cache(dest)[TICKET['id']]['payload']
            self.assertEqual(refreshed['payloadVersion'], exporter.PAYLOAD_VERSION)
            self.assertEqual(len(self.calls), 3)

    def test_legacy_malformed_collections_withhold_unknown_data_without_attachment_failure(self):
        for invalid in (7, 'not-a-list', {'unexpected':'object'}, [None, 7]):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp:
                legacy = {'status':'found', 'email':TICKET['fromEmail'],
                    'customer':CUSTOMER, 'order':{**ORDER, 'lineItems':{'nodes':invalid}},
                    'history':invalid, 'returns':{'returns':{'nodes':invalid}}}
                dest = Path(temp) / 'rail.sqlite3'
                with sqlite3.connect(dest) as db:
                    db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
                    db.execute('INSERT INTO rail VALUES(?,?,?)', (TICKET['id'], json.dumps(legacy), 1000))
                rail = attach(dict(TICKET), dest)['shopifyRail']
                self.assertFalse(rail['history'])
                self.assertFalse(rail['order']['lineItems']['nodes'])
                self.assertFalse(rail['returns']['returns']['nodes'])
                self.assertTrue(rail['legacyMoneyUnverified'])
                self.assertTrue(all(flag is None for flag in rail['partial'].values()))
                self.assertIsNone(rail['order']['currentTotalPriceSet'])

    def test_legacy_return_prices_are_hidden_and_malformed_items_are_withheld(self):
        legacy = {'returns':{'returns':{'nodes':[
            {'id':'retained', 'items':[{'title':'Button', 'price':{'amount':'0.0', 'currencyCode':'USD'}}, None]},
            {'id':'unknown', 'items':7}]}}}
        displayed = display_payload(legacy)
        returned = displayed['returns']['returns']['nodes']
        self.assertEqual(returned[0]['id'], 'retained')
        self.assertEqual(returned[0]['items'], [{'title':'Button', 'price':None}])
        self.assertIsNone(returned[1]['items'])
        self.assertEqual(legacy['returns']['returns']['nodes'][0]['items'][0]['price']['currencyCode'], 'USD')

    def test_failed_legacy_refresh_caches_attempt_without_trusting_old_money(self):
        legacy = {
            'status': 'found', 'email': TICKET['fromEmail'], 'keysHash': exporter.keys_hash(TICKET),
            'fetchedAt': '2026-09-01T00:00:00Z', 'fetchedAtEpoch': 1000,
            'customer': CUSTOMER, 'order': ORDER,
            'history': [{'id': 'old', 'currentTotalPriceSet': ORDER['currentTotalPriceSet']}],
            'returns': {'returns': {'nodes': [{'id': 'old-return', 'items': [
                {'title': 'Old item', 'price': {'amount': '0', 'currencyCode': 'USD'}}]}]}},
        }
        calls = []
        def failed(*args):
            calls.append(args)
            raise TimeoutError('synthetic outage')
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            with sqlite3.connect(dest) as db:
                db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
                db.execute('INSERT INTO rail VALUES(?,?,?)', (TICKET['id'], json.dumps(legacy), 1000))
            with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                 patch.object(exporter, 'load_shopify_env', return_value={}):
                exporter.export('', dest, '', now=23000, graphql_call=failed, mint=lambda _: '')
                entry = exporter.load_cache(dest)[TICKET['id']]
                payload = entry['payload']
                self.assertEqual(payload['payloadVersion'], exporter.PAYLOAD_VERSION)
                self.assertEqual(entry['updated_at'], 1000)
                self.assertEqual(payload['fetchedAt'], legacy['fetchedAt'])
                self.assertEqual(payload['fetchedAtEpoch'], 1000)
                self.assertEqual(payload['history'][0]['id'], 'old')
                self.assertIsNone(payload['customer']['amountSpent'])
                self.assertIsNone(payload['order']['currentTotalPriceSet'])
                self.assertIsNone(payload['history'][0]['currentTotalPriceSet'])
                self.assertIsNone(payload['returns']['returns']['nodes'][0]['items'][0]['price'])
                self.assertTrue(payload['legacyMoneyUnverified'])
                self.assertTrue(all(value is None for value in payload['partial'].values()))
                self.assertTrue(attach(dict(TICKET), dest)['shopifyRail']['stale'])
                retry_at = 23000 + exporter.CACHE_MISS_SECONDS
                self.assertEqual(payload['retryAt'], retry_at)
                exporter.export('', dest, '', now=retry_at - 1, graphql_call=failed, mint=lambda _: '')
                self.assertEqual(len(calls), 1)
                exporter.export('', dest, '', now=retry_at, graphql_call=failed, mint=lambda _: '')
                self.assertEqual(len(calls), 2)
                # A successful refresh replaces the failure and legacy warnings.
                exporter.export('', dest, '', now=retry_at + exporter.CACHE_MISS_SECONDS,
                                graphql_call=self.caches()['graphql'], mint=lambda _: '')
                refreshed = exporter.load_cache(dest)[TICKET['id']]['payload']
                self.assertNotIn('refreshError', refreshed)
                self.assertNotIn('legacyMoneyUnverified', refreshed)
                self.assertEqual(refreshed['customer']['amountSpent'], CUSTOMER['amountSpent'])

    def test_upstream_failure_keeps_previous_details_and_marks_refresh_error(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                 patch.object(exporter, 'load_shopify_env', return_value={}):
                exporter.export('', dest, '', now=1000, graphql_call=self.caches()['graphql'], mint=lambda _: '')
                def failed(*args):
                    raise RuntimeError('upstream unavailable')
                exporter.export('', dest, '', now=23000, graphql_call=failed, mint=lambda _: '')
                entry = exporter.load_cache(dest)[TICKET['id']]
                self.assertEqual(entry['updated_at'], 1000)
                self.assertEqual(entry['payload']['order']['name'], '#10319148')
                self.assertTrue(entry['payload']['refreshError'])
                self.assertTrue(attach(dict(TICKET), dest)['shopifyRail']['stale'])

    def test_failed_legacy_refresh_names_current_store_or_retains_known_store(self):
        current_shop = 'current-synthetic.myshopify.com'
        old_shop = 'old-synthetic.myshopify.com'
        for old_value, env, expected in (
            (None, {'SHOPIFY_SHOP': current_shop}, current_shop),
            (old_shop, {'SHOPIFY_SHOP': current_shop}, current_shop),
            (old_shop, {}, old_shop),
        ):
            with self.subTest(old_shop=old_value, env=env), tempfile.TemporaryDirectory() as temp:
                legacy = {'status': 'found', 'email': TICKET['fromEmail'],
                          'keysHash': exporter.keys_hash(TICKET), 'customer': CUSTOMER}
                if old_value:
                    legacy['shop'] = old_value
                dest = Path(temp) / 'rail.sqlite3'
                with sqlite3.connect(dest) as db:
                    db.execute('CREATE TABLE rail(ticket_id TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at REAL NOT NULL)')
                    db.execute('INSERT INTO rail VALUES(?,?,?)', (TICKET['id'], json.dumps(legacy), 1000))
                def failed(*args):
                    raise TimeoutError('synthetic outage')
                with patch.object(exporter, 'read_projection_tickets', return_value=[TICKET]), \
                     patch.object(exporter, 'load_shopify_env', return_value=env):
                    exporter.export('', dest, '', now=23000, graphql_call=failed, mint=lambda _: '')
                payload = exporter.load_cache(dest)[TICKET['id']]['payload']
                self.assertEqual(payload['shop'], expected)
                self.assertIsNone(payload['customer']['amountSpent'])
                self.assertEqual(payload['payloadVersion'], exporter.PAYLOAD_VERSION)
                self.assertEqual(payload['retryAt'], 23000 + exporter.CACHE_MISS_SECONDS)

    def test_bad_or_absent_snapshot_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            self.assertNotIn('shopifyRail', attach(dict(TICKET), dest))
            dest.write_text('corrupt')
            self.assertNotIn('shopifyRail', attach(dict(TICKET), dest))


if __name__ == '__main__':
    unittest.main()
