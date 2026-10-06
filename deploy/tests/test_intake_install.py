"""Prove source consumers load intake code from the installed shared tree."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[2]
INTERPRETER_VARIABLES = (
    ('gate', 'PYTHON'),
    ('webhook', 'WEBHOOK_PYTHON'),
    ('processor', 'PROCESSOR_PYTHON'),
    ('inbox', 'INBOX_PYTHON'),
    ('qa', 'QA_PYTHON'),
    ('tools', 'TOOLS_PYTHON'),
    ('hermes verifier', 'HERMES_VERIFY_PYTHON'),
)


def _interpreter(value: str) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        return str(candidate)
    if len(candidate.parts) > 1:
        return str((REPO / candidate).absolute())
    return shutil.which(value) or str((REPO / candidate).absolute())


class InstalledIntakeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'installed'
        self.outside = Path(self.temporary.name) / 'outside'
        self.shared = self.root / 'shared'
        self.webhook_src = self.root / 'webhook/src'
        self.tools = self.root / 'tools'
        self.processor = self.root / 'processor'
        self.inbox = self.root / 'console-src/inbox'
        self.outside.mkdir(parents=True)

        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        shutil.copytree(REPO / 'intake', self.shared / 'intake', ignore=ignore)
        shutil.copytree(REPO / 'webhook/src/bb_webhook',
                        self.webhook_src / 'bb_webhook', ignore=ignore)
        self.tools.mkdir(parents=True)
        shutil.copy2(REPO / 'tools/gorgias_content.py', self.tools / 'gorgias_content.py')
        self.processor.mkdir(parents=True)
        for name in ('gorgias_reconcile.py', 'logging_setup.py'):
            shutil.copy2(REPO / 'processor' / name, self.processor / name)
        self.inbox.mkdir(parents=True)
        for name in ('export_projection.py', 'projection.py', 'shop_rail.py'):
            shutil.copy2(REPO / 'console-src/inbox' / name, self.inbox / name)

        self.import_paths = (
            self.shared,
            self.webhook_src,
            self.tools,
            self.processor,
            self.inbox,
        )
        self.expected_intake_module = self.shared / 'intake/message_content.py'
        self.environment = {
            'HOME': str(self.outside),
            'PATH': os.environ.get('PATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1',
            'PYTHONNOUSERSITE': '1',
            'PYTHONPATH': os.pathsep.join(str(path) for path in self.import_paths),
        }

    def _configured_interpreters(self):
        candidates = [('test runner', sys.executable)]
        candidates.extend(
            (label, _interpreter(value))
            for label, variable in INTERPRETER_VARIABLES
            if (value := os.environ.get(variable))
        )
        seen = set()
        for label, executable in candidates:
            if executable not in seen:
                seen.add(executable)
                yield label, executable

    def _role_interpreter(self, variable, *fallbacks):
        for name in (variable, *fallbacks):
            value = os.environ.get(name)
            if value:
                return _interpreter(value)
        return sys.executable

    def _run(self, executable, code):
        completed = subprocess.run(
            [executable, '-c', textwrap.dedent(code), str(self.expected_intake_module)],
            cwd=self.outside,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            completed.returncode, 0,
            f'{executable} failed from the installed layout:\n'
            f'{completed.stdout}{completed.stderr}',
        )

    def test_prepared_interpreters_load_cleanup_from_shared_intake(self):
        code = '''\
            import intake.message_content as canonical
            from pathlib import Path
            import sys

            contract = {
                'display_text': 'Saved display history',
                'current_text': 'Saved customer question',
                'display_source': 'body_text',
                'current_source': 'stripped_text',
                'original_content': 'Saved original body',
                'original_field': 'body_text',
                'history_available': True,
                'source_truncated': False,
                'cleanup_version': 'intake-1',
            }
            message = canonical.intake_from_message({
                'stripped_text': 'A different source that must not replace the saved contract',
                'preferred_content': 'NOT A RAW BODY',
                **contract,
            })
            assert Path(canonical.__file__).resolve() == Path(sys.argv[1]).resolve()
            assert message == contract
            assert message['cleanup_version'] == 'intake-1'
        '''
        for label, executable in self._configured_interpreters():
            with self.subTest(interpreter=label, executable=executable):
                self._run(executable, code)

    def test_installed_consumers_use_shared_intake(self):
        cases = (
            (
                'webhook', 'WEBHOOK_PYTHON', 'PYTHON',
                '''\
                    import intake.message_content as canonical
                    from bb_webhook.message_content import normalize_message
                    from pathlib import Path
                    import sys

                    assert Path(canonical.__file__).resolve() == Path(sys.argv[1]).resolve()
                    message = normalize_message({'stripped_text': 'Where is my order?'})
                    assert message['current_text'] == 'Where is my order?'
                    assert message['cleanup_version'] == 'intake-1'
                ''',
            ),
            (
                'tools', 'TOOLS_PYTHON', 'PYTHON',
                '''\
                    import intake.message_content as canonical
                    from gorgias_content import curate_message
                    from pathlib import Path
                    import sys

                    assert Path(canonical.__file__).resolve() == Path(sys.argv[1]).resolve()
                    curated = curate_message({'stripped_text': 'Where is my order?'})
                    assert curated['current_text'] == 'Where is my order?'
                    assert curated['cleanup_version'] == 'intake-1'
                    curated['stripped_text'] = 'A later raw source must not replace the saved contract'
                    saved = canonical.intake_from_message(curated)
                    assert saved['current_text'] == 'Where is my order?'
                    assert saved['original_content'] == 'Where is my order?'
                ''',
            ),
            (
                'processor', 'PROCESSOR_PYTHON', 'PYTHON',
                '''\
                    import intake.message_content as canonical
                    import gorgias_reconcile
                    import json
                    from pathlib import Path
                    import sys

                    assert Path(canonical.__file__).resolve() == Path(sys.argv[1]).resolve()
                    contract = {
                        'display_text': 'Saved display history',
                        'current_text': 'Saved customer question',
                        'display_source': 'body_text',
                        'current_source': 'stripped_text',
                        'original_content': 'Saved original body',
                        'original_field': 'body_text',
                        'history_available': True,
                        'source_truncated': False,
                        'cleanup_version': 'intake-1',
                    }
                    normalized = gorgias_reconcile.intake_from_message({
                        'stripped_text': 'A different source that must not replace the saved contract',
                        'preferred_content': 'NOT A RAW BODY',
                        **contract,
                    })
                    bounded = json.loads(gorgias_reconcile._bound_raw({
                        'ticket': {'subject': 'Synthetic order question', 'customer': {}},
                        'message': {
                            'id': 7, 'from_agent': False,
                            'created_datetime': '2026-10-05T12:00:00Z',
                            'stripped_text': 'A different source that must not replace the saved contract',
                            'preferred_content': 'NOT A RAW BODY',
                            **normalized,
                        },
                    }))['message']
                    assert bounded['current_text'] == 'Saved customer question'
                    assert bounded['display_text'] == 'Saved display history'
                    assert bounded['original_content'] == 'Saved original body'
                    assert bounded['cleanup_version'] == 'intake-1'
                    assert bounded['preferred_content'] == 'NOT A RAW BODY'
                ''',
            ),
            (
                'Inbox projection', 'INBOX_PYTHON', 'PYTHON',
                '''\
                    import intake.message_content as canonical
                    import export_projection
                    from pathlib import Path
                    import sys

                    assert Path(canonical.__file__).resolve() == Path(sys.argv[1]).resolve()
                    contract = {
                        'display_text': 'Saved display history',
                        'current_text': 'Saved customer question',
                        'display_source': 'body_text',
                        'current_source': 'stripped_text',
                        'original_content': 'Saved original body',
                        'original_field': 'body_text',
                        'history_available': True,
                        'source_truncated': False,
                        'cleanup_version': 'intake-1',
                    }
                    normalized = canonical.intake_from_message({
                        'stripped_text': 'A different source that must not replace the saved contract',
                        'preferred_content': 'NOT A RAW BODY',
                        **contract,
                    })
                    message, truncated = export_projection.intake_for_record(
                        {'intake_normalized': normalized})
                    assert message == contract
                    assert truncated is False
                ''',
            ),
        )
        for label, variable, fallback, code in cases:
            with self.subTest(consumer=label):
                self._run(self._role_interpreter(variable, fallback), code)


if __name__ == '__main__':
    unittest.main()
