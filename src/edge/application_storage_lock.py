"""Cooperative cross-process write suspension; not an OS/database sandbox.

Every participating writer must hold storage_write_lock(application_root)
before checking state and through commit/publication. Use the SAME root for all
stores: application_root/data and application_root/settings. A backup acquires
this lock across every snapshot. Non-cooperating writers are not suspended.
"""

from __future__ import annotations

import os
import stat
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from edge.errors import EdgeError

LOCK_FILENAME = ".edge-application-write.lock"
_threads: dict[str, threading.RLock] = {}
_guard = threading.Lock()
_local = threading.local()


class StorageLockError(EdgeError):
    """Write suspension could not be acquired safely."""


def check_local_path(path: Path) -> Path:
    """Reject lexical symlink/reparse ancestors before any resolution.

    This does not defend against a hostile filesystem race after the check.
    """
    path = path.absolute()
    for part in (*reversed(path.parents), path):
        try:
            metadata = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise StorageLockError("Redirected storage paths are unsupported.")
    return path


def _try_lock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def storage_write_lock(
    application_root: Path, *, timeout_seconds: float = 10.0
) -> Iterator[None]:
    """Reentrant per thread; exclusive across threads/processes; bounded wait.

    The application root must already exist. The persistent lock file is never
    deleted (deleting a lock inode can split locking across processes). Reads
    need not acquire it. A caller doing multi-store work holds it once throughout.
    """
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (float, int))
        or not 0 <= timeout_seconds <= 60
    ):
        raise StorageLockError("Lock timeout must be between zero and 60 seconds.")
    root = check_local_path(application_root)
    root = Path(os.path.abspath(root))
    if not root.is_dir():
        raise StorageLockError("Application root must already exist.")
    key = os.path.normcase(str(root))
    with _guard:
        thread_lock = _threads.setdefault(key, threading.RLock())
    deadline = time.monotonic() + timeout_seconds
    if not thread_lock.acquire(timeout=timeout_seconds):
        raise StorageLockError("Application storage is busy.")
    fd = None
    acquired = False
    depths = getattr(_local, "depths", None)
    if depths is None:
        depths = _local.depths = {}
    try:
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        lock_path = check_local_path(root / LOCK_FILENAME)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StorageLockError("Invalid storage lock file.")
        # Windows byte-range locks can lock beyond EOF; no contents need writing.
        while True:
            try:
                _try_lock(fd)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StorageLockError("Application storage is busy.") from None
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
    except OSError:
        raise StorageLockError("Application storage lock is unavailable.") from None
    finally:
        if fd is not None:
            try:
                if acquired:
                    _unlock(fd)
            finally:
                os.close(fd)
        thread_lock.release()
