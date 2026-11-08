"""Write suspension uses real process locks and disposable roots only."""

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from edge.application_storage_lock import (
    LOCK_FILENAME,
    StorageLockError,
    check_local_path,
    storage_write_lock,
)


def test_reentrant_and_exception_releases(tmp_path):
    with pytest.raises(RuntimeError), storage_write_lock(tmp_path), \
            storage_write_lock(tmp_path, timeout_seconds=0):
        raise RuntimeError("test")
    with storage_write_lock(tmp_path, timeout_seconds=0):
        assert (tmp_path / LOCK_FILENAME).is_file()


def test_other_thread_times_out(tmp_path):
    errors = []

    def worker():
        try:
            with storage_write_lock(tmp_path, timeout_seconds=0.05):
                errors.append("unexpected acquired")
        except StorageLockError:
            errors.append("busy")

    with storage_write_lock(tmp_path):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert errors == ["busy"]


def test_other_process_times_out_then_acquires(tmp_path):
    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from edge.application_storage_lock import storage_write_lock,StorageLockError\n"
        "try:\n"
        " with storage_write_lock(Path(sys.argv[1]),timeout_seconds=0.05): pass\n"
        "except StorageLockError: sys.exit(17)\n"
    )
    with storage_write_lock(tmp_path):
        result = subprocess.run(
            [sys.executable, "-B", "-c", script, str(tmp_path)],
            capture_output=True, timeout=10, check=False,
        )
    assert result.returncode == 17, result.stderr
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, str(tmp_path)],
        capture_output=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("timeout", [-1, 61, True, "1", float("nan")])
def test_invalid_timeout(tmp_path, timeout):
    with pytest.raises(StorageLockError), \
            storage_write_lock(tmp_path, timeout_seconds=timeout):
        pass


def test_root_must_exist(tmp_path):
    with pytest.raises(StorageLockError), storage_write_lock(tmp_path / "missing"):
        pass
    assert not (tmp_path / "missing").exists()


def test_reparse_ancestor_rejected_without_resolve(tmp_path, monkeypatch):
    original = Path.lstat

    class Junction:
        st_mode = 0o40755
        st_file_attributes = 0x400

    def lstat(path):
        return Junction() if path == tmp_path else original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(StorageLockError):
        check_local_path(tmp_path / "child")


def test_symlink_path_rejected(tmp_path):
    link = tmp_path / "link"
    try:
        os.symlink(tmp_path, link, target_is_directory=True)
    except OSError:
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(StorageLockError):
        check_local_path(link / "child")
