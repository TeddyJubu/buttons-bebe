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
from shop_rail import attach

CUSTOMER = {'id': 'gid://shopify/Customer/1', 'displayName': 'Test Customer',
            'defaultEmailAddress': {'emailAddress': 'qa@example.com'}, 'numberOfOrders': '2'}
ORDER = {'id': 'gid://shopify/Order/2', 'name': '#10319148', 'email': 'qa@example.com',
         'customer': CUSTOMER, 'lineItems': {'nodes': []},
         'returns': {'nodes': [{'id': 'r1', 'status': 'OPEN', 'totalQuantity': 1}]}}
TICKET = {'id': 'gorgias:1', 'subject': 'Re: Order 10319148', 'fromEmail': 'qa@example.com'}


class ShopRailTests(unittest.TestCase):
    def caches(self, customer=CUSTOMER, order=ORDER):
        self.calls = []
        def graphql(env, token, document, variables):
            self.calls.append((document, variables))
            if document == exporter.CUSTOMER_BY_EMAIL:
                return {'customers': {'nodes': [customer] if customer else []}}
            if document == exporter.ORDER_BY_NAME:
                return {'orders': {'nodes': [order] if order else []}}
            return {'customer': {'orders': {'nodes': []}}}
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

    def test_bad_or_absent_snapshot_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / 'rail.sqlite3'
            self.assertNotIn('shopifyRail', attach(dict(TICKET), dest))
            dest.write_text('corrupt')
            self.assertNotIn('shopifyRail', attach(dict(TICKET), dest))


if __name__ == '__main__':
    unittest.main()
