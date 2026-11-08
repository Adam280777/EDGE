"""Real backup/legacy writer interleaving, using temporary application roots only."""

from __future__ import annotations

import multiprocessing
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import edge.application_backup as backup
import edge.journal_store as journal_module
import edge.provider_settings as settings_module
import edge.watchlist_store as watchlist_module
from edge.application_storage_lock import StorageLockError, storage_write_lock
from edge.journal import JournalDraft, JournalStorageError
from edge.journal_store import JournalStore
from edge.provider_settings import (
    ProviderSettings,
    SettingsError,
    load_settings,
    save_settings,
)
from edge.watchlist import WatchlistDraft, WatchlistStorageError
from edge.watchlist_store import WatchlistStore

KINDS = ("journal", "watchlist", "settings")


def _write(root: Path, kind: str, label: str = "new") -> None:
    data_root = root / "data"
    if kind == "journal":
        JournalStore(data_root).create(JournalDraft(
            "swing", "TEST", "Declared", label, date(2020, 1, 1),
            "Manual thesis", "", "", "", "",
        ))
    elif kind == "watchlist":
        WatchlistStore(data_root).create(WatchlistDraft("TEST", "Declared", label))
    else:
        save_settings(data_root, replace(
            ProviderSettings(), timeout_seconds=2 if label == "new" else 3,
        ))


def _process_write(root: str, kind: str, attempted, finished, results) -> None:
    """Spawn-safe process verifies lock exclusion before invoking the legacy API."""
    try:
        try:
            with storage_write_lock(Path(root), timeout_seconds=0):
                results.put("unexpected acquisition")
                return
        except StorageLockError:
            pass
        attempted.set()
        _write(Path(root), kind)
        results.put("written")
    except (
        JournalStorageError, WatchlistStorageError, SettingsError,
        StorageLockError, OSError, ValueError,
    ) as error:
        results.put(type(error).__name__)
    finally:
        finished.set()


@pytest.fixture
def application(tmp_path):
    root = tmp_path / "application"
    root.mkdir()
    for kind in KINDS:
        _write(root, kind, "old")
    return root


def _assert_contents(root: Path, kind: str, *, after: bool) -> None:
    if kind == "journal":
        assert len(JournalStore(root / "data").list_entries()) == (2 if after else 1)
    elif kind == "watchlist":
        assert len(WatchlistStore(root / "data").list_entries()) == (2 if after else 1)
    else:
        assert load_settings(root / "data").timeout_seconds == (2 if after else 3)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("process", [False, True], ids=["thread", "process"])
def test_backup_blocks_old_writers_from_first_snapshot_through_settings(
    application, tmp_path, monkeypatch, kind, process,
):
    """Writer cannot enter during the gap BETWEEN SQLite snapshots and settings."""
    settings_paused = threading.Event()
    release_backup = threading.Event()
    original_settings = backup._settings

    def pause_settings(contents):
        settings_paused.set()
        assert release_backup.wait(15)
        return original_settings(contents)

    monkeypatch.setattr(backup, "_settings", pause_settings)
    archive = tmp_path / "bundle.zip"
    context = multiprocessing.get_context("spawn")
    attempted = context.Event() if process else threading.Event()
    finished = context.Event() if process else threading.Event()
    results = context.Queue() if process else None
    worker = None
    with ThreadPoolExecutor(max_workers=2) as executor:
        backup_future = executor.submit(backup.backup_application, application, archive)
        try:
            assert settings_paused.wait(10)
            if process:
                worker = context.Process(
                    target=_process_write,
                    args=(str(application), kind, attempted, finished, results),
                )
                worker.start()
            else:
                def thread_write():
                    attempted.set()
                    try:
                        _write(application, kind)
                    finally:
                        finished.set()
                writer_future = executor.submit(thread_write)
            assert attempted.wait(10)
            assert not finished.wait(0.2)
        finally:
            release_backup.set()
        manifest = backup_future.result(timeout=15)
        if process:
            worker.join(15)
            try:
                assert not worker.is_alive()
                assert worker.exitcode == 0
                assert results.get(timeout=5) == "written"
            finally:
                if worker.is_alive():
                    worker.terminate()
                    worker.join(5)
                results.close()
                results.join_thread()
        else:
            writer_future.result(timeout=15)
    assert finished.is_set()
    _assert_contents(application, kind, after=True)
    restored = tmp_path / "restored"
    backup.restore_application(archive, restored)
    _assert_contents(restored, kind, after=False)
    # The default registry claim is only a cooperative locked cut, not verified
    # identity, market data, authenticity, external-writer or production safety.
    assert manifest["consistency"] == "cooperative_locked_cut_registered_user_stores"
    assert manifest["cross_store_consistency_verified"] is True


