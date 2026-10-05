"""Synthetic observer checks: injected /proc readers run on every host."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import qa_execution as execution


HELPER = Path(__file__).resolve().parents[1] / "processor/hermes_runner/process.py"


class LocalSubprocess:
    """Test-only local module binding; never patch the global stdlib object."""
    def __init__(self, original, popen):
        self.original, self.Popen = original, popen

    def __getattr__(self, name):
        return getattr(self.original, name)


class Reader:
    def __init__(self, rows, *, alive=True):
        self.rows = iter(rows)
        self.last = None
        self.alive = alive
        self.fds = []

    def open_image(self, pid):
        row = next(self.rows, self.last)
        self.last = row
        if isinstance(row, BaseException):
            raise row
        backing, semantic = row
        fd = os.open(backing, os.O_RDONLY)
        self.fds.append(fd)
        return execution.ImageSnapshot.from_fd(fd, semantic, process_start=123)

    def confirm_image(self, pid, image):
        return self.alive


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / "qa-home"
        self.home.mkdir()
        self.binary = self.root / "python3"
        self.binary.write_bytes(b"fictional Python executable\n")
        self.sha = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        self.helper = execution.load_helper(HELPER)
        self.original = self.helper.subprocess

    def run_case(self, reader, *, duration=0.04, after=None, error=None, command=None, **options):
        options.setdefault("required_effective_role", "selected_python")
        def synthetic(command, **kwargs):
            child = self.helper.subprocess.Popen(command, env=kwargs["env"], cwd=kwargs["cwd"])
            time.sleep(duration)
            if after:
                after()
            if error:
                raise error
            return subprocess.CompletedProcess(command, 0, "synthetic output", "")
        self.helper.run_bounded = synthetic
        child = type("Child", (), {"pid": 4321})()
        launch = Mock(return_value=child)
        local = LocalSubprocess(self.original, launch)
        self.helper.subprocess = local
        try:
            result = execution.run_observed(self.helper, command or [str(self.binary), "-I", "-c", "pass"],
                timeout=1, env={"fictional": "kept unchanged"}, cwd=self.home,
                expected_sha256=self.sha, private_home=self.home, reader=reader,
                poll_interval=0.002, **options)
            self.assertIs(self.helper.subprocess, local)
        finally:
            self.helper.subprocess = self.original
        self.assertIs(self.helper.subprocess, self.original)
        launch.assert_called_once()
        self.assertEqual(launch.call_args.args, (command or [str(self.binary), "-I", "-c", "pass"],))
        self.assertEqual(launch.call_args.kwargs,
            {"env": {"fictional": "kept unchanged"}, "cwd": self.home})
        return result

    def assert_closed(self, reader):
        for fd in reader.fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_requested_command_env_and_actual_pid_bound_without_output_content(self):
        reader = Reader([(self.binary, str(self.binary))])
        command = [str(self.binary), "-I", "-c", "pass", "fictional-argument"]
        result, evidence = self.run_case(reader, command=command)
        self.assertEqual(result.stdout, "synthetic output")
        self.assertEqual(evidence["status"], "verified_sampled")
        self.assertEqual(evidence["pid"], 4321)
        self.assertEqual(evidence["requested_launch_sha256"], execution.command_digest(command))
        self.assertEqual(evidence["images"][0]["sha256_before"], self.sha)
        self.assertEqual(evidence["images"][0]["sha256_after"], self.sha)
        self.assertEqual(evidence["helper_sha256_before"], evidence["helper_sha256_after"])
        self.assertNotIn("synthetic output", json.dumps(evidence))
        self.assertFalse(evidence["all_exec_transitions_observed"])
        self.assert_closed(reader)

    def test_private_managed_interpreter_transition_same_pid_normalizes_home(self):
        private = self.home / ".hermes/tools/python-3.14.7+20260901-linux-x64/bin/python3.14"
        reader = Reader([(self.binary, str(self.binary)), (self.binary, str(private))])
        _, evidence = self.run_case(reader, required_effective_role="private_managed_python")
        self.assertEqual(len(evidence["images"]), 2)
        self.assertEqual(evidence["images"][1]["role"], "private_managed_python")
        self.assertTrue(evidence["images"][1]["normalized_path"].startswith("${QA_HOME}/"))
        self.assertEqual(evidence["effective_observed_image"], 1)
        self.assert_closed(reader)

    def test_unknown_path_and_bad_private_role_fail_closed(self):
        for semantic in ("/unapproved/python3", str(self.home / ".hermes/tools/arbitrary/bin/python3"),
                         str(self.home / ".hermes/tools/python-3.14.7/bin/bash"),
                         str(self.binary) + " (deleted)"):
            with self.subTest(path=semantic):
                reader = Reader([(self.binary, semantic)])
                with self.assertRaises(execution.ExecutionObservationError) as raised:
                    self.run_case(reader)
                self.assertEqual(raised.exception.execution_evidence["status"], "incomplete")
                self.assert_closed(reader)

    def test_unknown_binary_hash_rejected(self):
        other = self.root / "other"
        other.write_bytes(b"unapproved")
        reader = Reader([(other, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader)
        self.assert_closed(reader)

    def test_missing_proc_and_confirmation_race_rejected(self):
        for reader in (Reader([OSError("no proc")]), Reader([(self.binary, str(self.binary))], alive=False)):
            with self.assertRaises(execution.ExecutionObservationError):
                self.run_case(reader)
            self.assert_closed(reader)

    def test_same_path_changed_inode_rejected_even_with_identical_bytes(self):
        other = self.root / "copy"
        other.write_bytes(self.binary.read_bytes())
        reader = Reader([(self.binary, str(self.binary)), (other, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader)
        self.assert_closed(reader)

    def test_same_fd_rehash_survives_unlink_and_exit(self):
        reader = Reader([(self.binary, str(self.binary))])
        _, evidence = self.run_case(reader, after=self.binary.unlink)
        self.assertEqual(evidence["images"][0]["sha256_after"], self.sha)
        self.assert_closed(reader)

    def test_content_mutation_before_after_rejected(self):
        reader = Reader([(self.binary, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader, after=lambda: self.binary.write_bytes(b"modified image"))
        self.assert_closed(reader)

    def test_helper_exception_preserved_and_resources_restored(self):
        reader = Reader([(self.binary, str(self.binary))])
        error = subprocess.TimeoutExpired("synthetic", 1)
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            self.run_case(reader, error=error)
        self.assertIs(raised.exception, error)
        self.assertEqual(error.execution_evidence["images"][0]["sha256_after"], self.sha)
        self.assertIs(self.helper.subprocess, self.original)
        self.assert_closed(reader)

    def test_observer_start_failure_still_preserves_helper_cleanup_and_exception(self):
        reader = Reader([(self.binary, str(self.binary))])
        error = subprocess.TimeoutExpired("synthetic", 1)
        with patch.object(execution.threading.Thread, "start", side_effect=RuntimeError("synthetic start failure")):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                self.run_case(reader, error=error)
        self.assertIs(raised.exception, error)
        self.assertIs(self.helper.subprocess, self.original)
        self.assert_closed(reader)

    def test_short_child_cannot_claim_effective_image(self):
        reader = Reader([(self.binary, str(self.binary)), execution.ProcessExited()])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader)
        self.assert_closed(reader)

    def test_initial_selected_image_does_not_prove_private_postbootstrap_model(self):
        reader = Reader([(self.binary, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError) as raised:
            self.run_case(reader, required_effective_role="private_managed_python")
        self.assertEqual(raised.exception.execution_evidence["failure"], "required_effective_role_not_observed")
        self.assert_closed(reader)

    def test_pid_reuse_and_helper_loaded_source_drift_rejected(self):
        class ReusedReader(Reader):
            def open_image(self, pid):
                image = super().open_image(pid)
                if len(self.fds) > 1:
                    image.process_start = 999
                return image
        reader = ReusedReader([(self.binary, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader)
        self.assert_closed(reader)
        self.helper._qa_loaded_source_sha256 = "0" * 64
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(Reader([(self.binary, str(self.binary))]))

    def test_malformed_pins_bounds_and_role_fail_before_popen(self):
        for options in ({"expected_sha256": None}, {"expected_sha256": 123}, {"timeout": True},
                        {"timeout": float("nan")}, {"max_images": True}, {"max_binary_bytes": 0},
                        {"required_effective_role": "anything"}):
            with self.subTest(options=options):
                launch = Mock()
                self.helper.subprocess = LocalSubprocess(self.original, launch)
                arguments = dict(timeout=1, env={}, expected_sha256=self.sha, private_home=self.home,
                    reader=Reader([(self.binary, str(self.binary))]))
                arguments.update(options)
                with self.assertRaises(execution.ExecutionObservationError):
                    execution.run_observed(self.helper, [str(self.binary)], **arguments)
                launch.assert_not_called()
                self.helper.subprocess = self.original

    def test_real_helper_timeout_cleans_observer_without_replacing_exception(self):
        selected = Path(sys.executable).resolve()
        reader = Reader([(selected, str(selected))])
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            execution.run_observed(self.helper,
                [str(selected), "-I", "-c", "import time; time.sleep(3)"], timeout=0.08,
                env={}, cwd=self.home, expected_sha256=hashlib.sha256(selected.read_bytes()).hexdigest(),
                private_home=self.home, reader=reader, poll_interval=0.002,
                required_effective_role="selected_python")
        evidence = raised.exception.execution_evidence
        self.assertEqual(evidence["status"], "incomplete")
        self.assertIs(self.helper.subprocess, self.original)
        self.assertFalse(any(t.name == f"qa-child-image-observer-{evidence['pid']}" for t in threading.enumerate()))
        self.assert_closed(reader)

    def test_history_and_binary_bytes_are_bounded(self):
        paths = [str(self.home / f".hermes/tools/python-3.14.{i}/bin/python3") for i in range(4)]
        reader = Reader([(self.binary, path) for path in paths])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader, max_images=2)
        self.assert_closed(reader)
        reader = Reader([(self.binary, str(self.binary))])
        with self.assertRaises(execution.ExecutionObservationError):
            self.run_case(reader, max_binary_bytes=2)
        self.assert_closed(reader)

    def test_no_linux_proc_fails_before_child_launch(self):
        with patch.object(execution.sys, "platform", "darwin"):
            with self.assertRaises(execution.ExecutionObservationError):
                execution.run_observed(self.helper, [str(self.binary)], timeout=1, env={},
                    expected_sha256=self.sha, private_home=self.home)
        self.assertIs(self.helper.subprocess, self.original)

    def test_proc_reader_pins_readonly_fd_and_rejects_link_inode_race(self):
        reader = object.__new__(execution.ProcReader)
        real_open = os.open
        facts = os.stat(self.binary)
        for linked_paths, should_pass in (([str(self.binary), str(self.binary)], True),
                                          ([str(self.binary), "/changed/python3"], False)):
            opened = []
            def proc_open(path, flags):
                self.assertEqual(path, "/proc/4321/exe")
                self.assertEqual(flags & os.O_ACCMODE, os.O_RDONLY)
                fd = real_open(self.binary, os.O_RDONLY)
                opened.append(fd)
                return fd
            with patch.object(reader, "_state", return_value=123), \
                    patch.object(execution.os, "readlink", side_effect=linked_paths), \
                    patch.object(execution.os, "stat", return_value=facts), \
                    patch.object(execution.os, "open", side_effect=proc_open):
                if should_pass:
                    image = reader.open_image(4321)
                    self.assertEqual(execution._hash_fd(image.fd, 1024), self.sha)
                    image.close()
                else:
                    with self.assertRaises(execution._Failure):
                        reader.open_image(4321)
            for fd in opened:
                with self.assertRaises(OSError):
                    os.fstat(fd)

    def test_proc_reader_missing_live_image_differs_from_normal_exit(self):
        reader = object.__new__(execution.ProcReader)
        for state, error in (([123, 123], execution._Failure), ([123, execution.ProcessExited()], execution.ProcessExited)):
            with patch.object(reader, "_state", side_effect=state), \
                    patch.object(execution.os, "readlink", side_effect=FileNotFoundError):
                with self.assertRaises(error):
                    reader.open_image(4321)

    def test_approved_production_hash_is_accepted_as_an_explicit_pin(self):
        approved = "8dfa9757a52b9c3edf1dedaaa2a7a8c40ea4beb058f20e90bdd47b48f3b1b176"
        reader = Reader([(self.binary, str(self.binary))])
        self.sha = approved
        with patch.object(execution, "_hash_fd", return_value=approved):
            _, evidence = self.run_case(reader)
        self.assertEqual(evidence["expected_sha256"], approved)
        self.assert_closed(reader)

    def test_real_linux_proc_contract_or_injected_host_independent_contract(self):
        # No skipped test on macOS: exercise the same actual unchanged helper with
        # an injected binary reader; native Linux additionally reads real /proc.
        selected = Path(sys.executable).resolve()
        sha = hashlib.sha256(selected.read_bytes()).hexdigest()
        helper = execution.load_helper(HELPER)
        reader = None if sys.platform == "linux" else Reader([(selected, str(selected))])
        result, evidence = execution.run_observed(helper,
            [str(selected), "-I", "-c", "import time; time.sleep(0.12); print('fictional child')"],
            timeout=2, env={"PATH": "/usr/bin:/bin"}, cwd=self.home,
            expected_sha256=sha, private_home=self.home, reader=reader, poll_interval=0.005,
            required_effective_role="selected_python")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(evidence["images"][0]["sha256_after"], sha)
        if reader:
            self.assert_closed(reader)


if __name__ == "__main__":
    unittest.main()
