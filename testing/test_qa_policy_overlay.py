import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from qa_policy_overlay import load_overlay, replace_hits


class PolicyOverlayTests(unittest.TestCase):
    def test_only_hash_pinned_allowlisted_policies_can_replace_returned_hits(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'kb/policies').mkdir(parents=True)
            (root/'kb/policies/shipping.md').write_text('confirmed policy')
            name = 'policies/shipping.md'
            row = dict(file=name, category='policies', status='confirmed', title='Shipping',
                       text='## Timing\n24–48 hours\n## Pickup\nVerify readiness.', sensitive=False)
            path = root/'snapshot.json'
            def read(data):
                path.write_text(json.dumps(data))
                return load_overlay(path, hashlib.sha256(path.read_bytes()).hexdigest(), root)
            overlay = read({name: row})
            with self.assertRaises(ValueError):
                load_overlay(path, '0'*64, root)
            for bad in ('tickets/private.md', 'products/unreviewed.md', 'policies/../private.md'):
                with self.assertRaises(ValueError):
                    read({bad: {**row, 'file': bad}})
            for badrow in ({**row, 'status':'DRAFT'}, {**row, 'file':'policies/other.md'}):
                with self.assertRaises(ValueError):
                    read({name: badrow})
            product = dict(file='products/confirmed.md',text='Existing product fact')
            hits = replace_hits([{**row,'heading':'Timing','text':'Old timing'}, product], overlay)
            self.assertEqual(hits[0]['text'], '24–48 hours')
            self.assertEqual(hits[1], product)
            self.assertEqual(len(hits), 2)
            self.assertIn('Verify readiness.', replace_hits([{**row,'heading':'Old heading'}], overlay)[0]['text'])
