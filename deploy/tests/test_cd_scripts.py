import io
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RECEIVER = ROOT / "deploy/cd/buttonsbebe-deploy-receive.sh"


def embedded_python(after: str) -> str:
    source = RECEIVER.read_text(encoding="utf-8")
    match = re.search(
        rf"{re.escape(after)}.*?<<'PY'\n(?P<script>.*?)\nPY",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise AssertionError(f"embedded Python block after {after!r} was not found")
    return match.group("script")


def write_archive(path: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for member in members:
            data = io.BytesIO(b"x" * member.size) if member.isfile() else None
            archive.addfile(member, data)


class ArchiveValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = embedded_python('python3 - "$staging_archive"')

    def validate(self, members: list[tarfile.TarInfo], script: str | None = None):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "release.tar.gz"
            write_archive(archive, members)
            return subprocess.run(
                [sys.executable, "-", str(archive)],
                input=script or self.validator,
                text=True,
                capture_output=True,
                check=False,
            )

    def test_accepts_regular_files_and_directories(self) -> None:
        directory = tarfile.TarInfo("webhook")
        directory.type = tarfile.DIRTYPE
        file = tarfile.TarInfo("webhook/app.py")
        file.size = 4

        result = self.validate([directory, file])

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_path_traversal(self) -> None:
        member = tarfile.TarInfo("../outside")
        member.size = 1

        result = self.validate([member])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe archive member", result.stderr)

    def test_rejects_normalized_duplicate_paths(self) -> None:
        first = tarfile.TarInfo("webhook/app.py")
        second = tarfile.TarInfo("./webhook/app.py")
        result = self.validate([first, second])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate archive member", result.stderr)

    def test_rejects_special_files(self) -> None:
        member = tarfile.TarInfo("named-pipe")
        member.type = tarfile.FIFOTYPE

        result = self.validate([member])

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported archive member type", result.stderr)

    def test_enforces_member_count_limit(self) -> None:
        script = self.validator.replace("MAX_MEMBER_COUNT = 20_000", "MAX_MEMBER_COUNT = 1")
        first = tarfile.TarInfo("one")
        second = tarfile.TarInfo("two")

        result = self.validate([first, second], script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("too many members", result.stderr)

    def test_enforces_expanded_size_limit(self) -> None:
        script = self.validator.replace(
            "MAX_EXPANDED_BYTES = 256 * 1024 * 1024",
            "MAX_EXPANDED_BYTES = 1",
        )
        member = tarfile.TarInfo("two-bytes")
        member.size = 2

        result = self.validate([member], script)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("expands beyond", result.stderr)


class DeploymentGuardrailTests(unittest.TestCase):
    def test_bounded_input_and_hardened_copy_flags_are_present(self) -> None:
        source = RECEIVER.read_text(encoding="utf-8")
        self.assertIn('head -c "$((max_archive_bytes + 1))"', source)
        self.assertIn("--no-same-owner --no-same-permissions", source)
        self.assertNotIn("rsync", source)

    def test_readiness_retries_do_not_trigger_the_global_rollback_trap(self) -> None:
        source = RECEIVER.read_text(encoding="utf-8")
        readiness = source.split("readiness_ok() (", 1)[1].split("\n)", 1)[0]
        self.assertIn("trap - ERR", readiness)
        self.assertIn("curl --fail", readiness)

    def test_host_lock_precedes_staging_and_no_live_dependency_mutation(self) -> None:
        source = RECEIVER.read_text()
        self.assertIn("flock -n 9", source)
        self.assertLess(source.index("flock -n 9"), source.index("mktemp"))
        for forbidden in ("uv sync", "pip install", "npm ci", "scripts/index_kb.py", "rsync"):
            self.assertNotIn(forbidden, source)
        self.assertIn('python3 "$source_helper" rollback', source)
        self.assertIn("ROLLBACK INCOMPLETE", source)
        self.assertIn("wait_ready || failed=1", source)
        self.assertNotIn('[[ "$whatsapp_state" != "connected" ]]', source)

    def test_checkout_and_sudo_do_not_persist_or_prompt_for_credentials(self) -> None:
        workflow = (ROOT / ".github/workflows/deploy-production.yml").read_text(
            encoding="utf-8"
        )
        wrapper = (ROOT / "deploy/cd/buttonsbebe-deploy-ssh.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn('exec sudo -n "$receiver"', wrapper)

    def test_pr_review_gate_keeps_a_read_only_token(self) -> None:
        workflow = (ROOT / ".github/workflows/pr-review.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull-requests: read", workflow)
        self.assertNotIn("issues: write", workflow)
        self.assertNotIn("pull-requests: write", workflow)
        self.assertIn("core.summary.addRaw(body).write()", workflow)


class AppliedConsumerUnitTests(unittest.TestCase):
    """Run the actual embedded guard on private files without Linux services."""
    UNITS = ('buttonsbebe-webhook.service', 'buttonsbebe-processor.service',
             'buttonsbebe-gorgias-mcp.service')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.release = self.root / 'release'
        self.approval = self.root / 'approved'
        self.installed = {}
        source = RECEIVER.read_text()
        self.guard = source.split("<<'PYCONFIG'\n", 1)[1].split('\nPYCONFIG', 1)[0]
        paths = ('/etc/caddy/sites/support.caddy',
                 '/etc/systemd/system/helpdesk-inbox2.service',
                 '/etc/systemd/system/buttonsbebe-inbox-projection.service',
                 '/etc/systemd/system/buttonsbebe-inbox-projection.timer')
        for path in paths + tuple('/etc/systemd/system/' + name for name in self.UNITS):
            target = self.root / 'installed' / Path(path).name
            target.parent.mkdir(parents=True, exist_ok=True)
            self.guard = self.guard.replace(path, str(target))
            if Path(path).name in self.UNITS:
                body = (ROOT / 'deploy/systemd' / Path(path).name).read_bytes()
                candidate = self.release / 'deploy/systemd' / Path(path).name
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(body)
                self.installed[Path(path).name] = target
            else:
                body = b'reviewed unrelated configuration'
            target.write_bytes(body)
        self.approve()

    def approve(self, omit=()):
        self.approval.write_text(''.join(str(path) + ' ' + hashlib.sha256(path.read_bytes()).hexdigest() + '\n'
            for path in sorted((self.root / 'installed').iterdir()) if path.name not in omit))

    def run_guard(self):
        return subprocess.run([sys.executable, '-', str(self.approval), str(self.release)],
            input=self.guard, text=True, capture_output=True, timeout=10)

    def assert_missing_refused(self, *names):
        self.approve(omit=names)
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Shared intake consumer unit applied fingerprints are required', result.stderr)

    def test_missing_all_consumer_fingerprints(self):
        self.assert_missing_refused(*self.UNITS)

    def test_missing_webhook_fingerprint(self):
        self.assert_missing_refused('buttonsbebe-webhook.service')

    def test_missing_processor_fingerprint(self):
        self.assert_missing_refused('buttonsbebe-processor.service')

    def test_missing_gorgias_fingerprint(self):
        self.assert_missing_refused('buttonsbebe-gorgias-mcp.service')

    def test_approval_of_prior_units_cannot_authorize_incompatible_release(self):
        for path in self.installed.values():
            old = path.read_bytes().replace(b'/opt/buttonsbebe/shared:', b'')
            old = old.replace(b'Environment=PYTHONPATH=/opt/buttonsbebe/shared\n', b'')
            path.write_bytes(old)
        self.approve()
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Shared intake consumer unit differs from reviewed release', result.stderr)

    def test_post_approval_drift_is_refused(self):
        self.installed[self.UNITS[0]].write_bytes(b'unapproved change')
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Applied configuration drift', result.stderr)

    def test_each_approved_noncanonical_fragment_is_refused(self):
        for name in self.UNITS:
            with self.subTest(unit=name):
                path = self.installed[name]
                canonical = path.read_bytes()
                path.write_bytes(canonical + b'# approved host variant\n')
                self.approve()
                result = self.run_guard()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Shared intake consumer unit differs from reviewed release', result.stderr)
                self.assertIn(name, result.stderr)
                path.write_bytes(canonical)

    def test_missing_canonical_release_fragment_is_refused(self):
        (self.release / 'deploy/systemd' / self.UNITS[0]).unlink()
        result = self.run_guard()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Reviewed shared intake consumer unit is absent', result.stderr)

    def test_canonical_approved_units_and_separately_approved_dropin_pass(self):
        dropin = self.root / 'installed/reviewed-dropin.conf'
        dropin.write_bytes(b'reviewed drop-in preserved')
        self.approve()
        before = {path: path.read_bytes() for path in (self.root / 'installed').iterdir()}
        result = self.run_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual({path: path.read_bytes() for path in before}, before)


if __name__ == "__main__":
    unittest.main()
