import io
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


class RetentionTests(unittest.TestCase):
    def test_prunes_old_directories_and_preserves_current_release(self) -> None:
        script = embedded_python('python3 - "$root"')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / f"release-{index}" for index in range(4)]
            for index, path in enumerate(paths):
                path.mkdir()
                os.utime(path, (index + 1, index + 1))

            result = subprocess.run(
                [sys.executable, "-", str(root), "2", str(paths[0])],
                input=script,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(paths[0].exists())
            self.assertFalse(paths[1].exists())
            self.assertTrue(paths[2].exists())
            self.assertTrue(paths[3].exists())


class DeploymentGuardrailTests(unittest.TestCase):
    def test_bounded_input_and_hardened_copy_flags_are_present(self) -> None:
        source = RECEIVER.read_text(encoding="utf-8")
        self.assertIn('head -c "$((max_archive_bytes + 1))"', source)
        self.assertIn("--no-same-owner --no-same-permissions", source)
        self.assertIn("--no-specials --no-devices", source)

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


if __name__ == "__main__":
    unittest.main()
