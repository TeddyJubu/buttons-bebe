"""Offline tests for processor/heartbeat.sh.

The script only ever shells out to systemctl / journalctl / curl, so we can
exercise every branch by putting stubs for those three on PATH and reading
back what the script tried to do. Nothing here touches systemd, the network,
or the live processor.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "heartbeat.sh"

_SYSTEMCTL_STUB = """#!/bin/sh
# usage: systemctl is-active <unit>
if [ "$1" = "is-active" ]; then
    printf '%s\\n' "${FAKE_ACTIVE:-active}"
    [ "${FAKE_ACTIVE:-active}" = "active" ] && exit 0 || exit 3
fi
exit 0
"""

_JOURNALCTL_STUB = """#!/bin/sh
# Emit FAKE_JOURNAL_LINES lines. FAKE_JOURNAL_KIND picks what kind:
#   healthy -> the markers that mean the loop is turning
#   errors  -> a processor that is up but failing every single ticket
i=0
while [ "$i" -lt "${FAKE_JOURNAL_LINES:-3}" ]; do
    if [ "${FAKE_JOURNAL_KIND:-healthy}" = "healthy" ]; then
        echo "bb-processor: Processor idle heartbeat pid=123"
    else
        echo "bb-processor: Job failed - exception: hermes: command not found"
    fi
    i=$((i + 1))
done
exit 0
"""

_CURL_STUB = """#!/bin/sh
# Record every argument, one per line, then exit with FAKE_CURL_EXIT.
for arg in "$@"; do
    printf '%s\\n' "$arg" >> "$CURL_LOG"
