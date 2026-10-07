"""Bounded Linux child-image evidence for the unchanged production helper.

This samples the *actual child PID*, including bootstrap execv transitions. It
does not promise that every brief exec was observed, or bind a descendant model
process. No command line, environment, output, or credential file is inspected.
Readers may be injected for synthetic tests; production evidence requires /proc.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from types import ModuleType


class ExecutionObservationError(RuntimeError):
    def __init__(self, evidence):
        super().__init__("Actual child execution identity is incomplete")
        self.execution_evidence = evidence


class ProcessExited(Exception):
    """The observed PID has exited; not an unreadable live executable."""


class ThreadGroupAlive(Exception):
    """Only the thread-group leader exited; a sibling still runs (and may exec) under this PID."""


class _Failure(Exception):
    pass


@dataclass
class ImageSnapshot:
    fd: int
    path: str
    device: int
    inode: int
    size: int
    process_start: int

    @classmethod
    def from_fd(cls, fd, path, *, process_start):
        facts = os.fstat(fd)
        return cls(fd, str(path), facts.st_dev, facts.st_ino, facts.st_size, process_start)

    def close(self):
        if self.fd >= 0:
            fd, self.fd = self.fd, -1
            os.close(fd)


_PF_EXITING = 0x4  # include/linux/sched.h


class ProcReader:
    """Only /proc/PID/exe and nonsecret PID state/start-time fields are read.

    stat start time detects PID reuse. The open FD pins the observed image even
    after process exit. A changed link/inode during capture is rejected.
    """
    def __init__(self):
        if sys.platform != "linux" or not Path("/proc/self/exe").exists():
            raise _Failure("linux_proc_required")

    @staticmethod
    def _facts(pid):
        with open(f"/proc/{pid}/stat", "rb") as stream:
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise _Failure("pid_state_invalid")
        try:
            # The comm field may contain spaces/parentheses; it is not cmdline.
            fields = raw.rsplit(b") ", 1)[1].split()
            return fields[0], int(fields[19]), int(fields[6])  # 22: starttime, 9: flags
        except (IndexError, ValueError):
            raise _Failure("pid_state_invalid") from None

    @classmethod
    def _state(cls, pid, *, image_missing_start=None):
        try:
            state, start, flags = cls._facts(pid)
        except (FileNotFoundError, ProcessLookupError):  # ESRCH: reaped between open/read
            raise ProcessExited() from None
        # After /proc/PID/exe vanished for the process started at image_missing_start:
        # do_exit() sets PF_EXITING before exit_mm() drops the link, and stat reports
        # Z only after exit_notify(); a new start means it was reaped and the PID reused.
        if image_missing_start is not None and start != image_missing_start:
            raise ProcessExited()
        def exiting(state, flags):
            return state in (b"Z", b"X") or (image_missing_start is not None and flags & _PF_EXITING)
        if exiting(state, flags):
            # Z/PF_EXITING describe the leader thread only: after a leader-only exit a
            # sibling keeps this PID alive and may exec. That exec swaps a live leader
            # (same start) in between our reads, so re-read after listing threads.
            if not cls._siblings_remain(pid):
                try:
                    state, again, flags = cls._facts(pid)
                except (FileNotFoundError, ProcessLookupError):
                    raise ProcessExited() from None
                if again != start or exiting(state, flags):
                    raise ProcessExited()
            raise ThreadGroupAlive()
        return start

    @staticmethod
    def _siblings_remain(pid):
        try:
            return any(tid != str(pid) for tid in os.listdir(f"/proc/{pid}/task"))
        except (FileNotFoundError, ProcessLookupError):
            return False

    def open_image(self, pid):
        start = self._state(pid)
        link = f"/proc/{pid}/exe"
        fd = -1
        try:
            path = os.readlink(link)
            fd = os.open(link, os.O_RDONLY | os.O_CLOEXEC)
            image = ImageSnapshot.from_fd(fd, path, process_start=start)
            if not self.confirm_image(pid, image):
                raise _Failure("image_capture_race")
            return image
        except FileNotFoundError:
            if fd >= 0:
                os.close(fd)
            self._state(pid, image_missing_start=start)
            raise _Failure("live_image_missing") from None
        except BaseException:
            if fd >= 0:
                os.close(fd)
            raise

    def start_identity(self, pid):
        return self._state(pid)

    def final_state(self, pid, process_start):
        try:
            state, start, _ = self._facts(pid)
        except (FileNotFoundError, ProcessLookupError):
            return "absent_after_helper_reap"
        if start != process_start:
            return "reused"
        return "unreaped" if state in (b"Z", b"X") else "live"

    def confirm_image(self, pid, image):
        start = self._state(pid)
        try:
            current = os.stat(f"/proc/{pid}/exe")
            return (start == image.process_start
                    and os.readlink(f"/proc/{pid}/exe") == image.path
                    and (current.st_dev, current.st_ino) == (image.device, image.inode))
        except FileNotFoundError:
            # Distinguish normal process exit from an unreadable live image.
            self._state(pid, image_missing_start=start)
            raise _Failure("live_image_missing") from None


def command_digest(command):
    return hashlib.sha256(json.dumps(list(command), ensure_ascii=True,
        separators=(",", ":")).encode()).hexdigest()


def load_helper(path):
    """Load the exact self-contained helper source; no credential config/pyc."""
    path = Path(path).resolve(strict=True)
    source = path.read_bytes()
    module = ModuleType("qa_execution_production_helper")
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    module._qa_loaded_source_sha256 = hashlib.sha256(source).hexdigest()
    return module


def _hash_fd(fd, max_binary_bytes, stop=None):
    facts = os.fstat(fd)
    if not stat.S_ISREG(facts.st_mode) or not 0 < facts.st_size <= max_binary_bytes:
        raise _Failure("binary_size_limit")
    deadline = time.monotonic() + 1.0
    sha = hashlib.sha256()
    offset = 0
    while offset < facts.st_size:
        if (stop is not None and stop.is_set()) or time.monotonic() >= deadline:
            raise _Failure("binary_hash_incomplete")
        chunk = os.pread(fd, min(1024 * 1024, facts.st_size - offset), offset)
        if not chunk:
            raise _Failure("binary_hash_incomplete")
        sha.update(chunk)
        offset += len(chunk)
    after = os.fstat(fd)
    if (after.st_dev, after.st_ino, after.st_size) != (facts.st_dev, facts.st_ino, facts.st_size):
        raise _Failure("binary_identity_changed")
    return sha.hexdigest()


def _path_role(path, selected, home):
    if not isinstance(path, str) or not path.startswith("/") or " (deleted)" in path:
        raise _Failure("unapproved_image_path")
    value = Path(path)
    if ".." in value.parts or "." in path.split("/"):
        raise _Failure("unapproved_image_path")
    if str(value) == str(selected):
        return "selected_python", str(value)
    try:
        parts = value.relative_to(home / ".hermes" / "tools").parts
    except ValueError:
        raise _Failure("unapproved_image_path") from None
    if (len(parts) != 3 or parts[1] != "bin"
            or not re.fullmatch(r"python-\d+\.\d+\.\d+(?:\+[-A-Za-z0-9_.]+)?", parts[0])
            or not re.fullmatch(r"python3(?:\.\d+)?", parts[2])):
        raise _Failure("unapproved_image_path")
    return "private_managed_python", "${QA_HOME}/.hermes/tools/" + "/".join(parts)


class _Observer:
    def __init__(self, reader, evidence, selected, home, *, poll_interval, max_images, max_binary_bytes):
        self.reader, self.evidence = reader, evidence
        self.selected, self.home = selected, home
        self.interval, self.max_images, self.max_bytes = poll_interval, max_images, max_binary_bytes
        self.stop_event = threading.Event()
        self.thread = None
        self.images = []
        self.path_inodes = {}
        self.start_time = None
        self.last_index = None
        self.last_consecutive = 0
        self.pid = None
        self.child = None

    def begin(self, child):
        if self.pid is not None:
            raise _Failure("multiple_child_launches")
        if type(child.pid) is not int or child.pid <= 0:
            raise _Failure("invalid_child_pid")
        self.pid = child.pid
        self.child = child
        self.evidence["pid"] = child.pid
        # Bind start ticks synchronously before returning Popen to the helper.
        # The helper has not yet reaped this child, so this cannot accidentally
        # adopt a reused PID on the sampler's first asynchronous read.
        self.start_time = self.reader.start_identity(child.pid)
        if type(self.start_time) is not int or self.start_time <= 0:
            raise _Failure("invalid_pid_identity")
        self.evidence["pid_start_ticks"] = self.start_time
        self.thread = threading.Thread(target=self._sample,
            name=f"qa-child-image-observer-{child.pid}", daemon=True)
        self.thread.start()

    def _sample(self):
        began = time.monotonic()
        try:
            while not self.stop_event.is_set():
                try:
                    image = self.reader.open_image(self.pid)
                    owned = True
                    try:
                        if image.process_start != self.start_time:
                            raise _Failure("pid_identity_changed")
                        role, normalized = _path_role(image.path, self.selected, self.home)
                        inode = (image.device, image.inode)
                        old_inode = self.path_inodes.get(image.path)
                        if old_inode is not None and inode != old_inode:
                            raise _Failure("image_inode_changed")
                        self.path_inodes[image.path] = inode
                        index = next((i for i, (saved, _) in enumerate(self.images)
                                      if saved.path == image.path and (saved.device, saved.inode) == inode), None)
                        if index is None:
                            if len(self.images) >= self.max_images:
                                raise _Failure("image_history_limit")
                            before = _hash_fd(image.fd, self.max_bytes, self.stop_event)
                            if before != self.evidence["expected_sha256"]:
                                raise _Failure("unapproved_binary_hash")
                            if not self.reader.confirm_image(self.pid, image):
                                raise _Failure("image_capture_race")
                            index = len(self.images)
                            row = {"path": image.path, "normalized_path": normalized, "role": role,
                                   "device": image.device, "inode": image.inode, "size": image.size,
                                   "path_source": "/proc/PID/exe" if self.evidence["reader_kind"] == "linux_proc" else "injected",
                                   "sha256_before": before, "sha256_after": None, "samples": 0}
                            self.images.append((image, row))
                            self.evidence["images"].append(row)
                            owned = False
                        if index != self.last_index:
                            if len(self.evidence["observed_transitions"]) >= 32:
                                raise _Failure("transition_history_limit")
                            self.evidence["observed_transitions"].append(
                                {"image": index, "elapsed_seconds": round(time.monotonic() - began, 6)})
                            self.last_consecutive = 0
                        self.last_index = index
                        self.last_consecutive += 1
                        self.images[index][1]["samples"] += 1
                        self.evidence["samples"] += 1
                    finally:
                        if owned:
                            image.close()
                except ThreadGroupAlive:
                    # From open_image or the post-hash confirm: the exited leader exposes
                    # no image, but a sibling may still exec. The finally closed any image.
                    pass
                self.stop_event.wait(self.interval)
        except ProcessExited:
            self.evidence["process_exit_observed"] = True
        except BaseException as error:
            # Never expose reader/system exception text or a credential value.
            self.evidence["failure"] = str(error) if isinstance(error, _Failure) else "image_observation_failed"

    def finish(self, result, helper_completed):
        self.stop_event.set()
        if self.thread and self.thread.ident is not None:
            self.thread.join(timeout=2.0)
            if self.thread.is_alive():
                self.evidence["failure"] = "observer_cleanup_incomplete"
        try:
            self.evidence["helper_completed"] = helper_completed
            child_code = getattr(self.child, "returncode", None)
            self.evidence["child_returncode"] = child_code if type(child_code) is int else None
            if helper_completed:
                result_code = getattr(result, "returncode", None)
                self.evidence["helper_returncode"] = result_code if type(result_code) is int else None
                if (self.child is None or getattr(self.child, "pid", None) != self.pid
                        or type(child_code) is not int or type(result_code) is not int
                        or child_code != result_code):
                    raise _Failure("child_completion_unbound")
                final_state = self.reader.final_state(self.pid, self.start_time)
                if final_state not in ("absent_after_helper_reap", "reused", "unreaped", "live"):
                    raise _Failure("final_pid_state_unknown")
                self.evidence["final_pid_state"] = final_state
                if final_state != "absent_after_helper_reap":
                    raise _Failure("child_not_verified_reaped")
                self.evidence["process_exit_observed"] = True
                # The unchanged helper returned only after its finally cleanup;
                # neither the observer nor its proxy calls poll/wait/reap.
                self.evidence["helper_cleanup_completed"] = True
            if self.evidence.get("failure") != "observer_cleanup_incomplete":
                # Preserve diagnostic FD identities even when the helper raised
                # or an image failed to qualify; these cannot grant PASS.
                for image, row in self.images:
                    after = _hash_fd(image.fd, self.max_bytes)
                    row["sha256_after"] = after
                    facts = os.fstat(image.fd)
                    row["device_after"], row["inode_after"], row["size_after"] = (
                        facts.st_dev, facts.st_ino, facts.st_size)
                    if (after != row["sha256_before"]
                            or (facts.st_dev, facts.st_ino, facts.st_size)
                            != (image.device, image.inode, image.size)):
                        raise _Failure("executing_image_changed")
            if not self.evidence.get("failure"):
                if self.pid is None or self.last_consecutive < 2:
                    self.evidence["failure"] = "effective_image_not_stably_observed"
                elif (self.evidence["required_effective_role"] is not None
                        and self.images[self.last_index][1]["role"] != self.evidence["required_effective_role"]):
                    self.evidence["failure"] = "required_effective_role_not_observed"
                else:
                    self.evidence["effective_observed_image"] = self.last_index
        except BaseException as error:
            self.evidence["failure"] = str(error) if isinstance(error, _Failure) else "image_rehash_failed"
        finally:
            for image, _ in self.images:
                try:
                    image.close()
                except OSError:
                    self.evidence["failure"] = "observer_fd_cleanup_failed"


class _SubprocessProxy:
    def __init__(self, original, observer):
        self.original, self.observer = original, observer

    def __getattr__(self, name):
        return getattr(self.original, name)

    def Popen(self, *args, **kwargs):
        if self.observer.pid is not None:
            raise _Failure("multiple_child_launches")
        child = self.original.Popen(*args, **kwargs)
        # Observation failures are evidence failures. Let the helper reach its
        # original cleanup rather than throwing between Popen and helper try.
        try:
            self.observer.begin(child)
        except BaseException:
            self.observer.evidence["failure"] = "observer_start_failed"
        return child


def run_observed(helper, command, *, timeout, env, expected_sha256, private_home,
                 selected_python=None, cwd=None, capture_output=True, text=True,
                 reader=None, poll_interval=0.01, max_images=8, max_binary_bytes=128 * 1024 * 1024,
                 required_effective_role="private_managed_python"):
    """Return (CompletedProcess, sampled-image evidence), or fail closed.

    Only helper.subprocess is temporarily replaced; the real Popen child and all
    command/env/lifecycle arguments are untouched. Use a fresh loaded helper per
    call; sharing this local module concurrently is unsupported. Injected readers
    must keep each call bounded; they are only a synthetic test seam. A caller cannot
    turn injected-reader evidence into a Linux /proc claim: reader_kind is bound.
    Hashes pin opened image FDs before and after helper completion. Polling may
    miss brief execs; verified_sampled is explicitly not an all-execs proof. The
    default requires the last stable observed image to be private managed Python,
    not just the initial selected interpreter. OS exe is a resolved kernel image;
    it does not reveal Python's possibly aliased sys.executable string.
    """
    evidence = {"schema": 1, "status": "incomplete", "pid": None, "images": [],
                "samples": 0, "observed_transitions": [], "effective_observed_image": None,
                "all_exec_transitions_observed": False, "process_exit_observed": False,
                "helper_completed": False, "helper_cleanup_completed": False,
                "child_returncode": None, "helper_returncode": None, "final_pid_state": None,
                "reader_kind": "linux_proc" if reader is None else "injected",
                "required_effective_role": None,
                "expected_sha256": None, "requested_launch_sha256": None}
    if (not isinstance(expected_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
            or not isinstance(command, (list, tuple)) or not command
            or not all(isinstance(value, str) for value in command)
            or type(timeout) not in (int, float) or not 0 < timeout <= 600
            or type(poll_interval) not in (int, float) or not 0.001 <= poll_interval <= 0.1
            or type(max_images) is not int or not 1 <= max_images <= 16
            or type(max_binary_bytes) is not int or not 1 <= max_binary_bytes <= 256 * 1024 * 1024):
        evidence["failure"] = "invalid_observation_bounds"
        raise ExecutionObservationError(evidence)
    if required_effective_role not in (None, "selected_python", "private_managed_python"):
        evidence["failure"] = "invalid_effective_role"
        raise ExecutionObservationError(evidence)
    evidence["expected_sha256"] = expected_sha256
    evidence["required_effective_role"] = required_effective_role
    evidence["requested_launch_sha256"] = command_digest(command)
    selected = Path(selected_python or command[0])
    if not selected.is_absolute() or str(selected) != command[0]:
        evidence["failure"] = "selected_command_mismatch"
        raise ExecutionObservationError(evidence)
    try:
        selected = selected.resolve(strict=True)
        home = Path(private_home).resolve(strict=True)
        evidence["selected_python"] = str(selected)
        source = Path(helper.__file__)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        if getattr(helper, "_qa_loaded_source_sha256", None) != before:
            raise _Failure("helper_loaded_source_mismatch")
        evidence["helper_sha256_before"] = before
        reader = ProcReader() if reader is None else reader
    except BaseException:
        evidence["failure"] = "observation_setup_failed"
        raise ExecutionObservationError(evidence) from None
    observer = _Observer(reader, evidence, selected, home, poll_interval=poll_interval,
                         max_images=max_images, max_binary_bytes=max_binary_bytes)
    original = helper.subprocess
    if isinstance(original, _SubprocessProxy):
        evidence["failure"] = "helper_already_observed"
        raise ExecutionObservationError(evidence)
    helper.subprocess = _SubprocessProxy(original, observer)
    result = None
    helper_completed = False
    try:
        result = helper.run_bounded(command, timeout=timeout, env=env, cwd=cwd,
                                   capture_output=capture_output, text=text)
        helper_completed = True
    except BaseException as error:
        evidence["failure"] = "helper_failed"
        # Retain the original type/value (e.g. TimeoutExpired) and lifecycle.
        try:
            error.execution_evidence = evidence
        except (AttributeError, TypeError):
            pass
        raise
    finally:
        helper.subprocess = original
        observer.finish(result, helper_completed)
        try:
            evidence["helper_sha256_after"] = hashlib.sha256(source.read_bytes()).hexdigest()
            if evidence["helper_sha256_after"] != before:
                evidence["failure"] = "helper_source_changed"
        except OSError:
            evidence["failure"] = "helper_source_unreadable"
    if evidence.get("failure"):
        raise ExecutionObservationError(evidence)
    evidence["status"] = "verified_sampled"
    return result, evidence
