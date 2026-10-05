import hmac
import http.client
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from intake import auth
from intake.policy import CAPABILITIES, Invalid, workspace_directory
from intake.server import LocalServer
from intake.store import Store

REPO = Path(__file__).resolve().parents[2]


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.server = LocalServer(0, self.store, "test")
        auth.provision(self.store, 'admin', 'Test Administrator', 'admin', 'Only a synthetic test password')
        raw = auth.login(self.store, 'admin', 'Only a synthetic test password', self.server.audience)
        self.cookie = self.server.cookie_name + '=' + raw
        self.token = hmac.new(self.server.csrf_secret, raw.encode(), 'sha256').hexdigest()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, path, data=None, headers=None, token=True):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            combined = {"Content-Type": "application/json", "Cookie": self.cookie}
            if token:
                combined["X-Intake-Token"] = self.token
            combined.update(headers or {})
            client.request("POST" if data is not None else "GET", path,
                           json.dumps(data) if data is not None else None, combined)
            response = client.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            client.close()

    def test_no_live_capability_even_with_enabling_environment_flags(self):
        self.assertFalse(any(CAPABILITIES[name] for name in ("liveIntake", "sendReply", "providerReads", "aiGeneration", "notifications")))
        for route in ("/api/send", "/api/webhook/gorgias", "/api/pull_mailbox", "/api/sync", "/api/notify",
                      "/api/rewrite", "/console/api/inbox/send-access", "/dashboard/api/ticket/1/send"):
            status, _, body = self.request(route, {"enabled": True})
            self.assertEqual(status, 403, route)
            self.assertIn(b"disabled", body)
        self.assertEqual(self.store.list_tickets()["total"], 0)

    def test_host_origin_and_token_protection(self):
        for headers in ({"Host": "evil.example"}, {"Origin": "https://evil.example"}, {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request("/api/session", headers=headers)[0], 403)
            self.assertEqual(self.request("/api/tickets", {"subject": "A"}, headers=headers)[0], 403)
        self.assertEqual(self.request("/api/tickets", token=False)[0], 403)
        self.assertEqual(self.request("/api/tickets", {}, token=False)[0], 403)
        self.assertEqual(self.request("/api/tickets", headers={"X-Intake-Token": "wrong"})[0], 403)

    def test_replay_has_no_http_ingress(self):
        for path in ('/api/replay', '/api/replay/preview', '/api/replay/commit', '/api/inbound'):
            self.assertEqual(self.request(path, {'mode': 'offline_replay'})[0], 404)
        self.assertEqual(self.store.list_tickets(queue='all')['total'], 0)

    def test_private_attachment_and_backup_operations_have_no_http_routes(self):
        for path in ('/api/attachment-import', '/api/backup', '/api/restore'):
            self.assertEqual(self.request(path, {'mode': 'offline_sandbox'})[0], 404)
        for path in ('/api/attachments/anything', '/backups/checkpoint/intake.sqlite3', '/copies/file.html'):
            self.assertEqual(self.request(path)[0], 404)

    def test_fake_delivery_api_requires_review_and_confirmation(self):
        from intake import import_store
        payload = (REPO / 'intake/fixtures/gorgias-synthetic.json').read_bytes()
        preview = import_store.preview(self.store, payload, 'test')
        import_store.apply_import(self.store, payload, 'test', preview['digest'])
        with self.store.connection() as db:
            tid = db.execute("SELECT internal_id FROM source_records WHERE kind='ticket' AND external_id='900001'").fetchone()[0]
        path = f'/api/tickets/{tid}'
        body = {'mode': 'offline_simulation', 'operation_id': 'api-review', 'revision': 1,
                'body': 'Synthetic API reply', 'scenario': 'accepted_timeout'}
        self.assertEqual(self.request(path + '/simulation-review', body, token=False)[0], 403)
        status, _, raw = self.request(path + '/simulation-review', body)
        self.assertEqual(status, 200)
        review = json.loads(raw)
        confirmation = {'mode': 'offline_simulation', 'review_id': review['review_id'], 'digest': review['digest']}
        self.assertEqual(self.request(path + '/simulation-confirm', confirmation)[0], 400)
        confirmation['confirmed'] = True
        status, _, raw = self.request(path + '/simulation-confirm', confirmation)
        self.assertEqual(status, 200)
        attempt = json.loads(raw)
        self.assertEqual(attempt['state'], 'uncertain')
        _, _, repeated = self.request(path + '/simulation-confirm', confirmation)
        self.assertEqual(json.loads(repeated)['attempt_id'], attempt['attempt_id'])
        _, _, raw = self.request(path + '/simulation-reconcile', {'mode': 'offline_simulation',
            'attempt_id': attempt['attempt_id'], 'confirmed': True})
        self.assertEqual(json.loads(raw)['state'], 'simulated_delivered')
        self.assertEqual(self.request('/api/send', confirmation)[0], 403)

    def test_static_content_cannot_load_remote_resources(self):
        status, headers, _ = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("img-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        for path in ("/.env", "/../AGENTS.md", "/.local/test/intake.sqlite3", "/fixtures/gorgias-synthetic.json"):
            self.assertEqual(self.request(path)[0], 404)

    def test_isolated_inbox_serves_only_fixed_local_assets(self):
        for path in ('/inbox/', '/inbox/app.js', '/inbox/client.js', '/inbox/styles.css',
                     '/inbox/offline.css', '/inbox/icons.js'):
            status, headers, body = self.request(path, token=False)
            self.assertEqual(status, 200)
            self.assertIn("img-src 'none'", headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertNotIn(b'fonts.googleapis.com', body)
        for path in ('/inbox/live_api.py', '/inbox/provenance.json', '/inbox/.local/test/intake.sqlite3',
                     '/inbox/../../.env', '/inbox/api/helpdesk', '/console/api/auth/logout'):
            self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(self.request('/inbox/', headers={'Origin': 'https://wrong.test'})[0], 403)
        self.assertEqual(self.request('/inbox/api/helpdesk', {'tool': 'helpdesk.get_ticket'})[0], 404)

    def test_inbox_copies_preserve_the_existing_production_assets(self):
        from hashlib import sha256
        from html.parser import HTMLParser
        provenance = json.loads((REPO / 'intake/inbox/provenance.json').read_text())
        for name, expected in provenance['source_sha256'].items():
            self.assertEqual(sha256((REPO / 'console-src/inbox2' / name).read_bytes()).hexdigest(), expected)
        for name in ('styles.css', 'icons.js', 'lucide-LICENSE.txt'):
            self.assertEqual((REPO / 'intake/inbox' / name).read_bytes(), (REPO / 'console-src/inbox2' / name).read_bytes())
        refs = []
        class Markup(HTMLParser):
            def handle_starttag(self, tag, attrs):
                for key, value in attrs:
                    if key in ('href', 'src'):
                        refs.append(value)
        Markup().feed((REPO / 'intake/inbox/index.html').read_text())
        self.assertTrue(all(ref == '/' or ref.startswith(('/inbox/', '#')) for ref in refs))

    def test_api_create_note_and_conflict_status(self):
        payload = {"operation_id": "create", "subject": "Test", "body": "An internal record"}
        status, _, body = self.request("/api/tickets", payload)
        self.assertEqual(status, 200)
        tid = json.loads(body)["id"]
        self.assertEqual(self.request(f"/api/tickets/{tid}/notes", {"operation_id": "note", "revision": 1, "body": "A note"})[0], 200)
        self.assertEqual(self.request(f"/api/tickets/{tid}/update", {"operation_id": "edit", "revision": 1, "status": "closed", "priority": "normal"})[0], 409)

    def test_invalid_payloads_return_errors_without_writes(self):
        for payload in ([], {"subject": "Missing body"}, {"subject": {}, "body": "x", "operation_id": "a"}):
            self.assertEqual(self.request("/api/tickets", payload)[0], 400)
        self.assertEqual(self.request("/api/import/commit", {"account": "test", "export": []})[0], 400)
        self.assertEqual(self.store.list_tickets()["total"], 0)

    def test_http_import_retains_raw_json_validation_and_atomic_preview(self):
        payload = (REPO / "intake/fixtures/gorgias-synthetic.json").read_text()
        status, _, body = self.request("/api/import/preview", {"account": "test", "export_text": payload})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.list_tickets()["total"], 0)
        fingerprint = json.loads(body)["digest"]
        status, _, _ = self.request("/api/import/commit", {"account": "test", "export_text": payload, "expected_digest": fingerprint})
        self.assertEqual(status, 200)
        self.assertEqual(self.store.list_tickets()["total"], 2)
        status, _, _ = self.request("/api/import/preview", {"account": "test", "export_text": '{"tickets":[],"tickets":[]}'})
        self.assertEqual(status, 400)

    def test_intake_is_outside_production_release_inventory(self):
        tree = ast.parse((REPO / "deploy/cd/source_release.py").read_text())
        components = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                          and any(isinstance(t, ast.Name) and t.id == "COMPONENTS" for t in n.targets))
        self.assertFalse(any(p == "intake" or p.startswith("intake/") for p in components))

    def test_outbound_guard_blocks_connections_dns_and_processes(self):
        program = r'''
import socket, subprocess
from intake.policy import install_offline_guard, CAPABILITIES
from intake.server import LocalServer
from intake.store import Store
from intake import replay, delivery, attachments, recovery, channel_adapter, intake_jobs
from intake.private_files import checksum
from intake.records import canonical
from unittest.mock import patch
import json
from pathlib import Path
import tempfile
install_offline_guard()
blocked = 0
def connect():
    with socket.socket() as s: s.connect(("127.0.0.1", 9))
def datagram():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s: s.sendto(b"x", ("127.0.0.1", 9))
for action in (connect, datagram, lambda: socket.getaddrinfo("example.test", 443), lambda: subprocess.run(["true"])):
    try: action()
    except PermissionError: blocked += 1
    else: raise AssertionError("external operation was allowed")
assert blocked == 4
assert not CAPABILITIES["sendReply"]
with tempfile.TemporaryDirectory() as tmp:
    store = Store(tmp)
    data = Path('intake/fixtures/replay-synthetic.json').read_bytes()
    preview = replay.run(store, data, preview=True)
    result = replay.run(store, data, preview['digest'])
    assert result['events'] == 7 and result['outboundActions'] == 0
    batch = json.loads(data)
    channel = channel_adapter.create_channel(store,batch['account'],batch['mailbox'],batch['events'][0]['provider'])
    key = channel_adapter.rotate_key(store,channel)
    signed = channel_adapter.make_fixture(store,key,data)
    planned = channel_adapter.enqueue(store,signed,preview=True)
    channel_adapter.enqueue(store,signed,planned['digest'])
    assert sum(intake_jobs.run(store)['outcomes'].values()) == 7
    assert intake_jobs.health(store)['jobs'] == {'done':7}
    tid = result['results'][0]['ticket_id']
    reviewed = delivery.review(store, tid, {'mode': 'offline_simulation', 'operation_id': 'offline-reply',
        'revision': store.get_ticket(tid)['revision'], 'body': 'Only a local simulation', 'scenario': 'accepted_timeout'})
    attempt = delivery.confirm(store, tid, {'mode': 'offline_simulation', 'review_id': reviewed['review_id'],
        'digest': reviewed['digest'], 'confirmed': True})
    assert attempt['state'] == 'uncertain'
    resolved = delivery.reconcile(store, tid, {'mode': 'offline_simulation', 'attempt_id': attempt['attempt_id'], 'confirmed': True})
    assert resolved['state'] == 'simulated_delivered' and resolved['outboundActions'] == 0
    from intake import assistance
    from intake.assistance_demo import make_fixture
    saved_input = assistance.read_input(store, tid)['input']
    fixture = make_fixture(saved_input, 'needs_staff', evidence=False)
    blob = canonical(fixture).encode()
    planned = assistance.import_fixture(store, blob, preview=True)
    assistance.import_fixture(store, blob, planned['digest'])
    assistance.run(store, tid, {'mode':'offline_fixture','operation_id':'guard-assistance',
        'revision':saved_input['ticket']['revision'],'fixture_digest':planned['digest']})
    assert store.get_ticket(tid)['assistance']['draft']['state'] == 'needs_staff'

    raw = json.loads(data)
    candidate = raw['events'][0]
    candidate['event_id'] = 'guard-attachment'
    candidate['message']['id'] = 'guard-attachment'
    candidate['message']['headers'] = {'Message-ID': '<guard-attachment@example.test>'}
    candidate['message']['attachments'] = [{'name': 'guard.bin', 'size': 3}]
    raw['events'] = [candidate]
    attachment_event = canonical(raw).encode()
    replay.run(store, attachment_event, replay.run(store, attachment_event, preview=True)['digest'])
    with store.connection() as db:
        aid = db.execute('SELECT id FROM attachments').fetchone()[0]
    bundle = Path(tmp) / 'bundle'
    bundle.mkdir(mode=0o700)
    (bundle/'file.bin').write_bytes(b'abc')
    (bundle/'manifest.json').write_text(canonical({'mode':'offline_attachment_import','files':[
        {'attachment_id':aid,'file':'file.bin','sha256':checksum(b'abc')}]}))
    attachments.apply_import(store,bundle,attachments.preview(store,bundle)['digest'])
    backup = recovery.backup(store,'guard')
    path = Path(tmp)/'backups'/'guard'
    assert recovery.verify(path)['attachment_blobs'] == 1
    with patch.object(recovery,'ROOT',Path(tmp)):
        restored = recovery.restore(path,'guard-restored',backup['digest'])
        assert restored['attachment_blobs'] == 1 and restored['outboundActions'] == 0
    server = LocalServer(0, store, "test")
    assert server.server_address[0] == "127.0.0.1"
    server.server_close()
print("offline boundary passed")
'''
        env = {**os.environ, "HELPDESK_OUTBOUND_ENABLED": "1", "GORGIAS_BRIDGE_ENABLED": "1", "INTAKE_LIVE": "1"}
        result = subprocess.run([sys.executable, "-c", program], cwd=REPO, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "offline boundary passed")

    def test_workspace_rejects_external_paths(self):
        for name in ("../production", "/var/lib/app", ".", "", "../../", "a/b"):
            with self.assertRaises(Invalid):
                workspace_directory(name)


if __name__ == "__main__":
    unittest.main()
