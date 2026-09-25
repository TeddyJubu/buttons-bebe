"""Explicit recovery scope and bounded no-symlink file access (stdlib only)."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat

MAX_MEMBERS = 20000
MAX_FILE = 64 * 1024 * 1024
MAX_DATABASE = 512 * 1024 * 1024
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
APP = '/root/Buttonsbebe Agent'
EXACT = {
    APP+'/.env', APP+'/.buttonsbebe-release.json',
    '/root/.config/systemd/user/hermes-gateway.service',
    '/root/.hermes/config.yaml', '/root/.hermes/auth.json', '/root/.hermes/.env', '/root/.hermes/SOUL.md',
    '/etc/buttonsbebe-deploy-approved-config.sha256', '/etc/buttonsbebe-backup-recipient.pem',
    '/var/lib/buttonsbebe-deploy/source-manifest.json',
}
TREES = tuple(APP+'/KB/'+name for name in
              ('policies','faq','intents','products','tickets','learned','_archive_learned','archive_learned','notices','shopify')) + (
    APP+'/whatsapp-connect/auth', '/root/.hermes/skills/buttonsbebe',
    '/opt/buttonsbebe/backups/recovery-input',
)
DATABASES = {APP+'/webhook/data/webhook.db', '/var/lib/buttonsbebe-inbox/inbox.sqlite3'}


def canonical(value):
    if not isinstance(value, str) or not value.startswith('/') or str(PurePosixPath(value)) != value or '..' in PurePosixPath(value).parts:
        raise ValueError('Noncanonical path')
    return value


def allowed(path, kind):
    canonical(path)
    if kind == 'sqlite': return path in DATABASES
    if kind not in {'file','tree','symlink'}: return False
    if kind == 'file' and path in EXACT: return True
    if kind != 'symlink' and any(path == root or path.startswith(root+'/') for root in TREES): return True
    # Caddy imports must be listed individually; no shared-site directory crawl.
    if kind in {'file','symlink'} and path.startswith('/etc/caddy/'): return True
    unit = r'/etc/systemd/system/(?:buttonsbebe-[A-Za-z0-9_-]+|helpdesk-inbox2|hermes(?:-[A-Za-z0-9_-]+)?)(?:\.(?:service|timer))'
    if kind == 'file' and re.fullmatch(unit, path): return True
    if kind == 'file' and re.fullmatch(unit+r'\.d/[A-Za-z0-9_.-]+\.conf', path): return True
    return False


def plan_entries(plan, policy=allowed):
    if not isinstance(plan, dict) or set(plan) != {'schema','release_commit','recipient_sha256','entries'} or type(plan['schema']) is not int or plan['schema'] != 1:
        raise ValueError('Unsupported recovery plan')
    if not re.fullmatch('[0-9a-f]{40}', str(plan['release_commit'])) or not re.fullmatch('[0-9a-f]{64}', str(plan['recipient_sha256'])):
        raise ValueError('Expected commit and independently verified recipient fingerprint')
    entries = plan['entries']
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_MEMBERS: raise ValueError('Invalid plan size')
    paths = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) not in ({'path','kind'}, {'path','kind','target'}): raise ValueError('Invalid entry')
        path, kind = entry['path'], entry['kind']
        if not policy(path, kind) or path in paths: raise ValueError('Unapproved or repeated recovery path')
        if kind == 'symlink':
            if 'target' not in entry or not policy(entry['target'], 'file'): raise ValueError('Unapproved link target')
        elif 'target' in entry: raise ValueError('Unexpected link target')
        paths.add(path)
    for entry in entries:
        if entry['kind'] == 'symlink' and not any(e['path'] == entry['target'] and e['kind'] == 'file' for e in entries):
            raise ValueError('Link target must be captured explicitly')
    return entries


def open_parent(path):
    """Traverse directory descriptors with O_NOFOLLOW, never Path.resolve()."""
    path = canonical(str(path))
    parts = PurePosixPath(path).parts
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in parts[1:-1]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd); fd = child
        return fd, parts[-1]
    except BaseException:
        os.close(fd); raise


def open_regular(path):
    parent, name = open_parent(path)
    try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally: os.close(parent)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd); raise ValueError('Only regular files may be captured')
    return fd


def fingerprint(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_mode, st.st_uid, st.st_gid)


def hash_fd(fd, limit=MAX_FILE):
    os.lseek(fd, 0, os.SEEK_SET)
    digest = hashlib.sha256(); size = 0
    while chunk := os.read(fd, 1024*1024):
        size += len(chunk)
        if size > limit: raise ValueError('File exceeds recovery size limit')
        digest.update(chunk)
    return digest.hexdigest(), size


def private_directory(path):
    parent, name = open_parent(path)
    try: fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally: os.close(parent)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700: raise ValueError('Directory must be owner-private mode0700')
    finally: os.close(fd)


def load_plan(path, policy=allowed):
    fd = open_regular(path)
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or st.st_mode & 0o077 or st.st_size > MAX_MANIFEST: raise ValueError('Plan must be private and bounded')
        with os.fdopen(fd, 'r') as stream: fd = None; plan = json.load(stream)
    finally:
        if fd is not None: os.close(fd)
    plan_entries(plan, policy)
    return plan
