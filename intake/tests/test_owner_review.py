from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from intake import owner_review
from intake.policy import Invalid
from intake.records import canonical
from intake.store import Store


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class OwnerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'snapshot'
        self.source.mkdir(mode=0o700)
        self.data = json.loads((Path(__file__).resolve().parents[1] / 'fixtures/gorgias-synthetic.json').read_text())
        for target in ('intake.policy.ROOT', 'intake.recovery.ROOT'):
            patcher = patch(target, self.root)
            patcher.start()
            self.addCleanup(patcher.stop)

    def snapshot(self):
        files = []
        for i, ticket in enumerate(self.data['tickets']):
            payload = canonical({'tickets': [ticket]}).encode()
            name = 'sample.json' if i == 0 else f'sample-{i}.json'
            (self.source / name).write_bytes(payload)
            files.append({'file': name, 'sha256': sha256(payload).hexdigest()})
        messages = [m for t in self.data['tickets'] for m in t['messages']]
        manifest = {'format': 'gorgias-mcp-test-snapshot-v1', 'sample_capture_complete': True,
                    'counts': {'tickets': len(self.data['tickets']), 'messages': len(messages),
                               'notes': sum(not m['public'] for m in messages),
                               'attachments': sum(len(m.get('attachments') or []) for m in messages)},
                    'files': files}
        (self.source / 'manifest.json').write_text(canonical(manifest))

    def test_private_packet_requires_actual_owner_decision_and_new_workspace(self):
        self.snapshot()
        original = (self.source / 'sample.json').read_bytes()
        report = owner_review.prepare(self.source, 'review', 'synthetic')
        self.assertEqual(report['owner_acceptance'], 'pending')
        directory = self.root / '.local/review'
        packet = json.loads((directory / 'owner-review.json').read_text())
        self.assertTrue(packet['automated_passed'])
        self.assertFalse(packet['live_activation_authorized'])
        self.assertEqual((self.source / 'sample.json').read_bytes(), original)
        for name in ('owner-review.html', 'owner-review.md', 'owner-review.json', 'START-HERE.md'):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        before = (directory / 'owner-review.json').read_bytes()
        with self.assertRaises(Invalid):
            owner_review.prepare(self.source, 'review', 'synthetic')
        self.assertEqual((directory / 'owner-review.json').read_bytes(), before)

    def test_customer_markup_cannot_become_active_content_or_links(self):
        attack = '</pre><script>fetch("https://example.test/")</script><img src="https://example.test/pixel"><a href="javascript:alert(1)">X</a>\n```\n# Injected heading'
        self.data['tickets'][0]['subject'] = attack
        self.data['tickets'][0]['messages'][0]['body_text'] = attack
        self.snapshot()
        owner_review.prepare(self.source, 'escaping', 'synthetic')
        html = (self.root / '.local/escaping/owner-review.html').read_text()
        parsed = Elements()
        parsed.feed(html)
        for tag, attrs in parsed.elements:
            self.assertNotIn(tag, ('script', 'img', 'iframe', 'object', 'embed', 'form'))
            self.assertFalse(any(name.startswith('on') for name in attrs))
            if 'href' in attrs:
                self.assertTrue(attrs['href'].startswith('#case-'))
        self.assertIn('&lt;script&gt;', html)
        self.assertIn("script-src 'none'", html)
        self.assertIn('Owner acceptance has not been recorded', html)
        markdown = (self.root / '.local/escaping/owner-review.md').read_text()
        self.assertIn('````text\n' + attack + '\n````', markdown)

    def test_corrupt_source_is_refused_before_review_workspace_created(self):
        self.snapshot()
        (self.source / 'sample.json').write_text('{}')
        with self.assertRaises(Invalid):
            owner_review.prepare(self.source, 'corrupt', 'synthetic')
        self.assertFalse((self.root / '.local/corrupt').exists())

    def test_contact_or_chronology_failure_prevents_acceptance_packet(self):
        self.snapshot()
        original_get = Store.get_ticket
        def corrupted(store, tid):
            ticket = original_get(store, tid)
            ticket['name'] = 'Mismatched synthetic contact'
            ticket['messages'].reverse()
            return ticket
        with patch.object(Store, 'get_ticket', corrupted), self.assertRaises(Invalid):
            owner_review.prepare(self.source, 'bad-display', 'synthetic')
        directory = self.root / '.local/bad-display'
        self.assertFalse((directory / 'owner-review.html').exists())
        failures = json.loads((directory / 'owner-review-failures.json').read_text())
        self.assertEqual(failures['failures']['displayed_contact_name'], 2)
        self.assertEqual(failures['failures']['chronological_display'], 1)


if __name__ == '__main__':
    unittest.main()
