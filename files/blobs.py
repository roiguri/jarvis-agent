"""File bytes on disk, named by their sha256 under config.FILES_DIR.

Bytes never change once written, so a name is its content. Layout `ab/cd/<sha256>`
keeps directories small; there is no extension (the type lives in the index).
Storage sits behind put / open / exists / delete / hashes so a mounted volume or
object storage can replace this without touching callers.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from typing import Iterator

import config


def root() -> str:
    return config.FILES_DIR


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def path_of(sha: str) -> str:
    return os.path.join(root(), sha[:2], sha[2:4], sha)


def exists(sha: str) -> bool:
    return os.path.isfile(path_of(sha))


def put(data: bytes) -> str:
    """Store `data`, return its hash. Already stored → its mtime is refreshed (the
    collector's age test reads it). A new file is written to a temp file in the same
    directory, fsynced, renamed into place and the directory fsynced, so a crash
    never leaves a half-written file under a real name."""
    sha = digest(data)
    final = path_of(sha)
    try:
        os.utime(final)
        return sha
    except FileNotFoundError:
        pass
    directory = os.path.dirname(final)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, final)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    dfd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return sha


def read(sha: str) -> bytes:
    """The bytes. Raises FileNotFoundError when they are gone."""
    with open(path_of(sha), "rb") as f:
        return f.read()


def size(sha: str) -> int:
    return os.path.getsize(path_of(sha))


def mtime(sha: str) -> float:
    return os.path.getmtime(path_of(sha))


def delete(sha: str) -> None:
    try:
        os.unlink(path_of(sha))
    except FileNotFoundError:
        pass


def hashes() -> Iterator[str]:
    """Every stored hash (temp files skipped)."""
    base = root()
    if not os.path.isdir(base):
        return
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            if len(name) == 64 and not name.startswith("."):
                yield name


def temp_leftovers(older_than_s: float) -> Iterator[str]:
    """Paths of temp files a crash left behind, older than `older_than_s`."""
    base = root()
    if not os.path.isdir(base):
        return
    cutoff = time.time() - older_than_s
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            p = os.path.join(dirpath, name)
            if name.startswith(".tmp-") and os.path.getmtime(p) < cutoff:
                yield p
