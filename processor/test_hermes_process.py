"""Real local subprocess tests; never invoke Hermes or any external service."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from hermes_runner.process import run_bounded, OutputLimitExceeded, _signal_group
from hermes_runner.runner import _run_environment


class ProcessTests(unittest.TestCase):
    def run_python(self, code, timeout=2):
        return run_bounded([sys.executable, '-c', code], timeout=timeout, env={})

    def test_group_permission_retry_requires_signal_success_or_group_absence(self):
        for outcome in (None, ProcessLookupError()):
            with patch('hermes_runner.process.os.killpg', side_effect=[PermissionError(), outcome]) as kill, \
                 patch('hermes_runner.process.time.sleep'):
                _signal_group(123456, signal.SIGKILL)
                self.assertEqual(kill.call_count, 2)
                kill.assert_called_with(123456, signal.SIGKILL)

    def test_persistent_group_permission_failure_is_not_swallowed(self):
        with patch('hermes_runner.process.os.killpg', side_effect=PermissionError()), \
             patch('hermes_runner.process.time.monotonic', side_effect=[0, .1, .31]), \
             patch('hermes_runner.process.time.sleep') as pause:
            with self.assertRaises(PermissionError):
                _signal_group(123456, signal.SIGKILL)
            pause.assert_called_once_with(.01)

    def test_cleanup_failure_retains_timeout_context_and_closes_descriptors(self):
        child = MagicMock(pid=123456)
        child.wait.side_effect = subprocess.TimeoutExpired('synthetic', .3)
        selector = MagicMock()
        selector.get_map.return_value = {'synthetic': True}
        with patch('hermes_runner.process.subprocess.Popen', return_value=child), \
             patch('hermes_runner.process.selectors.DefaultSelector', return_value=selector), \
             patch('hermes_runner.process.os.set_blocking'), \
             patch('hermes_runner.process.time.monotonic', side_effect=[0, 2]), \
             patch('hermes_runner.process._signal_group', side_effect=PermissionError()):
            with self.assertRaises(PermissionError) as failure:
                run_bounded(['synthetic'], timeout=1, env={})
        self.assertIsInstance(failure.exception.__context__, subprocess.TimeoutExpired)
        child.wait.assert_called_once_with(timeout=.3)
        selector.close.assert_called_once()
        child.stdout.close.assert_called_once()
        child.stderr.close.assert_called_once()

    def test_output_and_exit(self):
        result = self.run_python("import sys; print('draft'); print('err',file=sys.stderr); sys.exit(3)")
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, 'draft\n', 'err\n'))

    def test_output_caps(self):
        for stream, count in [('stdout', 1100000), ('stderr', 140000)]:
            with self.subTest(stream=stream), self.assertRaises(OutputLimitExceeded):
                self.run_python(f"import sys; sys.{stream}.write('x'*{count}); sys.{stream}.flush()")

    def test_timeout_is_bounded(self):
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            self.run_python('import time; time.sleep(20)', timeout=.1)
        self.assertLess(time.monotonic() - started, 2)

    def test_invalid_utf8_fails_closed(self):
        with self.assertRaises(UnicodeDecodeError):
            self.run_python("import os; os.write(1, b'\\xff')")

    def test_group_cleanup_on_timeout_success_and_interruption(self):
        for mode in ('timeout', 'success', 'interrupt', 'nonzero'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                marker = Path(tmp) / 'descendant-survived'
                code = f'''
import os, signal, subprocess, sys, time
subprocess.Popen([sys.executable, '-c', "import signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(.8); pathlib.Path({str(marker)!r}).write_text('bad')"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(.1)
'''
                if mode in ('timeout', 'interrupt'):
                    code += 'time.sleep(20)\n'
                if mode == 'nonzero':
                    code += 'sys.exit(4)\n'
                if mode == 'interrupt':
                    previous = signal.signal(signal.SIGALRM, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
                    signal.setitimer(signal.ITIMER_REAL, .25)
                    try:
                        with self.assertRaises(KeyboardInterrupt):
                            self.run_python(code)
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                        signal.signal(signal.SIGALRM, previous)
                elif mode == 'timeout':
                    with self.assertRaises(subprocess.TimeoutExpired):
                        self.run_python(code, timeout=.25)
                else:
                    self.assertEqual(self.run_python(code).returncode, 4 if mode == 'nonzero' else 0)
                time.sleep(.9)
                self.assertFalse(marker.exists(), 'grandchild survived process cleanup')

    def test_environment_excludes_credentials_and_startup_injection(self):
        canaries = {name: 'DO-NOT-INHERIT' for name in (
            'GORGIAS_API_KEY', 'SHOPIFY_CLIENT_SECRET', 'WEBHOOK_SECRET',
            'CONSOLE_SESSION_SECRET', 'WHATSAPP_API_KEY', 'PYTHONPATH',
            'PYTHONSTARTUP', 'LD_PRELOAD', 'DYLD_INSERT_LIBRARIES', 'BASH_ENV')}
        with patch.dict(os.environ, {**canaries, 'OPENAI_API_KEY': 'model-only'}, clear=True):
            env = _run_environment(SimpleNamespace(hermes_home='/safe', hermes_path='/usr/bin:/bin'))
        self.assertEqual(env, {'HOME': '/safe', 'PATH': '/usr/bin:/bin', 'OPENAI_API_KEY': 'model-only'})
        result = run_bounded([sys.executable, '-c', "import os; print(any(v=='DO-NOT-INHERIT' for v in os.environ.values()))"], timeout=2, env=env)
        self.assertEqual(result.stdout.strip(), 'False')

    def test_runner_failures_keep_fallback_without_logging_raw_provider_output(self):
        from hermes_runner import runner
        settings = SimpleNamespace(job_timeout=2,
            hermes_toolsets="buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias")
        cases = [subprocess.CompletedProcess([], 4, '', 'PROVIDER-SECRET'),
                 subprocess.TimeoutExpired('PROVIDER-SECRET', 2),
                 OutputLimitExceeded('PROVIDER-SECRET')]
        for outcome in cases:
            with self.subTest(outcome=type(outcome).__name__), patch.object(
                runner, 'get_settings', return_value=settings
            ), patch.object(runner, 'run_bounded') as execute, patch.object(
                runner, 'log_event'
            ) as log:
                if isinstance(outcome, Exception):
                    execute.side_effect = outcome
                else:
                    execute.return_value = outcome
                result = runner.process_ticket_with_hermes(
                    1, 'My order never arrived', 'Missing order', 'test@example.invalid', [])
                self.assertFalse(result['draft_text'])
                self.assertEqual(result['generation_state'], 'failed')
                self.assertFalse(result['note_posted'])
                self.assertNotIn('PROVIDER-SECRET', str(log.call_args_list))

    def test_misconfigured_tools_keep_sensitive_fallback_without_launch(self):
        from hermes_runner import runner
        for invalid in ('', 'shell', 'buttonsbebe_kb,buttonsbebe_redo',
                        'buttonsbebe_kb,buttonsbebe_redo,buttonsbebe_gorgias,shell'):
            with self.subTest(invalid=invalid), patch.object(
                runner, 'get_settings', return_value=SimpleNamespace(
                    job_timeout=2, hermes_toolsets=invalid)
            ), patch.object(runner, 'run_bounded') as execute:
                result = runner.process_ticket_with_hermes(
                    1, 'My order never arrived', 'Missing order', 'test@example.invalid', [])
                execute.assert_not_called()
                self.assertFalse(result['draft_text'])
                self.assertEqual(result['generation_state'], 'failed')
                self.assertEqual(result['priority'], 'normal')
                self.assertFalse(result['notify_owner'])
                self.assertFalse(result['gorgias_priority_set'])
                self.assertFalse(result['note_posted'])
