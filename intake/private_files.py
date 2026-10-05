"""Bounded regular-file I/O for private offline artifacts; no links or URLs."""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat

from .policy import Invalid


def regular_directory(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise Invalid('Private file paths cannot traverse symbolic links.')
    if not path.is_dir():
        raise Invalid('Local bundle directory not found.')
    return path


def filename(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}', value):
        raise Invalid('Use a plain local filename without directories, links or URLs.')
    return value


def read_bytes(path, limit):
    regular_directory(Path(path).parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Invalid('Expected a bounded regular local file with no links.')
        data = source.read(limit + 1)
        if len(data) > limit:
            raise Invalid('Local file exceeds the supported size.')
        return data


def json_bytes(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Repeated field')
            result[key] = value
        return result
    def bad_constant(value):
        raise ValueError('Non-finite JSON')
    try:
        return json.loads(data.decode('utf-8-sig'), object_pairs_hook=unique, parse_constant=bad_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Invalid('Invalid local manifest JSON.') from exc


def checksum(data):
    return sha256(data).hexdigest()


def private_write(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def copy_regular(source, destination, limit):
    """Copy with a bounded stream and return its actual digest/length."""
    regular_directory(Path(source).parent)
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as incoming:
        info = os.fstat(incoming.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Invalid('Expected a bounded regular file with no links.')
        out = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        total, digest = 0, sha256()
        with os.fdopen(out, 'wb') as outgoing:
            while chunk := incoming.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise Invalid('Backup exceeds the supported size.')
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
    return {'bytes': total, 'sha256': digest.hexdigest()}


def file_digest(path, limit):
    regular_directory(Path(path).parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    total, digest = 0, sha256()
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
            raise Invalid('Expected a bounded regular file with no links.')
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise Invalid('Backup exceeds the supported size.')
            digest.update(chunk)
    return {'bytes': total, 'sha256': digest.hexdigest()}