@pytest.mark.parametrize("kind", KINDS)
def test_old_writer_blocks_backup_until_publication_complete(
    application, tmp_path, monkeypatch, kind,
):
    """Pause after atomic publication while writer lock is still held."""
    module = {
        "journal": journal_module, "watchlist": watchlist_module,
        "settings": settings_module,
    }[kind]
    writer_paused = threading.Event()
    release_writer = threading.Event()
    snapshots_started = threading.Event()
    original_lock = module.storage_write_lock
    original_snapshot = backup._snapshot

    @contextmanager
    def pause_after_write(root):
        with original_lock(root):
            yield
            writer_paused.set()
            assert release_writer.wait(15)

    def observe_snapshot(source, target):
        snapshots_started.set()
        return original_snapshot(source, target)

    monkeypatch.setattr(module, "storage_write_lock", pause_after_write)
    monkeypatch.setattr(backup, "_snapshot", observe_snapshot)
    archive = tmp_path / "after.zip"
    with ThreadPoolExecutor(max_workers=2) as executor:
        writer_future = executor.submit(_write, application, kind)
        try:
            assert writer_paused.wait(10)
            backup_future = executor.submit(backup.backup_application, application, archive)
            assert not snapshots_started.wait(0.2)
        finally:
            release_writer.set()
        writer_future.result(timeout=15)
        backup_future.result(timeout=15)
    assert snapshots_started.is_set()
    restored = tmp_path / "restored"
    backup.restore_application(archive, restored)
    _assert_contents(restored, kind, after=True)


def test_outer_application_lock_reentrant_for_all_legacy_writers_and_backup(
    application, tmp_path,
):
    def nested_work():
        with storage_write_lock(application):
            for kind in KINDS:
                _write(application, kind)
            return backup.backup_application(application, tmp_path / "nested.zip")

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(nested_work).result(timeout=15)
    for kind in KINDS:
        _assert_contents(application, kind, after=True)


@pytest.mark.parametrize("kind", ("journal", "watchlist"))
@pytest.mark.parametrize("existing", (False, True), ids=["first-publish", "append"])
def test_db_writer_lock_spans_exists_initialize_and_transaction_cleanup(
    tmp_path, monkeypatch, kind, existing,
):
    root = tmp_path / "application"
    root.mkdir()
    if existing:
        _write(root, kind)
    cls = JournalStore if kind == "journal" else WatchlistStore
    original_exists = cls._exists
    original_connect = cls._connect
    original_initialize = cls._initialize
    checks = []

    def excluded():
        def contender():
            with (
                pytest.raises(StorageLockError),
                storage_write_lock(root, timeout_seconds=0),
            ):
                pytest.fail("Writer released the common lock early.")
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(contender).result(timeout=5)
        checks.append(True)

    def exists(self):
        excluded()
        return original_exists(self)

    def initialize(self, parameters):
        excluded()
        result = original_initialize(self, parameters)
        excluded()
        return result

    @contextmanager
    def connect(self, **kwargs):
        excluded()
        with original_connect(self, **kwargs) as connection:
            yield connection
            excluded()
        excluded()  # commit and SQLite close completed while common lock held

    monkeypatch.setattr(cls, "_exists", exists)
    monkeypatch.setattr(cls, "_connect", connect)
    monkeypatch.setattr(cls, "_initialize", initialize)
    _write(root, kind)
    assert len(checks) >= 4


@pytest.mark.parametrize("kind", KINDS)
def test_lock_error_preserves_legacy_error_type_and_does_not_initialize(
    tmp_path, monkeypatch, kind,
):
    root = tmp_path / "application"
    root.mkdir()
    module, error = {
        "journal": (journal_module, JournalStorageError),
        "watchlist": (watchlist_module, WatchlistStorageError),
        "settings": (settings_module, SettingsError),
    }[kind]

    @contextmanager
    def unavailable(root):
        raise StorageLockError("sensitive error detail")
        yield

    monkeypatch.setattr(module, "storage_write_lock", unavailable)
    with pytest.raises(error) as failure:
        _write(root, kind)
    assert "sensitive" not in str(failure.value)
    assert list(root.iterdir()) == []


def test_settings_lock_spans_temp_fsync_replace_and_cleanup(tmp_path, monkeypatch):
    root = tmp_path / "application"
    root.mkdir()
    path = settings_module.settings_path(root / "data")
    fsync = settings_module.os.fsync
    replace_file = settings_module.os.replace
    unlink = Path.unlink
    checks = []

    def excluded():
        def contender():
            with (
                pytest.raises(StorageLockError),
                storage_write_lock(root, timeout_seconds=0),
            ):
                pytest.fail("Settings released the shared lock early.")
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(contender).result(timeout=5)
        checks.append(True)

    def inspect_fsync(fd):
        excluded()
        return fsync(fd)

    def inspect_replace(source, target):
        excluded()
        assert target == path
        return replace_file(source, target)

    def inspect_cleanup(target, *args, **kwargs):
        if target.parent == path.parent and target.name.startswith(".provider-"):
            excluded()
        return unlink(target, *args, **kwargs)

    monkeypatch.setattr(settings_module.os, "fsync", inspect_fsync)
    monkeypatch.setattr(settings_module.os, "replace", inspect_replace)
    monkeypatch.setattr(Path, "unlink", inspect_cleanup)
    _write(root, "settings")
    assert len(checks) == 3
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("kind", KINDS)
def test_missing_application_parent_fails_closed_with_original_error_class(tmp_path, kind):
    root = tmp_path / "missing" / "application"
    error = {
        "journal": JournalStorageError, "watchlist": WatchlistStorageError,
        "settings": SettingsError,
    }[kind]
    with pytest.raises(error):
        _write(root, kind)
    assert not root.exists()
