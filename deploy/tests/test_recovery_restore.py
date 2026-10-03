"""Synthetic backup-restore validation tests; no live paths or secrets."""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ops"))

import recovery_restore  # noqa: E402
import recovery_policy  # noqa: E402


class RecoveryExampleTests(unittest.TestCase):
    def test_example_is_accepted_by_current_policy(self):
        plan = json.loads((ROOT / 'deploy/recovery-plan.example.json').read_text())
        entries = recovery_policy.plan_entries(plan)
        self.assertIn('/etc/systemd/system/helpdesk-inbox2.service', [entry['path'] for entry in entries])

    def test_retired_unit_stays_rejected(self):
        plan = json.loads((ROOT / 'deploy/recovery-plan.example.json').read_text())
        plan['entries'].append({'path': '/etc/systemd/system/helpdesk-inbox.service', 'kind': 'file'})
        with self.assertRaisesRegex(ValueError, 'Unapproved'):
            recovery_policy.plan_entries(plan)


class FakePolicy:
    def __call__(self, path, kind):
        if kind == "sqlite":
            return path == "/restore/webhook.sqlite3"
        if kind == "file":
            return path == "/restore/.env"
        return False


def make_plan():
    return {
        "schema": 1,
        "release_commit": "0" * 40,
        "recipient_sha256": "1" * 64,
        "entries": [
            {"path": "/restore/.env", "kind": "file"},
            {"path": "/restore/webhook.sqlite3", "kind": "sqlite"},
        ],
    }


def make_sqlite_bytes():
    # A minimal valid SQLite database file: header page from an empty database.
    # Creating a real one with the sqlite3 module is easier and stays portable.
    import sqlite3

    with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as handle:
        path = Path(handle.name)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE demo(value TEXT)")
    connection.commit()
    connection.close()
    data = path.read_bytes()
    path.unlink()
    return data


