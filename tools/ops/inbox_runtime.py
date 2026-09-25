#!/usr/bin/env python3
"""Reviewed, root-only isolated inbox runtime installation. Never restores data.

Prepare is offline except the explicitly requested venv dependency installation.
Apply and rollback are separate operator actions. All subprocess output is
captured so credentials in external configuration/errors cannot leak to stdout.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RUNTIME = Path('/opt/buttonsbebe/inbox')
STATE = Path('/var/lib/buttonsbebe-inbox')
UNIT = Path('/etc/systemd/system/helpdesk-inbox.service')
BACKUPS = Path('/opt/buttonsbebe/backups')


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # Do not print stderr/argv: package tools and live config can contain secrets.
        raise RuntimeError(f'{Path(args[0]).name} failed with exit status {result.returncode}')
    return result.stdout


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_source(source: Path) -> None:
    inbox = source / 'console-src/inbox'
    required = ['review_server.py', 'static-manifest.json', 'requirements.txt', 'requirements.lock']
    if any(not (inbox / name).is_file() for name in required):
        raise ValueError('Source is not the reviewed ASGI inbox release')
    if 'from fastapi import' not in (inbox / 'review_server.py').read_text():
        raise ValueError('Development review server is not eligible')
    lock = source / 'console-src/helpdesk-agent/helpdesk/send_access.py'
    if 'SEND_ACCESS_ENABLED = False' not in lock.read_text():
        raise ValueError('Hardcoded Send lock missing')
    for tree in ('inbox', 'helpdesk-agent'):
        for path in (source / 'console-src' / tree).rglob('*'):
            if path.is_symlink():
                raise ValueError('Source trees must not contain symlinks')
            if path.name.startswith('.env') or path.suffix in ('.db', '.sqlite', '.sqlite3'):
                raise ValueError('Source trees must not contain credential or state files')


def owned_directory(path: Path) -> None:
    if path.is_symlink() or not path.is_dir() or path.stat().st_uid != 0:
        raise ValueError('Expected a root-owned real directory')
    if path.stat().st_mode & 0o022:
        raise ValueError('Directory must not be writable by group or other')


def prepare(source: Path, stage: Path) -> None:
    validate_source(source)
    if stage.parent != RUNTIME.parent or not stage.name.startswith('inbox-stage-') or stage.exists():
        raise ValueError('Use a new /opt/buttonsbebe/inbox-stage-NAME directory')
    stage.mkdir(mode=0o755)
    # mkdir's mode is masked by the root-only operations umask. The runtime
    # identity needs traversal of the candidate itself as well as its children.
    stage.chmod(0o755)
    try:
        for tree in ('inbox', 'helpdesk-agent'):
            shutil.copytree(source / 'console-src' / tree, stage / 'console-src' / tree,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.pytest_cache'))
        run('/usr/bin/python3', '-m', 'venv', str(stage / 'venv'))
        python = str(stage / 'venv/bin/python')
        run(python, '-m', 'pip', 'install', '--disable-pip-version-check', '--require-hashes', '-r',
            str(stage / 'console-src/inbox/requirements.lock'))
        run(python, '-c', 'import fastapi, uvicorn; assert fastapi.__version__ == "0.139.0"; assert uvicorn.__version__ == "0.50.2"')
        # Preserve executable package scripts while making all source read-only
        # to the service identity. Never chown code or its venv to bb-inbox.
        for path in stage.rglob('*'):
            if not path.is_symlink():
                path.chmod(0o755 if path.is_dir() or path.stat().st_mode & 0o111 else 0o644)
        receipt = {'source_files': {str(p.relative_to(stage)): digest(p)
                   for p in (stage / 'console-src').rglob('*') if p.is_file()},
                   'requirements': digest(stage / 'console-src/inbox/requirements.lock'),
                   'dependencies': run(python, '-m', 'pip', 'freeze').splitlines()}
        (stage / 'prepared.json').write_text(json.dumps(receipt, sort_keys=True, indent=2) + '\n')
        print('Prepared isolated inbox source and venv; service unchanged')
    except BaseException:
        # Failed candidates are retained for root inspection, never applied.
        print('Preparation failed; incomplete stage retained and service unchanged', file=sys.stderr)
        raise


def require_traversal(path: Path) -> None:
    for parent in (path, *path.parents):
        if not parent.stat().st_mode & 0o001:
            raise ValueError('Runtime account cannot traverse a code parent')


def verify_stage(stage: Path) -> None:
    owned_directory(stage)
    require_traversal(stage)
    if stage.parent != RUNTIME.parent or not stage.name.startswith('inbox-stage-'):
        raise ValueError('Invalid stage location')
    receipt = json.loads((stage / 'prepared.json').read_text())
    actual_files = {str(p.relative_to(stage)) for p in (stage / 'console-src').rglob('*') if p.is_file()}
    if actual_files != set(receipt['source_files']):
        raise ValueError('Prepared source inventory changed')
    for name, expected in receipt['source_files'].items():
        path = stage / name
        if not path.resolve().is_relative_to(stage.resolve()) or path.is_symlink() or digest(path) != expected:
            raise ValueError('Prepared source receipt mismatch')
    validate_source(stage)
    python = stage / 'venv/bin/python'
    if not python.is_file():
        raise ValueError('Prepared Python runtime missing')
    if receipt['requirements'] != digest(stage / 'console-src/inbox/requirements.lock'):
        raise ValueError('Dependency lock receipt mismatch')
    installed = run(str(python), '-m', 'pip', 'freeze').splitlines()
    if sorted(installed) != sorted(receipt['dependencies']):
        raise ValueError('Prepared installed dependencies changed')
    run(str(python), '-m', 'pip', 'check')


def ensure_identity() -> None:
    try:
        account = pwd.getpwnam('bb-inbox')
        if account.pw_dir not in ('/nonexistent', str(STATE)) or account.pw_shell not in ('/usr/sbin/nologin', '/sbin/nologin'):
            raise ValueError('Existing bb-inbox identity does not match the reviewed account')
    except KeyError:
        run('useradd', '--system', '--user-group', '--home-dir', '/nonexistent',
            '--shell', '/usr/sbin/nologin', 'bb-inbox')
        account = pwd.getpwnam('bb-inbox')
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if STATE.is_symlink():
        raise ValueError('State directory must not be a symlink')
    # State migration is an explicit separate step. Preserve every file and never
    # truncate/reinitialize an existing database to make startup succeed.
    for path in [STATE, *STATE.iterdir()]:
        if path.is_symlink() or (path != STATE and not path.is_file()):
            raise ValueError('Unexpected state directory entry')
        os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700 if path == STATE else 0o600)


def probe(*, require_ready: bool = True) -> None:
    if require_ready:
        with urllib.request.urlopen('http://127.0.0.1:8766/ready', timeout=5) as response:
            status = json.load(response)
        if status.get('ok') is not True or status.get('sendAccessEnabled') is not False:
            raise RuntimeError('Inbox storage readiness failed')
    data = json.dumps({'tool': 'helpdesk.send_reply', 'arguments': {
        'ticketId': 't-ada-track', 'text': 'Hi', 'confirmed': True}}).encode()
    req = urllib.request.Request('http://127.0.0.1:8766/console/api/helpdesk', data=data,
                                 headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        body = json.load(response)
    if not (body.get('ok') is False and body.get('error') == 'send_access_inactive'
            and body.get('message') == 'Activate the send access.'):
        raise RuntimeError('Send lock probe failed')
    try:
        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8766/webhook/gorgias', data=b'{}'), timeout=5)
    except urllib.error.HTTPError as error:
        if error.code == 503:
            return
    raise RuntimeError('Inbox webhook is not locked')


def wait_probe(*, require_ready: bool = True) -> None:
    for attempt in range(15):
        try:
            probe(require_ready=require_ready)
            return
        except (OSError, ValueError):
            if attempt == 14:
                raise
            time.sleep(1)


def apply(stage: Path, unit_source: Path, expected_unit: str, empty_or_migrated: bool) -> None:
    if not empty_or_migrated:
        raise ValueError('First verify legacy store absence or migrate it; acknowledge with --state-verified')
    verify_stage(stage)
    if not UNIT.is_file() or digest(UNIT) != expected_unit:
        raise ValueError('Live unit changed since review; refusing apply')
    unit_text = unit_source.read_text()
    if 'User=bb-inbox' not in unit_text or 'ProtectHome=true' not in unit_text:
        raise ValueError('Expected reviewed least-privilege unit')
    if RUNTIME.exists():
        owned_directory(RUNTIME)
    ensure_identity()
    # Point only the validation copy at the prepared binary, since the final
    # runtime path need not exist yet. The installed unit keeps its fixed path.
    with tempfile.TemporaryDirectory(prefix='bb-inbox-unit-') as temp:
        candidate = Path(temp) / 'helpdesk-inbox.service'
        candidate.write_text(unit_text.replace(str(RUNTIME), str(stage)))
        run('systemd-analyze', 'verify', str(candidate))
    backup = BACKUPS / ('inbox-runtime-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()))
    backup.mkdir(mode=0o700, parents=True, exist_ok=False)
    shutil.copy2(UNIT, backup / UNIT.name)
    (backup / 'metadata.json').write_text(json.dumps({'previous_runtime': RUNTIME.exists(),
        'was_active': run('systemctl', 'show', 'helpdesk-inbox.service', '--property=ActiveState', '--value').strip() == 'active',
        'applied_unit_sha256': digest(unit_source), 'previous_unit_sha256': digest(UNIT),
        'applied_source_files': json.loads((stage / 'prepared.json').read_text())['source_files']}))
    run('systemctl', 'stop', 'helpdesk-inbox.service')
    try:
        if RUNTIME.exists():
            owned_directory(RUNTIME)
            RUNTIME.rename(backup / 'runtime')
        stage.rename(RUNTIME)
        shutil.copy2(unit_source, UNIT)
        UNIT.chmod(0o644)
        run('systemctl', 'daemon-reload')
        run('systemctl', 'start', 'helpdesk-inbox.service')
        wait_probe()
        print(json.dumps({'status': 'applied', 'backup': str(backup), 'unit_sha256': digest(UNIT)}))
    except BaseException:
        rollback(backup, require_current=False)
        raise


def rollback(backup: Path, *, require_current: bool = True) -> None:
    if backup.parent != BACKUPS or not backup.name.startswith('inbox-runtime-'):
        raise ValueError('Invalid rollback directory')
    owned_directory(backup)
    metadata = json.loads((backup / 'metadata.json').read_text())
    if (backup / 'rollback-complete').exists():
        if digest(UNIT) != metadata['previous_unit_sha256']:
            raise ValueError('Unit changed since completed rollback')
        print('Rollback already completed; no changes')
        return
    if metadata['previous_runtime'] and not (backup / 'runtime').is_dir():
        raise ValueError('Previous runtime backup missing; refusing rollback')
    if require_current and digest(UNIT) != metadata['applied_unit_sha256']:
        raise ValueError('Live unit changed after apply; refusing stale rollback')
    if require_current:
        for name, expected in metadata['applied_source_files'].items():
            if not (RUNTIME / name).is_file() or digest(RUNTIME / name) != expected:
                raise ValueError('Live source changed after apply; refusing stale rollback')
    run('systemctl', 'stop', 'helpdesk-inbox.service')
    if RUNTIME.exists():
        RUNTIME.rename(backup / ('retained-candidate-' + str(time.time_ns())))
    if metadata['previous_runtime']:
        (backup / 'runtime').rename(RUNTIME)
    shutil.copy2(backup / UNIT.name, UNIT)
    run('systemctl', 'daemon-reload')
    if metadata['was_active']:
        run('systemctl', 'start', 'helpdesk-inbox.service')
        # The pre-hardening legacy server had no /ready endpoint. Its original
        # Send/bridge contract is still required when restoring that version.
        wait_probe(require_ready='User=bb-inbox' in UNIT.read_text())
    (backup / 'rollback-complete').write_text('completed\n')
    print('Rolled back inbox code/unit; persistent data preserved')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    prep = sub.add_parser('prepare'); prep.add_argument('--source', type=Path, required=True); prep.add_argument('--stage', type=Path, required=True)
    app = sub.add_parser('apply'); app.add_argument('--stage', type=Path, required=True); app.add_argument('--unit', type=Path, required=True); app.add_argument('--expected-unit-sha256', required=True); app.add_argument('--state-verified', action='store_true')
    back = sub.add_parser('rollback'); back.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    if args.command != "rollback":
        raise SystemExit("Inbox 1 is retired; use the Inbox release inventory.")
    if os.geteuid() != 0:
        raise SystemExit('Run only on the reviewed Linux VPS as root')
    os.umask(0o077)
    try:
        if args.command == 'prepare': prepare(args.source.resolve(), args.stage)
        else:
            with open('/run/lock/buttonsbebe-deploy.lock', 'a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if args.command == 'apply': apply(args.stage, args.unit, args.expected_unit_sha256, args.state_verified)
                else: rollback(args.backup)
    except Exception as error:
        # Tracebacks from package/config tools may reveal credential-bearing paths.
        raise SystemExit(f'Inbox operation failed: {type(error).__name__}; inspect protected local state') from None


if __name__ == '__main__':
    main()
