"""Run with python3 -m intake; no installation or production environment needed."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from . import attachments, import_store, recovery, replay, auth, assistance, channel_adapter, intake_jobs
from .integrity import check_database
from .imports import MAX_BYTES
from .policy import Invalid, install_offline_guard, workspace_directory
from .server import serve
from .store import Store


def main():
    parser = argparse.ArgumentParser(description="Offline ticket intake. Never connects to providers.")
    parser.add_argument("--workspace", default="default", help="Private sandbox name; no arbitrary database paths")
    commands = parser.add_subparsers(dest="command", required=True)
    server = commands.add_parser("serve", help="Loopback-only review UI")
    server.add_argument("--port", type=int, default=8891)
    for name in ("preview", "import"):
        command = commands.add_parser(name, help="Read a supplied local Gorgias JSON export")
        command.add_argument("file", type=Path)
        command.add_argument("--account", required=True, help="Stable source-account namespace")
        if name == "import":
            command.add_argument("--expected-digest", required=True, help="Digest from the preview report")
    commands.add_parser("report", help="List persisted import reports (no message bodies)")
    for name in ("replay-preview", "replay"):
        command = commands.add_parser(name, help="Simulate inbound messages from an explicitly offline local file")
        command.add_argument("file", type=Path)
        if name == "replay":
            command.add_argument("--expected-digest", required=True)
    commands.add_parser('attachment-index', help='Write a private attachment mapping index')
    for name in ('attachment-preview', 'attachment-import'):
        command = commands.add_parser(name, help='Read an explicit local attachment bundle')
        command.add_argument('bundle', type=Path)
        if name == 'attachment-import':
            command.add_argument('--expected-digest', required=True)
    command = commands.add_parser('attachment-copy', help='Extract private bytes locally without opening them')
    command.add_argument('--attachment-id', required=True)
    command.add_argument('--name', required=True, help='New plain filename inside this workspace copies directory')
    for name in ('backup', 'backup-verify'):
        command = commands.add_parser(name, help='Create or verify a private offline snapshot')
        command.add_argument('--name', required=True)
    command = commands.add_parser('restore', help='Restore a verified backup to a new workspace only')
    command.add_argument('--from-workspace', required=True)
    command.add_argument('--backup', required=True)
    command.add_argument('--expected-digest', required=True)
    commands.add_parser('verify-workspace', help='Read-only database, file and delivery-ledger integrity checks')
    command = commands.add_parser('user-set', help='Create/edit a local account and revoke its sessions')
    command.add_argument('--username', required=True)
    command.add_argument('--name', required=True)
    command.add_argument('--role', required=True, choices=sorted(auth.ROLES))
    command.add_argument('--disable', action='store_true')
    command.add_argument('--keep-password', action='store_true')
    command.add_argument('--password-file', type=Path, help='Private regular file, no group/other permissions')
    command = commands.add_parser('assistance-input', help='Write a private native-ID assistance input; no provider calls')
    command.add_argument('--ticket-id', required=True)
    for name in ('assistance-preview','assistance-import'):
        command = commands.add_parser(name, help='Read an explicit local assistance fixture')
        command.add_argument('file', type=Path)
        if name == 'assistance-import':
            command.add_argument('--expected-digest', required=True)
    command = commands.add_parser('channel-create', help='Register a local fake channel namespace')
    command.add_argument('--account', required=True)
    command.add_argument('--mailbox', required=True)
    command.add_argument('--provider', required=True, help='Source namespace only; never selects a transport')
    command = commands.add_parser('channel-key', help='Generate a workspace-only fake signing key; secret is never printed')
    command.add_argument('--channel-id', required=True)
    command.add_argument('--revoke-previous', action='store_true')
    command = commands.add_parser('channel-fixture', help='Sign a local replay or receipt file with a generated fake key')
    command.add_argument('file', type=Path)
    command.add_argument('--key-id', required=True)
    command.add_argument('--purpose', choices=('inbound','receipt'), default='inbound')
    command.add_argument('--name', required=True, help='New private output filename inside this workspace')
    for name in ('channel-preview','channel-enqueue'):
        command = commands.add_parser(name, help='Authenticate a local signed fixture; never receives live events')
        command.add_argument('file', type=Path)
        if name == 'channel-enqueue':
            command.add_argument('--expected-digest', required=True)
    command = commands.add_parser('jobs-run', help='Process a bounded number of local queued fake jobs, then stop')
    command.add_argument('--limit', type=int, default=100)
    commands.add_parser('jobs-health', help='Read redacted queue and delivery monitoring counts')
    command = commands.add_parser('jobs-resume', help='Inspect the restore hold; supply its digest to resume fake jobs')
    command.add_argument('--expected-digest')
    command = commands.add_parser('jobs-retry', help='Inspect one dead job; supply digest and reason to retry locally')
    command.add_argument('--job-id', required=True)
    command.add_argument('--expected-digest')
    command.add_argument('--reason', default='')
    args = parser.parse_args()
    install_offline_guard()
    # Protect SQLite files/journals and future local import artifacts by default.
    import os
    os.umask(0o077)
    try:
        if args.command in ('restore', 'backup-verify', 'verify-workspace'):
            if args.command == 'restore':
                result = recovery.restore(recovery.backup_path(args.from_workspace, args.backup), args.workspace, args.expected_digest)
            elif args.command == 'backup-verify':
                result = recovery.verify(recovery.backup_path(args.workspace, args.name))
            else:
                result = check_database(recovery.workspace_path(args.workspace) / 'intake.sqlite3')
            print(json.dumps(result, indent=2))
            return 0
        store = Store(workspace_directory(args.workspace))
        if args.command.startswith(('channel-','jobs-')):
            from .private_files import read_bytes, filename, private_write
            if args.command == 'channel-create':
                result = {'channel_id': channel_adapter.create_channel(store,args.account,args.mailbox,args.provider)}
            elif args.command == 'channel-key':
                result = {'key_id': channel_adapter.rotate_key(store,args.channel_id,revoke_previous=args.revoke_previous)}
            elif args.command == 'channel-fixture':
                value = channel_adapter.make_fixture(store,args.key_id,read_bytes(args.file,MAX_BYTES),args.purpose)
                destination = store.path.parent / filename(args.name)
                private_write(destination,value)
                result = {'file': str(destination), 'outboundActions': 0}
            elif args.command in ('channel-preview','channel-enqueue'):
                result = channel_adapter.enqueue(store,read_bytes(args.file,channel_adapter.MAX_FRAME),
                    getattr(args,'expected_digest',None),preview=args.command=='channel-preview')
            elif args.command == 'jobs-run':
                result = intake_jobs.run(store,args.limit)
            elif args.command == 'jobs-health':
                result = intake_jobs.health(store)
            elif args.command == 'jobs-resume':
                result = intake_jobs.resume(store,args.expected_digest)
            else:
                result = intake_jobs.retry_dead(store,args.job_id,args.expected_digest,args.reason)
            print(json.dumps(result,indent=2))
        elif args.command.startswith('assistance-'):
            if args.command == 'assistance-input':
                result = assistance.export_input(store,args.ticket_id)
            else:
                from .private_files import read_bytes
                from .assistance_contract import MAX_BYTES as MAX_FIXTURE_BYTES
                payload = read_bytes(args.file,MAX_FIXTURE_BYTES)
                result = assistance.import_fixture(store,payload,getattr(args,'expected_digest',None),preview=args.command=='assistance-preview')
            print(json.dumps(result,indent=2))
        elif args.command == 'user-set':
            if args.keep_password and args.password_file:
                raise Invalid('Choose a password file or keep the current password.')
            password = None
            if not args.keep_password:
                if args.password_file:
                    from .private_files import read_bytes
                    raw = read_bytes(args.password_file, 4096)
                    if args.password_file.stat().st_mode & 0o077:
                        raise Invalid('Password file must be private (mode 0600).')
                    password = raw.decode('utf8').rstrip('\r\n')
                else:
                    import getpass
                    if not sys.stdin.isatty():
                        raise Invalid('Use a terminal password prompt or a private --password-file.')
                    password = getpass.getpass('New sandbox-only password: ')
                    if password != getpass.getpass('Repeat password: '):
                        raise Invalid('Passwords did not match.')
            result = auth.provision(store, args.username, args.name, args.role, password, not args.disable)
            print(json.dumps(result))
        elif args.command.startswith('attachment-') or args.command == 'backup':
            if args.command == 'attachment-index':
                result = attachments.inventory(store)
            elif args.command == 'attachment-preview':
                result = attachments.preview(store, args.bundle)
            elif args.command == 'attachment-import':
                result = attachments.apply_import(store, args.bundle, args.expected_digest)
            elif args.command == 'attachment-copy':
                result = attachments.copy_file(store, args.attachment_id, args.name)
            else:
                result = recovery.backup(store, args.name)
            print(json.dumps(result, indent=2))
        elif args.command == "serve":
            if not 1024 <= args.port <= 65535:
                raise Invalid("Use a local port from 1024 to 65535.")
            serve(store, args.workspace, args.port)
        elif args.command == "report":
            print(json.dumps(store.import_history(), indent=2))
        else:
            with args.file.open("rb") as source:
                payload = source.read(MAX_BYTES + 1)
            if args.command.startswith("replay"):
                result = replay.run(store, payload, getattr(args, "expected_digest", None), preview=args.command == "replay-preview")
                # Detailed receipts stay private in SQLite; stdout has counts only.
                result.pop("results")
            else:
                result = (import_store.preview(store, payload, args.account) if args.command == "preview" else
                          import_store.apply_import(store, payload, args.account, args.expected_digest))
            print(json.dumps(result, indent=2))
    except (Invalid, OSError, sqlite3.Error) as exc:
        # File path and content must not leak through OS exception strings.
        print(str(exc) if isinstance(exc, Invalid) else "Cannot access the local sandbox or export file.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