def make_archive(records_payloads):
    """Build an uncompressed tar with manifest.json and payload members."""
    manifest = {
        "schema": 1,
        "plan": make_plan(),
        "records": [record for record, _ in records_payloads],
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        manifest_bytes = json.dumps(manifest).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        archive.addfile(info, io.BytesIO(manifest_bytes))
        for (_, payload_bytes), index in zip(records_payloads, range(len(records_payloads))):
            name = f"payload-{index:06d}"
            info = tarfile.TarInfo(name)
            info.size = len(payload_bytes)
            archive.addfile(info, io.BytesIO(payload_bytes))
    return buffer.getvalue()


def _nonsymlink_tmpdir() -> str:
    """Temp root with no symlink path components (private_directory uses O_NOFOLLOW)."""
    return os.path.realpath(tempfile.gettempdir())


def _ustar_header(name, *, typeflag=tarfile.REGTYPE, size=0, linkname=""):
    info = tarfile.TarInfo(name)
    info.type = typeflag
    info.size = size
    if linkname:
        info.linkname = linkname
    header = info.tobuf(format=tarfile.USTAR_FORMAT, encoding="utf-8", errors="strict")
    if len(header) != 512:
        raise AssertionError(f"expected 512-byte ustar header, got {len(header)}")
    return header


def _replace_member_header(archive_bytes, member_name, header):
    data = bytearray(archive_bytes)
    offset = 0
    while offset + 512 <= len(data):
        block = bytes(data[offset:offset + 512])
        if block == b"\0" * 512:
            break
        info = tarfile.TarInfo.frombuf(block, "utf-8", "strict")
        if info.name == member_name:
            data[offset:offset + 512] = header
            return bytes(data)
        offset += 512 + ((info.size + 511) // 512) * 512
    raise AssertionError(f"member {member_name} not found")


class RecoveryRestoreTests(unittest.TestCase):
    def setUp(self):
        # private_directory() traverses each path component with O_NOFOLLOW
        # from the filesystem root, so the fixture must live under a real
        # (non-symlinked) absolute prefix. On macOS /tmp is a symlink to
        # /private/tmp, which O_NOFOLLOW correctly refuses.
        self.temp = tempfile.TemporaryDirectory(dir=_nonsymlink_tmpdir())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _write_archive(self, data):
        archive = self.root / "archive.tar"
        archive.write_bytes(data)
        return archive

    def _destination(self, name="restore-output"):
        destination = self.root / name
        destination.mkdir(mode=0o700)
        return destination

    def _file_record(self, path, data, payload):
        return (
            {
                "path": path,
                "kind": "file",
                "mode": 0o600,
                "uid": 0,
                "gid": 0,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "payload": payload,
            },
            data,
        )

    def _sqlite_record(self, path, data, payload):
        return (
            {
                "path": path,
                "kind": "sqlite",
                "mode": 0o600,
                "uid": 0,
                "gid": 0,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "payload": payload,
            },
            data,
        )

    def test_well_formed_archive_restores_into_empty_private_destination(self):
        env_data = b"synthetic restore content\n"
        sqlite_data = make_sqlite_bytes()
        records_payloads = [
            self._file_record("/restore/.env", env_data, "payload-000000"),
            self._sqlite_record("/restore/webhook.sqlite3", sqlite_data, "payload-000001"),
        ]
        archive = self._write_archive(make_archive(records_payloads))
        destination = self._destination()

        result = recovery_restore.validate_archive(archive, destination, make_plan(), policy=FakePolicy())

        self.assertEqual(result["verification"], "isolated-files-and-sqlite-validated")
        self.assertEqual(result["members"], 2)
        self.assertFalse(result["services_started"])
        restored_env = destination / "payload-000000"
        restored_db = destination / "payload-000001"
        self.assertEqual(restored_env.read_bytes(), env_data)
        self.assertEqual(restored_db.read_bytes(), sqlite_data)
        self.assertEqual(stat.S_IMODE(restored_env.stat().st_mode), 0o600)
        self.assertTrue((destination / "manifest.json").is_file())

    def test_record_outside_approved_plan_is_rejected(self):
        env_data = b"synthetic restore content\n"
        records_payloads = [self._file_record("/outside/plan.txt", env_data, "payload-000000")]
        archive = self._write_archive(make_archive(records_payloads))
        destination = self._destination()

        with self.assertRaises(ValueError):
            recovery_restore.validate_archive(archive, destination, make_plan(), policy=FakePolicy())

        self.assertFalse(list(destination.iterdir()))

    def test_unexpected_archive_member_is_rejected(self):
        env_data = b"synthetic restore content\n"
        records_payloads = [self._file_record("/restore/.env", env_data, "payload-000000")]
        data = make_archive(records_payloads)
        buffer = io.BytesIO(data)
        with tarfile.open(fileobj=buffer, mode="a") as archive:
            info = tarfile.TarInfo("extra-member.txt")
            info.size = 0
            archive.addfile(info, io.BytesIO(b""))
        archive = self._write_archive(buffer.getvalue())
        destination = self._destination()

        with self.assertRaises(ValueError):
            recovery_restore.validate_archive(archive, destination, make_plan(), policy=FakePolicy())

    def test_non_empty_destination_is_refused(self):
        destination = self._destination()
        (destination / "existing.txt").write_text("occupied")
        archive = self._write_archive(b"")

        with self.assertRaises(ValueError):
            recovery_restore.validate_archive(archive, destination, make_plan(), policy=FakePolicy())

    def test_destination_must_be_owner_private(self):
        destination = self.root / "public-output"
        destination.mkdir(mode=0o755)
        archive = self._write_archive(b"")

        with self.assertRaises(ValueError):
            recovery_restore.validate_archive(archive, destination, make_plan(), policy=FakePolicy())

    def test_unsupported_tar_headers_and_size_limits_are_rejected(self):
        env_data = b"synthetic restore content\n"
        base = make_archive([self._file_record("/restore/.env", env_data, "payload-000000")])
        cases = (
            ("symlink", tarfile.SYMTYPE, 0, "evil-target"),
            ("pax", tarfile.XHDTYPE, 0, ""),
            ("gnu", tarfile.GNUTYPE_LONGNAME, 0, ""),
        )
        for label, typeflag, size, linkname in cases:
            with self.subTest(kind=label):
                header = _ustar_header(
                    "payload-000000",
                    typeflag=typeflag,
                    size=size,
                    linkname=linkname,
                )
                archive = self._write_archive(
                    _replace_member_header(base, "payload-000000", header),
                )
                with self.assertRaises(ValueError):
                    recovery_restore.validate_archive(
                        archive,
                        self._destination(f"restore-{label}"),
                        make_plan(),
                        policy=FakePolicy(),
                    )

        with self.subTest(kind="oversize"):
            header = _ustar_header(
                "payload-000000",
                typeflag=tarfile.REGTYPE,
                size=recovery_policy.MAX_DATABASE + 1,
            )
            archive = self._write_archive(
                _replace_member_header(base, "payload-000000", header),
            )
            with self.assertRaises(ValueError):
                recovery_restore.validate_archive(
                    archive,
                    self._destination("restore-oversize"),
                    make_plan(),
                    policy=FakePolicy(),
                )


if __name__ == "__main__":
    unittest.main()