done
printf -- '--END--\\n' >> "$CURL_LOG"
exit "${FAKE_CURL_EXIT:-0}"
"""


class HeartbeatTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (
            ("systemctl", _SYSTEMCTL_STUB),
            ("journalctl", _JOURNALCTL_STUB),
            ("curl", _CURL_STUB),
        ):
            path = self.bin / name
            path.write_text(body, encoding="utf-8")
            path.chmod(0o755)
        self.state_file = self.tmp / "tmp" / "buttonsbebe-heartbeat.state"
        self.curl_log = self.tmp / "curl.log"
        self.addCleanup(self._tmp.cleanup)

    def run_heartbeat(self, *, active: str = "active", journal_lines: int = 3,
                      journal_kind: str = "healthy", curl_exit: int = 0,
                      url: str = "http://127.0.0.1:8085/send",
                      secret: str = "s3cret",
                      state_file=None) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        env["FAKE_ACTIVE"] = active
        env["FAKE_JOURNAL_LINES"] = str(journal_lines)
        env["FAKE_JOURNAL_KIND"] = journal_kind
        env["FAKE_CURL_EXIT"] = str(curl_exit)
        env["CURL_LOG"] = str(self.curl_log)
        env["HEARTBEAT_STATE_FILE"] = str(state_file or self.state_file)
        env["PROCESSOR_UNIT"] = "buttonsbebe-processor"
        env["HEARTBEAT_STALE_MINUTES"] = "10"
        env["WHATSAPP_SEND_URL"] = url
        env["WA_SEND_SECRET"] = secret
        return subprocess.run(
            ["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60,
        )

    def sent(self) -> str:
        return self.curl_log.read_text(encoding="utf-8") if self.curl_log.exists() else ""

    def alert_count(self) -> int:
        return self.sent().count("--END--")

    # ── happy path ──────────────────────────────────────────────────────
    def test_healthy_processor_sends_nothing(self):
        proc = self.run_heartbeat()
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.alert_count(), 0)
        self.assertFalse(self.state_file.exists())
        self.assertIn("processor healthy", proc.stderr)

    # ── outage detection ────────────────────────────────────────────────
    def test_inactive_service_alerts_once_and_latches(self):
        first = self.run_heartbeat(active="failed")
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.alert_count(), 1)
        self.assertIn("looks DOWN", self.sent())
        self.assertTrue(self.state_file.exists())

        second = self.run_heartbeat(active="failed")
        self.assertEqual(second.returncode, 0)
        self.assertEqual(self.alert_count(), 1, "must not re-alert while still down")
        self.assertIn("staying quiet", second.stderr)

    def test_recovery_clears_state_and_sends_one_all_clear(self):
        self.run_heartbeat(active="inactive")
        self.assertEqual(self.alert_count(), 1)

        recovered = self.run_heartbeat(active="active")
        self.assertEqual(recovered.returncode, 0)
        self.assertEqual(self.alert_count(), 2)
        self.assertIn("back up", self.sent())
        self.assertFalse(self.state_file.exists())

        quiet = self.run_heartbeat(active="active")
        self.assertEqual(quiet.returncode, 0)
        self.assertEqual(self.alert_count(), 2, "must not repeat the all-clear")

    def test_active_but_silent_journal_is_treated_as_hung(self):
        proc = self.run_heartbeat(active="active", journal_lines=0)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.alert_count(), 1)
        self.assertIn("possibly hung", self.sent())

    # ── auth contract (must match whatsapp_notifier.py) ─────────────────
    def test_secret_travels_in_the_header_never_in_the_url(self):
        self.run_heartbeat(active="failed", url="http://127.0.0.1:8085/send",
                           secret="topsecret")
        sent = self.sent()
        self.assertIn("Authorization: Bearer topsecret", sent)
        url_lines = [ln for ln in sent.splitlines() if ln.startswith("http")]
        self.assertTrue(url_lines)
        for line in url_lines:
            self.assertNotIn("topsecret", line)

    def test_body_is_valid_json(self):
        import json
        self.run_heartbeat(active="failed")
        lines = self.sent().splitlines()
        body = lines[lines.index("-d") + 1]
        self.assertIn("text", json.loads(body))

    # ── fail-soft behaviour ─────────────────────────────────────────────
    def test_missing_credentials_logs_but_never_fails(self):
        proc = self.run_heartbeat(active="failed", url="", secret="")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.alert_count(), 0)
        self.assertIn("WHATSAPP_SEND_URL", proc.stderr)
        self.assertFalse(
            self.state_file.exists(),
            "must NOT latch on an undelivered alert - the owner never heard "
            "about this outage, so the next run has to try again",
        )

    # ── regressions from the code review ────────────────────────────────
    def test_an_undelivered_alert_is_retried_not_latched(self):
        """The dead-man's switch must survive WhatsApp being down too.

        A reboot, an OOM or a full disk kills the processor AND
        whatsapp-connect. Latching on the attempt meant the owner was never
        told, ever, in exactly that correlated-failure case.
        """
        first = self.run_heartbeat(active="failed", curl_exit=7)
        self.assertEqual(first.returncode, 0)
        self.assertEqual(self.alert_count(), 1, "it should have tried")
        self.assertFalse(self.state_file.exists(), "must not latch on a failed POST")
        self.assertIn("staying unlatched", first.stderr)

        # WhatsApp comes back: the very next firing delivers.
        second = self.run_heartbeat(active="failed", curl_exit=0)
        self.assertEqual(self.alert_count(), 2)
        self.assertTrue(self.state_file.exists())

        third = self.run_heartbeat(active="failed", curl_exit=0)
        self.assertEqual(self.alert_count(), 2, "now it latches and stays quiet")

    def test_a_processor_failing_every_ticket_is_not_healthy(self):
        """Errors produce MORE journal lines than idling, so counting lines
        made a total outage look perfectly well."""
        proc = self.run_heartbeat(active="active", journal_lines=300,
                                  journal_kind="errors")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.alert_count(), 1)
        self.assertIn("completed no work", self.sent())

    def test_healthy_markers_are_what_count_as_alive(self):
        proc = self.run_heartbeat(active="active", journal_lines=1,
                                  journal_kind="healthy")
        self.assertEqual(self.alert_count(), 0)
        self.assertIn("processor healthy", proc.stderr)

    def test_an_unwritable_state_file_does_not_cause_repeat_alerts(self):
        """mkdir -p on an existing directory returns 0 even on a read-only
        filesystem, so probing mkdir let a full disk alert every 5 minutes."""
        readonly = self.tmp / "readonly"
        readonly.mkdir()
        target = readonly / "buttonsbebe-heartbeat.state"
        readonly.chmod(0o500)
        self.addCleanup(readonly.chmod, 0o700)

        proc = self.run_heartbeat(active="failed", state_file=target)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("not writable", proc.stderr)
        self.assertFalse(target.exists())

    def test_a_state_path_that_is_not_a_state_file_is_refused(self):
        victim = self.tmp / "precious.conf"
        victim.write_text("do not truncate me", encoding="utf-8")
        proc = self.run_heartbeat(active="active", state_file=victim)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("refusing", proc.stderr)
        self.assertEqual(victim.read_text(encoding="utf-8"), "do not truncate me")

    def test_missing_systemctl_exits_zero(self):
        """A non-systemd host must be a no-op, not an error."""
        import shutil

        # Build a PATH that deliberately contains no systemctl at all, while
        # still offering the handful of coreutils the script touches before
        # the systemctl check.
        sandbox = self.tmp / "nosystemd"
        sandbox.mkdir()
        for tool in ("date", "dirname", "mkdir", "rm"):
            real = shutil.which(tool)
            if real:
                (sandbox / tool).symlink_to(real)
        (self.bin / "systemctl").unlink()

        env = dict(os.environ)
        env["PATH"] = f"{self.bin}:{sandbox}"
        env["HEARTBEAT_STATE_FILE"] = str(self.state_file)
        env["CURL_LOG"] = str(self.curl_log)
        bash = shutil.which("bash") or "/bin/bash"
        proc = subprocess.run([bash, str(SCRIPT)], env=env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.alert_count(), 0)
        self.assertIn("systemctl not available", proc.stderr)


class MarkerContractTests(unittest.TestCase):
    """Pin the journal marker strings producer to consumers.

    orchestrator.py emits "Processor idle heartbeat" / "Job completed";
    heartbeat.sh and tools/ops/monitor.py grep the journal for them (and the
    heartbeat tests stub journalctl with fixed strings, so a reworded marker
    in the orchestrator would pass every test while both watchdog readers go
    silently blind). A source-shape check is the only offline guard for that.
    """

    def test_orchestrator_marker_strings_are_the_ones_watchdogs_grep_for(self) -> None:
        processor_dir = Path(__file__).resolve().parent
        orchestrator = (processor_dir / "orchestrator.py").read_text(encoding="utf-8")
        heartbeat = (processor_dir / "heartbeat.sh").read_text(encoding="utf-8")
        monitor = (processor_dir.parent / "tools" / "ops" / "monitor.py").read_text(encoding="utf-8")

        for marker in ("Processor idle heartbeat", "Job completed"):
            with self.subTest(marker=marker):
                self.assertIn(f'"{marker}"', orchestrator)
                self.assertIn(marker, heartbeat)
                self.assertIn(marker, monitor)


if __name__ == "__main__":
    unittest.main()
