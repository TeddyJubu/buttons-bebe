"""Exercise Caddy Inbox authentication and legacy-route retirement on loopback."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

SOURCE = Path(__file__).resolve().parents[1] / 'caddy/sites/support.caddy'


def inbox_block(source):
    start = source.index('\thandle /inbox/api/* {')
    depth = 0
    for index in range(start, len(source)):
        if source[index] == '{': depth += 1
        elif source[index] == '}':
            depth -= 1
            if depth == 0: return source[start:index+1]
    raise ValueError('Inbox Caddy block incomplete')


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs): return None


@unittest.skipUnless(shutil.which('caddy'), 'Caddy binary required; run this suite on Linux staging')
class InboxCaddyContractTests(unittest.TestCase):
    def test_auth_sees_original_uri_method_and_origin_before_proxy(self):
        auth_requests = []
        class Auth(BaseHTTPRequestHandler):
            def do_GET(self):
                record = {name: self.headers.get(name) for name in
                          ('X-Forwarded-Uri', 'X-Forwarded-Method', 'Origin', 'Cookie', 'Authorization')}
                auth_requests.append(record)
                if self.headers.get('Cookie') != 'session=synthetic':
                    self.send_response(302)
                    self.send_header('Location', '/console/login?next=' + urllib.parse.quote(record['X-Forwarded-Uri'], safe=''))
                    self.end_headers()
                else:
                    self.send_response(200); self.end_headers()
            def log_message(self, *_args): pass
        class Upstream(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'path': self.path, 'method': self.command, 'cookie': self.headers.get('Cookie'), 'authorization': self.headers.get('Authorization')}).encode())
            do_POST = do_GET
            def log_message(self, *_args): pass
        auth = ThreadingHTTPServer(('127.0.0.1', 0), Auth)
        upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        for server in (auth, upstream):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        block = inbox_block(SOURCE.read_text()).replace('127.0.0.1:8000', f'127.0.0.1:{auth.server_port}').replace('127.0.0.1:8767', f'127.0.0.1:{upstream.server_port}')
        listen = port()
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'Caddyfile'
            config.write_text('{\n admin off\n auto_https off\n}\nhttp://127.0.0.1:' + str(listen) + ' {\n' + block + '\nhandle {\n respond "old console" 418\n}\n}\n')
            adapted = subprocess.run(['caddy', 'adapt', '--config', str(config), '--adapter', 'caddyfile'], capture_output=True, check=True, text=True)
            parsed = json.loads(adapted.stdout)
            # Adapted JSON is traversed in execution order, not source order.
            events = []
            def visit(node):
                if isinstance(node, dict):
                    if node.get('handler') == 'reverse_proxy':
                        events.append('auth' if node.get('rewrite', {}).get('uri') == '/auth/page-check' else 'proxy')
                    if node.get('handler') == 'rewrite' and node.get('strip_path_prefix') == '/inbox': events.append('strip')
                    for value in node.values(): visit(value)
                elif isinstance(node, list):
                    for value in node: visit(value)
            visit(parsed)
            self.assertLess(events.index('auth'), events.index('proxy'))
            process = subprocess.Popen(['caddy', 'run', '--config', str(config), '--adapter', 'caddyfile'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                opener = urllib.request.build_opener(NoRedirect())
                url = f'http://127.0.0.1:{listen}'
                for attempt in range(60):
                    try: opener.open(url + '/inbox/api/helpdesk', timeout=1)
                    except urllib.error.HTTPError as error:
                        self.assertEqual(error.code, 302)
                        self.assertEqual(error.headers['Location'], '/console/login?next=%2Finbox%2Fapi%2Fhelpdesk')
                        break
                    except urllib.error.URLError:
                        if attempt == 59: raise
                        time.sleep(0.05)
                request = urllib.request.Request(url + '/inbox/api/helpdesk', data=b'{}', headers={
                    'Cookie': 'session=synthetic', 'Authorization': 'Bearer synthetic', 'Origin': 'https://support.buttonsbebe.com', 'Content-Type': 'application/json'})
                with opener.open(request, timeout=3) as response:
                    self.assertEqual(json.load(response), {'path': '/inbox/api/helpdesk', 'method': 'POST', 'cookie': None, 'authorization': None})
                self.assertEqual(auth_requests[-1], {'X-Forwarded-Uri': '/inbox/api/helpdesk',
                    'X-Forwarded-Method': 'POST', 'Origin': 'https://support.buttonsbebe.com', 'Cookie': 'session=synthetic', 'Authorization': 'Bearer synthetic'})
                with self.assertRaises(urllib.error.HTTPError) as caught: opener.open(url + '/console/api/helpdesk', timeout=3)
                self.assertEqual(caught.exception.code, 418)
            finally:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)

    def test_retired_inbox_redirects_pages_but_never_forwards_old_api_or_assets(self):
        source = SOURCE.read_text()
        block = source[source.index('\t# Keep old Inbox 2 bookmarks'):source.index('\t@consoleauth')]
        listen = port()
        with tempfile.TemporaryDirectory() as temp:
            config = Path(temp) / 'Caddyfile'
            config.write_text('{\n admin off\n auto_https off\n}\nhttp://127.0.0.1:' + str(listen) + ' {\n' + block + '\n}\n')
            process = subprocess.Popen(['caddy', 'run', '--config', str(config)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                opener = urllib.request.build_opener(NoRedirect())
                base = f'http://127.0.0.1:{listen}'
                for attempt in range(60):
                    try: opener.open(base + '/inbox/', timeout=1)
                    except urllib.error.HTTPError: break
                    except urllib.error.URLError:
                        if attempt == 59: raise
                        time.sleep(.05)
                query = '?ticket=gorgias%3A123&view=open&q=two%20words'
                for path in ('/inbox2', '/inbox2/', '/inbox2/index.html'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        opener.open(base + path + query, timeout=3)
                    self.assertEqual(caught.exception.code, 308)
                    self.assertEqual(caught.exception.headers['Location'], '/inbox/' + query)
                for path in ('/inbox2/app.js', '/inbox2/styles.css', '/inbox2/api/helpdesk'):
                    with self.assertRaises(urllib.error.HTTPError) as caught:
                        opener.open(base + path, timeout=3)
                    self.assertEqual(caught.exception.code, 410)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    opener.open(urllib.request.Request(base + '/inbox2/', data=b'{}'), timeout=3)
                self.assertEqual(caught.exception.code, 410)
            finally:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__ == '__main__': unittest.main()
