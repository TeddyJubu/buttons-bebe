"""Exercise production MCP functions without importing credential-loading module."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from tools.gorgias_content import curate_messages, curate_ticket

SOURCE = Path(__file__).with_name('gorgias_mcp.py')

def functions():
    tree = ast.parse(SOURCE.read_text())
    selected = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in {'_positive_int','get_ticket','get_customer','get_ticket_messages','list_recent_tickets','list_inbox_tickets'}:
            node.decorator_list = []
            selected.append(node)
    get = Mock(return_value={'data': [], 'meta': {'next_cursor': 'next-page'}})
    scope = {'StrictInt': int, '_get': get, 'curate_messages': curate_messages, 'curate_ticket': curate_ticket}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), scope)
    return scope, get

class GorgiasReadContractTests(unittest.TestCase):
    def test_messages_use_documented_filtered_page_and_preserve_cursor(self):
        scope, get = functions()
        result = scope['get_ticket_messages'](123, 100, 'opaque-next')
        get.assert_called_once_with('/messages', {'ticket_id':123,'limit':50,'order_by':'created_datetime:desc','cursor':'opaque-next'})
        self.assertEqual(result['meta']['next_cursor'], 'next-page')
    def test_bad_ids_and_limits_never_reach_provider(self):
        scope, get = functions()
        for bad in (True, False, 0, -1, float('nan'), float('inf'), 1.5, '123', 2**63):
            for name in ('get_ticket', 'get_customer', 'get_ticket_messages'):
                with self.subTest(name=name, bad=bad), self.assertRaises(ValueError):
                    scope[name](bad)
            with self.assertRaises(ValueError): scope['get_ticket_messages'](123, bad)
            with self.assertRaises(ValueError): scope['list_recent_tickets'](bad)
            with self.assertRaises(ValueError): scope['list_inbox_tickets'](bad)
        get.assert_not_called()
    def test_inbox_page_is_bounded_and_omits_message_content(self):
        scope, get = functions()
        get.return_value = {'data': [{'id': 123, 'subject': 'Example', 'body_html': 'private body'}], 'meta': {'next_cursor': 'next'}}
        result = scope['list_inbox_tickets'](1000, cursor='https://untrusted.invalid/api')
        get.assert_called_once_with('/tickets', {'limit': 100, 'order_by': 'updated_datetime:desc', 'trashed': 'true', 'cursor': 'https://untrusted.invalid/api'})
        self.assertEqual(result['data'][0]['id'], 123)
        self.assertNotIn('body_html', result['data'][0])
        self.assertEqual(result['meta']['next_cursor'], 'next')

    def test_invalid_inbox_cursor_never_reaches_provider(self):
        scope, get = functions()
        for bad in ('', 'x'*2049, {}, True):
            with self.assertRaises(ValueError): scope['list_inbox_tickets'](cursor=bad)
        get.assert_not_called()

    def test_invalid_cursor_does_not_become_arbitrary_url(self):
        scope, get = functions()
        for bad in ('', 'x'*2049, {}, True):
            with self.assertRaises(ValueError): scope['get_ticket_messages'](123, cursor=bad)
        get.assert_not_called()
        scope['get_ticket_messages'](123, cursor='https://untrusted.invalid/api')
        self.assertEqual(get.call_args.args[0], '/messages')

if __name__ == '__main__': unittest.main()
