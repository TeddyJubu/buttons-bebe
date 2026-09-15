#!/usr/bin/env python3
"""Journaled file-level code deployment. Never a whole-tree/data rollback.

All callers must hold the receiver's host flock. Files switch atomically, but a
release spans multiple files: affected services must be stopped during apply.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import tempfile

COMPONENTS = {
    'feedback': ('feedback', ['buttonsbebe-webhook', 'buttonsbebe-processor', 'buttonsbebe-kb-mcp']),
    'webhook': ('webhook', ['buttonsbebe-webhook', 'buttonsbebe-processor']),
    'processor': ('processor', ['buttonsbebe-webhook', 'buttonsbebe-processor']),
    'tools': ('tools', ['buttonsbebe-gorgias-mcp', 'buttonsbebe-redo-mcp', 'buttonsbebe-processor']),
    'kb': ('KB', ['buttonsbebe-kb-mcp', 'buttonsbebe-processor']),
    'kb-admin': ('kb-admin', ['buttonsbebe-kb-admin']),
    'whatsapp-connect': ('whatsapp-connect', ['buttonsbebe-whatsapp-connect']),
    'console-src/inbox': ('console-src/inbox', ['helpdesk-inbox']),
    'console-src/helpdesk-agent': ('console-src/helpdesk-agent', ['helpdesk-inbox']),
}
REQUIRED_FILES = {
    'webhook': ('src/bb_webhook/app.py', 'pyproject.toml', 'uv.lock'),
    'processor': ('orchestrator.py', 'hermes_runner/process.py', 'pyproject.toml', 'uv.lock'),
    'feedback': ('__init__.py', 'pii.py'),
    'tools': ('run-gorgias.sh', 'run-redo.sh', 'requirements.txt', 'requirements.lock', 'runtime-constraints.txt'),
    'kb': ('scripts/index_kb.py', 'sync-products.sh', 'requirements.txt', 'requirements.lock', 'runtime-constraints.txt'),
    'kb-admin': ('server.js',),  # Node builtins only; no package manifest exists.
    'whatsapp-connect': ('server.js', 'package.json', 'package-lock.json'),
    'console-src/inbox': ('run-review.sh', 'index.html', 'requirements.txt', 'requirements.lock', 'projection.py', 'export_projection.py', 'shop_rail.py', 'export_shop_rail.py'),
    'console-src/helpdesk-agent': ('helpdesk/dispatch.py', 'helpdesk/send_access.py'),
}

EXCLUDED = {'.venv', 'venv', 'node_modules', '__pycache__', 'data', 'logs', 'auth',
            '.wwebjs_auth', '.wwebjs_cache', '.git', '.pytest_cache', 'lancedb',
            'products', 'learned', 'notices', 'archive', '_archive_learned'}
KB_CONTENT = {'intents', 'faq', 'policies', 'tickets', 'shopify'}
DEPENDENCIES = {'pyproject.toml', 'uv.lock', 'requirements.txt', 'package.json', 'package-lock.json', 'requirements.lock', 'runtime-constraints.txt'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def safe_path(root, relative):
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts or not part.parts:
        raise ValueError('unsafe release path')
    path = root / part
    for parent in [path, *path.parents]:
        if parent == root.parent:
            break
        if parent.is_symlink():
            raise ValueError(f'symlink in deployment path: {relative}')
    if path.exists() and not path.is_file():
        raise ValueError(f'deployment target is not a regular file: {relative}')
    return path


def atomic_copy(source, destination, mode=None, directory_mode=0o755):
    # Deployment may run with umask077. Newly created public code directories
    # still need traversal by the dedicated runtime account; never chmod an
    # existing directory, and keep recovery/state directories private.
    missing = []
    parent = destination.parent
    while not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=directory_mode)
        directory.chmod(directory_mode)
    fd, temporary = tempfile.mkstemp(prefix='.deploy-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output, source.open('rb') as incoming:
            shutil.copyfileobj(incoming, output)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode if mode is not None else source.stat().st_mode & 0o777)
        os.replace(temporary, destination)
        fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(value, destination):
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'state.json'
        source.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
        atomic_copy(source, destination, 0o600, directory_mode=0o700)


def inventory(release):
    result = {}
    for component, (target, services) in COMPONENTS.items():
        directory = release / component
        if not directory.is_dir():
            raise ValueError(f'missing required release component: {component}')
        for required in REQUIRED_FILES[component]:
            if not (directory / required).is_file():
                raise ValueError(f'missing required release file: {component}/{required}')
        for path in directory.rglob('*'):
            relative = path.relative_to(directory)
            if EXCLUDED.intersection(relative.parts) or any(p.startswith('.env') for p in relative.parts):
                continue
            if component == 'kb' and relative.parts[0] in KB_CONTENT:
                continue  # All KB editor/generated corpora are data, not code.
            if path.suffix in {'.db', '.sqlite', '.sqlite3', '.pyc'} or path.name.endswith(('-wal', '-shm')):
                continue
            if path.is_symlink():
                raise ValueError('symlink in release')
            if path.is_file():
                key = ('inbox/' if component.startswith('console-src/') else 'app/') + str(Path(target) / relative)
                result[key] = {'source': str(path.relative_to(release)), 'sha256': digest(path),
                               'component': component, 'services': services,
                               'mode': 0o755 if path.suffix == '.sh' else 0o644}
    for name in ('index.html', 'login.html'):
        path = release / 'console-src' / name
        if not path.is_file():
            raise ValueError(f'missing console asset: {name}')
        result['web/' + name] = {'source': str(path.relative_to(release)), 'sha256': digest(path),
                                'component': 'console', 'services': [], 'mode': 0o644}
    return result


def target_path(key, live, web, inbox=None):
    prefix, relative = key.split('/', 1)
    if prefix not in {'app', 'web', 'inbox'}:
        raise ValueError('unknown deployment root')
    return safe_path({'app': live, 'web': web, 'inbox': inbox or live}[prefix], relative)


def prepare(release, live, web, journal_dir, state, inbox=None):
    new = inventory(release)
    metadata = json.loads((release / '.buttonsbebe-release.json').read_text())
    if type(metadata.get('generation')) is not int or metadata['generation'] < 1:
        raise ValueError('invalid verified workflow generation')
    if not re.fullmatch('[0-9a-f]{40}', metadata.get('commit', '')):
        raise ValueError('invalid verified commit')
    old = json.loads(state.read_text()) if state.exists() else {'files': {}}
    if metadata['generation'] < old.get('generation', 0):
        raise ValueError('stale release generation')
    if metadata['generation'] == old.get('generation') and metadata.get('commit') != old.get('commit'):
        raise ValueError('generation belongs to another commit')
    changes = []
    # Only remove files owned by a previous successful manifest. Unknown runtime
    # files are never deleted, including on the first adoption deployment.
    for key in sorted(new.keys() | old['files'].keys()):
        incoming = new.get(key)
        previous = old['files'].get(key)
        target = target_path(key, live, web, inbox)
        current = digest(target)
        if incoming and current == incoming['sha256']:
            continue
        if not incoming and current is None:
            continue
        if previous and current != previous['sha256']:
            raise ValueError(f'live code drift requires review: {key}')
        if Path((incoming or previous)['source']).name in DEPENDENCIES:
            raise ValueError(f'dependency preparation required before deploy: {key}; do not mutate live environments in CD')
        changes.append({'key': key, 'before': current,
                        'after': incoming['sha256'] if incoming else None,
                        'entry': incoming or previous})
    journal_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    for change in changes:
        if change['before'] is not None:
            atomic_copy(target_path(change['key'], live, web, inbox), journal_dir / 'files' / change['key'], directory_mode=0o700)
    result = {'files': new, 'changes': changes, 'release': str(release), 'live': str(live), 'web': str(web), 'inbox': str(inbox or live), 'generation': metadata['generation'], 'commit': metadata['commit']}
    atomic_json(result, journal_dir / 'journal.json')
    return result


def apply(journal_dir, rollback=False):
    journal = json.loads((journal_dir / 'journal.json').read_text())
    live, web = Path(journal['live']), Path(journal['web'])
    inbox = Path(journal['inbox'])
    changes = list(reversed(journal['changes'])) if rollback else journal['changes']
    # Check every file before touching any, including retained runtime edits.
    for change in changes:
        current = digest(target_path(change['key'], live, web, inbox))
        allowed = {change['before'], change['after']} if rollback else {change['before']}
        if current not in allowed:
            raise ValueError(f'concurrent code edit; refusing overwrite: {change["key"]}')
    for change in changes:
        target = target_path(change['key'], live, web, inbox)
        wanted = change['before'] if rollback else change['after']
        if digest(target) == wanted:
            continue
        if wanted is None:
            target.unlink(missing_ok=True)
        else:
            source = journal_dir / 'files' / change['key'] if rollback else Path(journal['release']) / change['entry']['source']
            if digest(source) != wanted:
                raise ValueError('backup or staged artifact checksum changed')
            atomic_copy(source, target, None if rollback else change['entry']['mode'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply', 'rollback', 'services', 'commit'])
    parser.add_argument('--release', type=Path)
    parser.add_argument('--live', type=Path, default=Path('/root/Buttonsbebe Agent'))
    parser.add_argument('--web', type=Path, default=Path('/var/www/console'))
    parser.add_argument('--inbox', type=Path, default=Path('/opt/buttonsbebe/inbox'))
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--state', type=Path, default=Path('/var/lib/buttonsbebe-deploy/source-manifest.json'))
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.release, args.live, args.web, args.journal, args.state, args.inbox)
    elif args.action in {'apply', 'rollback'}:
        apply(args.journal, args.action == 'rollback')
    else:
        journal = json.loads((args.journal / 'journal.json').read_text())
        if args.action == 'services':
            affected = {s for c in journal['changes'] for s in c['entry']['services']}
            priority = {'buttonsbebe-webhook': 0, 'buttonsbebe-processor': 99}
            print('\n'.join(sorted(affected, key=lambda service: (priority.get(service, 50), service))))
        else:
            for key, entry in journal['files'].items():
                target = target_path(key, Path(journal['live']), Path(journal['web']), Path(journal['inbox']))
                if digest(target) != entry['sha256']:
                    raise ValueError(f'installed source changed before commit: {key}')
            atomic_json({'files': journal['files'], 'release': journal['release'], 'generation': journal['generation'], 'commit': journal['commit']}, args.state)


if __name__ == '__main__':
    main()
