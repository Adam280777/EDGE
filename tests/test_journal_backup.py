"""Journal recovery acceptance against temporary stores only, never user data."""

import hashlib
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import edge.journal_backup as recovery
from edge.cli import main
from edge.journal import JournalDraft, SourceReference
from edge.journal_backup import JournalBackupError, backup_journal, restore_journal
from edge.journal_store import JournalStore
from edge.validation import canonical_json


@pytest.fixture
def draft() -> JournalDraft:
    return JournalDraft(
        "swing", "NOTE", "User-declared company", "Local hypothesis",
        date(2020, 1, 2), "Unverified opinion", "", "", "", "",
        (SourceReference("Manual source", "https://example.test"),),
    )


@pytest.fixture
def store(tmp_path: Path, draft: JournalDraft) -> JournalStore:
    result = JournalStore(tmp_path / "source")
    result.create(draft)
    result.create(replace(draft, horizon="long-term", title="Long hypothesis"))
    return result


@pytest.fixture(autouse=True)
def forbid_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    import edge.data
    import edge.simulation_service

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Recovery must not call a provider, engine or network")

    monkeypatch.setattr(edge.data, "lookup_sec_filings", forbidden)
    monkeypatch.setattr(edge.simulation_service, "run", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def unpack(archive: Path) -> tuple[dict, bytes]:
    with zipfile.ZipFile(archive) as bundle:
        return json.loads(bundle.read("manifest.json")), bundle.read(recovery.DATABASE)


def repack(archive: Path, manifest: dict, snapshot: bytes) -> None:
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", canonical_json(manifest))
        bundle.writestr(recovery.DATABASE, snapshot)


def mutate_database(path: Path, kind: str) -> None:
    if kind == "corrupt":
        path.write_bytes(b"not sqlite")
    elif kind == "empty":
        path.write_bytes(b"")
    elif kind == "foreign":
        path.unlink()
        with closing(sqlite3.connect(path)) as connection:
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.commit()
    else:
        statements = {
            "version": "PRAGMA user_version=2",
            "application": "PRAGMA application_id=1",
            "schema": "CREATE TABLE unexpected (value TEXT)",
            "record": "UPDATE journal_entries SET sources_json='invalid'",
            "late-record": (
                "UPDATE journal_entries SET thesis='' WHERE entry_id="
                "(SELECT entry_id FROM journal_entries ORDER BY entry_id DESC LIMIT 1)"
            ),
        }
        with closing(sqlite3.connect(path)) as connection:
            connection.execute(statements[kind])
            connection.commit()


def test_round_trip_new_and_empty_root_source_unchanged(
    tmp_path: Path, store: JournalStore, draft: JournalDraft
) -> None:
    # Unrelated application evidence must neither enter the backup nor change.
    (store.root / "private-run.json").write_text("unrelated", encoding="utf-8")
    before = files(store.root)
    archive = tmp_path / "journal.zip"
    manifest = backup_journal(store.root, archive)
    document, snapshot = unpack(archive)
    assert manifest == document
    assert document["format"] == recovery.FORMAT
    assert document["version"] == recovery.VERSION
    assert document["entry_count"] == 2
    assert document["snapshot_bytes"] == len(snapshot)
    assert document["snapshot_sha256"] == hashlib.sha256(snapshot).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {"manifest.json", recovery.DATABASE}
    archive_before = archive.read_bytes()
    for existing in (False, True):
        root = tmp_path / ("empty" if existing else "new")
        if existing:
            root.mkdir()
        assert restore_journal(archive, root) == manifest
        restored = JournalStore(root)
        assert restored.list_entries() == store.list_entries()
        for summary in store.list_entries():
            assert restored.get(summary.entry_id) == store.get(summary.entry_id)
        # A recovered file remains usable as a standalone append-only store.
        restored.create(draft)
        assert len(restored.list_entries()) == 3
        assert set(root.iterdir()) == {restored.database}
    assert files(store.root) == before
    assert archive.read_bytes() == archive_before
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize(
    "kind", ["corrupt", "empty", "foreign", "version", "application", "schema", "record", "late-record"]
)
def test_invalid_source_no_backup_no_source_changes(
    tmp_path: Path, store: JournalStore, kind: str
) -> None:
    mutate_database(store.database, kind)
    before = files(store.root)
    with pytest.raises(JournalBackupError):
        backup_journal(store.root, tmp_path / "never.zip")
    assert files(store.root) == before
    assert not (tmp_path / "never.zip").exists()
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize(
    "kind", ["corrupt", "empty", "foreign", "version", "application", "schema", "record"]
)
def test_restore_valid_hash_is_not_enough(
    tmp_path: Path, store: JournalStore, kind: str
) -> None:
    archive = tmp_path / "journal.zip"
    backup_journal(store.root, archive)
    manifest, snapshot = unpack(archive)
    work = tmp_path / "mutated.sqlite3"
    work.write_bytes(snapshot)
    mutate_database(work, kind)
    snapshot = work.read_bytes()
    manifest["snapshot_bytes"] = len(snapshot)
    manifest["snapshot_sha256"] = hashlib.sha256(snapshot).hexdigest()
    repack(archive, manifest, snapshot)
    before = files(tmp_path)
    with pytest.raises(JournalBackupError):
        restore_journal(archive, tmp_path / "never")
    assert files(tmp_path) == before
    assert not (tmp_path / "never").exists()
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format", "foreign"), ("version", 2), ("version", True),
        ("application_id", 0), ("schema_version", 2), ("schema_version", True),
        ("entry_count", 1), ("entry_count", -1), ("snapshot_bytes", 1),
        ("snapshot_bytes", recovery.MAX_SNAPSHOT_BYTES + 1),
        ("snapshot_sha256", "0" * 64), ("snapshot_sha256", None),
        ("unexpected", "field"),
    ],
)
def test_manifest_mismatch_refused(
    tmp_path: Path, store: JournalStore, field: str, value: object
) -> None:
    archive = tmp_path / "journal.zip"
    backup_journal(store.root, archive)
    manifest, snapshot = unpack(archive)
    manifest[field] = value
    repack(archive, manifest, snapshot)
    with pytest.raises(JournalBackupError):
        restore_journal(archive, tmp_path / "never")
    assert not (tmp_path / "never").exists()
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize("kind", ["truncated", "crc", "duplicate", "path", "compressed", "bad-json"])
def test_malformed_archives_cannot_publish_or_extract(
    tmp_path: Path, store: JournalStore, kind: str
) -> None:
    archive = tmp_path / "journal.zip"
    backup_journal(store.root, archive)
    manifest, snapshot = unpack(archive)
    if kind == "truncated":
        archive.write_bytes(archive.read_bytes()[:50])
    elif kind == "crc":
        data = bytearray(archive.read_bytes())
        offset = data.index(b"SQLite format 3")
        data[offset] ^= 1
        archive.write_bytes(data)
    else:
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("manifest.json", "{" if kind == "bad-json" else canonical_json(manifest))
            bundle.writestr(
                "../escape.sqlite3" if kind == "path" else recovery.DATABASE,
                snapshot,
                compress_type=zipfile.ZIP_DEFLATED if kind == "compressed" else zipfile.ZIP_STORED,
            )
            if kind == "duplicate":
                with pytest.warns(UserWarning):
                    bundle.writestr("manifest.json", "{}")
    before = files(tmp_path)
    with pytest.raises(JournalBackupError):
        restore_journal(archive, tmp_path / "never")
    assert files(tmp_path) == before
    assert not (tmp_path / "never").exists()


@pytest.mark.parametrize("kind", ["file", "empty", "directory", "source"])
def test_backup_existing_target_never_clobbered(
    tmp_path: Path, store: JournalStore, kind: str
) -> None:
    target = tmp_path / "existing"
    if kind == "directory":
        target.mkdir()
    elif kind == "source":
        target = store.database
    else:
        target.write_bytes(b"keep" if kind == "file" else b"")
    before = files(tmp_path)
    with pytest.raises(JournalBackupError):
        backup_journal(store.root, target)
    assert files(tmp_path) == before


@pytest.mark.parametrize("kind", ["valid", "corrupt", "empty", "sidecar", "other-file", "root-file"])
def test_restore_existing_content_never_clobbered(
    tmp_path: Path, store: JournalStore, kind: str
) -> None:
    archive = tmp_path / "journal.zip"
    backup_journal(store.root, archive)
    root = tmp_path / "existing"
    if kind == "root-file":
        root.write_bytes(b"not a directory")
    elif kind == "valid":
        root = store.root
    else:
        root.mkdir()
        name = {
            "sidecar": recovery.DATABASE + "-wal", "other-file": "private.txt"
        }.get(kind, recovery.DATABASE)
        (root / name).write_bytes(b"" if kind == "empty" else b"preserve")
    before = files(tmp_path)
    with pytest.raises(JournalBackupError):
        restore_journal(archive, root)
    assert files(tmp_path) == before


def test_dangling_backup_target_no_clobber(tmp_path: Path, store: JournalStore) -> None:
    target = tmp_path / "dangling"
    try:
        target.symlink_to(tmp_path / "missing")
    except OSError:
        pytest.skip("Symlink creation unavailable for this Windows account")
    with pytest.raises(JournalBackupError):
        backup_journal(store.root, target)
    assert target.is_symlink()
    assert not (tmp_path / "missing").exists()


def make_junction(junction: Path, target: Path, temporary_root: Path) -> None:
    """Create only an exact temporary junction, without requiring elevation."""
    assert junction.is_absolute() and target.is_absolute()
    junction.relative_to(temporary_root)
    target.relative_to(temporary_root)
    result = subprocess.run(
        [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert junction.is_junction()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction regression")
@pytest.mark.parametrize(("ancestor", "late"), [(False, False), (True, False), (False, True)])
def test_restore_refuses_junction_root_and_ancestors_at_publication(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
    ancestor: bool, late: bool,
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    outside = tmp_path / "must-remain-untouched"
    outside.mkdir()
    junction = tmp_path / "junction"
    root = junction / "new" if ancestor else junction
    if ancestor:
        (outside / "new").mkdir()
    before = files(outside)

    def install_junction() -> None:
        if late:
            root.rmdir()
            if ancestor:
                junction.rmdir()
        make_junction(junction, outside, tmp_path)

    if late:
        root.mkdir(parents=True)
        sync = recovery._sync

        def late_junction(path: Path) -> None:
            sync(path)
            install_junction()

        monkeypatch.setattr(recovery, "_sync", late_junction)
    else:
        install_junction()
    try:
        with pytest.raises(JournalBackupError):
            restore_journal(archive, root)
        assert files(outside) == before
        assert not list(tmp_path.glob(".edge-journal-*"))
    finally:
        # Remove the junction itself, NEVER recursively remove its target.
        if junction.is_junction():
            junction.rmdir()
    assert outside.is_dir()
    assert files(outside) == before


def test_restore_rechecks_ancestor_reparse_attributes_before_publication(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    parent = tmp_path / "normal-parent"
    parent.mkdir()
    root = parent / "new"
    original_lstat = Path.lstat
    sync = recovery._sync
    link = Mock(side_effect=AssertionError("Publication after ancestor redirect"))

    def reparse_lstat(path: Path):
        metadata = original_lstat(path)
        if path == parent:
            return SimpleNamespace(
                st_mode=metadata.st_mode,
                st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT,
            )
        return metadata

    def late_reparse(path: Path) -> None:
        sync(path)
        # Model an ancestor becoming a reparse point after validation. The real
        # Windows junction regressions above cover non-symlink lstat attributes.
        monkeypatch.setattr(Path, "lstat", reparse_lstat)

    monkeypatch.setattr(recovery, "_sync", late_reparse)
    monkeypatch.setattr(recovery.os, "link", link)
    with pytest.raises(JournalBackupError):
        restore_journal(archive, root)
    link.assert_not_called()
    assert not root.exists()
    assert list(parent.iterdir()) == []


def test_oversized_raw_archive_rejected_before_zip_parser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Reduced cap keeps the sparse-file regression small on non-sparse volumes.
    monkeypatch.setattr(recovery, "MAX_ARCHIVE_BYTES", 1024, raising=False)
    archive = tmp_path / "oversized.zip"
    with archive.open("wb") as stream:
        stream.seek(1024)
        stream.write(b"\0")
    parser = Mock(side_effect=AssertionError("Oversized file reached ZIP parser"))
    monkeypatch.setattr(recovery.zipfile, "ZipFile", parser)
    with pytest.raises(JournalBackupError):
        recovery._unpack(archive, tmp_path / "never.sqlite3")
    parser.assert_not_called()
    assert not (tmp_path / "never.sqlite3").exists()


@pytest.mark.parametrize("member", ["snapshot", "manifest"])
def test_member_size_cap_applies_before_payload_read(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
    member: str,
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    monkeypatch.setattr(
        recovery, "MAX_SNAPSHOT_BYTES" if member == "snapshot" else "MAX_MANIFEST_BYTES", 1
    )
    read = Mock(side_effect=AssertionError("Oversized member read"))
    monkeypatch.setattr(zipfile.ZipFile, "read", read)
    with pytest.raises(JournalBackupError):
        recovery._unpack(archive, tmp_path / "never.sqlite3")
    read.assert_not_called()


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_concurrent_publication_only_one_wins(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    root = tmp_path / "empty"
    root.mkdir()
    target = tmp_path / "competing.zip"
    ready = threading.Barrier(2)
    link = os.link

    def competing_link(source: Path, destination: Path) -> None:
        ready.wait(timeout=10)
        link(source, destination)

    def attempt() -> bool:
        try:
            if operation == "backup":
                backup_journal(store.root, target)
            else:
                restore_journal(archive, root)
            return True
        except JournalBackupError:
            return False

    monkeypatch.setattr(recovery.os, "link", competing_link)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt) for _ in range(2)]
        assert sorted(future.result(timeout=20) for future in futures) == [False, True]
    if operation == "backup":
        assert unpack(target)[0]["entry_count"] == 2
    else:
        assert JournalStore(root).list_entries() == store.list_entries()
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize("race", [False, True])
def test_failed_publication_cleanup_and_late_target_preserved(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
    operation: str, race: bool,
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    target = tmp_path / "new.zip" if operation == "backup" else tmp_path / "new" / recovery.DATABASE
    unrelated = tmp_path / ".edge-journal-other"
    unrelated.write_bytes(b"keep unrelated")
    before = files(store.root)
    link = os.link

    def fail(source: Path, destination: Path) -> None:
        # Publication sees only a complete validated artifact, never an open DB.
        assert source.is_file()
        assert not Path(str(source) + "-wal").exists()
        if operation == "restore":
            assert recovery._validate(source) == 2
        else:
            assert unpack(source)[0]["entry_count"] == 2
        if race:
            destination.write_bytes(b"race winner")
            link(source, destination)
        raise OSError("private path SQL secret")

    monkeypatch.setattr(recovery.os, "link", fail)
    with pytest.raises(JournalBackupError) as error:
        if operation == "backup":
            backup_journal(store.root, target)
        else:
            restore_journal(archive, target.parent)
    assert "secret" not in str(error.value)
    assert files(store.root) == before
    if race:
        assert target.read_bytes() == b"race winner"
    else:
        assert not target.exists()
        if operation == "restore":
            assert not target.parent.exists()
    assert unrelated.read_bytes() == b"keep unrelated"
    assert list(tmp_path.glob(".edge-journal-*")) == [unrelated]


def test_wal_snapshot_committed_records_and_source_untouched(
    tmp_path: Path, store: JournalStore, draft: JournalDraft
) -> None:
    with closing(sqlite3.connect(store.database)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        # Keep a reader active so the subsequent committed WAL cannot be
        # checkpointed/deleted when JournalStore closes its write connection.
        writer.execute("BEGIN")
        writer.execute("SELECT COUNT(*) FROM journal_entries").fetchone()
        entry = store.create(replace(draft, title="Committed in WAL"))
        # Include committed WAL data, not merely the older main DB file.
        assert Path(str(store.database) + "-wal").stat().st_size > 0
        before_db = store.database.read_bytes()
        before_wal = Path(str(store.database) + "-wal").read_bytes()
        archive = tmp_path / "wal.zip"
        backup_journal(store.root, archive)
        assert store.database.read_bytes() == before_db
        assert Path(str(store.database) + "-wal").read_bytes() == before_wal
        restore_journal(archive, tmp_path / "restored")
        assert JournalStore(tmp_path / "restored").get(entry.entry_id) == entry


def test_busy_online_backup_stops_at_deadline(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = sqlite3.connect
    before = files(store.root)
    monkeypatch.setattr(recovery, "BACKUP_TIMEOUT_SECONDS", 0.1)

    class BusyConnection(sqlite3.Connection):
        def backup(self, target, **kwargs):
            # Lock only after the metadata reads, exercising the backup API's
            # SQLITE_BUSY callback rather than a preliminary connect timeout.
            with closing(connect(store.database)) as blocker:
                blocker.execute("BEGIN EXCLUSIVE")
                try:
                    return super().backup(target, **kwargs)
                finally:
                    blocker.rollback()

    def busy_connect(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("?mode=ro"):
            kwargs["factory"] = BusyConnection
        return connect(*args, **kwargs)

    monkeypatch.setattr(recovery.sqlite3, "connect", busy_connect)
    with pytest.raises(JournalBackupError):
        backup_journal(store.root, tmp_path / "never.zip")
    assert files(store.root) == before
    assert not (tmp_path / "never.zip").exists()
    assert not list(tmp_path.glob(".edge-journal-*"))


def test_writer_during_online_backup_is_consistent(
    tmp_path: Path, store: JournalStore, draft: JournalDraft, monkeypatch: pytest.MonkeyPatch
) -> None:
    connect = sqlite3.connect
    writes = []

    class ObservedConnection(sqlite3.Connection):
        def backup(self, target, **kwargs):
            # A save after the RO connection opens is included consistently.
            writes.append(store.create(replace(draft, title="Concurrent save")))
            return super().backup(target, **kwargs)

    def observed_connect(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("?mode=ro"):
            kwargs["factory"] = ObservedConnection
        return connect(*args, **kwargs)

    monkeypatch.setattr(recovery.sqlite3, "connect", observed_connect)
    archive = tmp_path / "concurrent.zip"
    assert backup_journal(store.root, archive)["entry_count"] == 3
    restore_journal(archive, tmp_path / "restored")
    assert JournalStore(tmp_path / "restored").get(writes[0].entry_id) == writes[0]


def test_save_between_backup_steps_restarts_consistently(
    tmp_path: Path, store: JournalStore, draft: JournalDraft,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # More than one 128-page step, so the write happens DURING online backup,
    # not merely after opening its connection.
    for index in range(20):
        store.create(replace(
            draft, title=f"Large note {index}", thesis="a" * 10000,
            bull_case="b" * 10000, bear_case="c" * 10000,
            catalysts="d" * 10000, invalidation="e" * 10000,
        ))
    connect = sqlite3.connect
    saves = []

    class ConcurrentConnection(sqlite3.Connection):
        def backup(self, target, **kwargs):
            progress = kwargs["progress"]

            def save_between_steps(status, remaining, total):
                if remaining > 0 and not saves:
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(
                            store.create, replace(draft, title="Saved mid-backup")
                        )
                        saves.append(future.result(timeout=10))
                progress(status, remaining, total)

            kwargs["progress"] = save_between_steps
            return super().backup(target, **kwargs)

    def concurrent_connect(*args, **kwargs):
        if isinstance(args[0], str) and args[0].endswith("?mode=ro"):
            kwargs["factory"] = ConcurrentConnection
        return connect(*args, **kwargs)

    monkeypatch.setattr(recovery.sqlite3, "connect", concurrent_connect)
    archive = tmp_path / "during-copy.zip"
    manifest = backup_journal(store.root, archive)
    assert len(saves) == 1
    assert manifest["entry_count"] == 23
    restore_journal(archive, tmp_path / "restored")
    assert JournalStore(tmp_path / "restored").list_entries() == store.list_entries()


def test_uncommitted_save_is_not_backed_up(
    tmp_path: Path, store: JournalStore
) -> None:
    with closing(sqlite3.connect(store.database)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE journal_entries SET title='uncommitted'")
        # A read-only online backup can read the prior committed state.
        archive = tmp_path / "before-commit.zip"
        backup_journal(store.root, archive)
        writer.rollback()
    restore_journal(archive, tmp_path / "restored")
    assert JournalStore(tmp_path / "restored").list_entries() == store.list_entries()


def test_backup_deadline_and_failure_cleanup(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recovery, "BACKUP_TIMEOUT_SECONDS", -1)
    with pytest.raises(JournalBackupError):
        backup_journal(store.root, tmp_path / "never.zip")
    assert not (tmp_path / "never.zip").exists()
    assert not list(tmp_path.glob(".edge-journal-*"))


@pytest.mark.parametrize("operation", ["backup", "restore"])
@pytest.mark.parametrize("stage", ["copy", "sync"])
def test_partial_staging_failure_never_publishes(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
    operation: str, stage: str,
) -> None:
    archive = tmp_path / "source.zip"
    backup_journal(store.root, archive)
    before = files(tmp_path)

    def failed_copy(source: Path, destination: Path):
        destination.write_bytes(b"partial private snapshot")
        raise sqlite3.OperationalError("private SQL details")

    if stage == "sync":
        monkeypatch.setattr(recovery, "_sync", Mock(side_effect=OSError("private path")))
    else:
        monkeypatch.setattr(
            recovery, "_snapshot" if operation == "backup" else "_unpack", failed_copy
        )
    with pytest.raises(JournalBackupError):
        if operation == "backup":
            backup_journal(store.root, tmp_path / "never.zip")
        else:
            restore_journal(archive, tmp_path / "never")
    assert files(tmp_path) == before
    assert not list(tmp_path.glob(".edge-journal-*"))
    assert not (tmp_path / "never").exists()


@pytest.mark.parametrize("arguments", [
    ["journal-backup", "--help"], ["journal-restore", "--help"],
    ["journal-backup"], ["journal-restore", "absent.zip"],
    ["journal-backup", "new.zip", "--unknown"],
])
def test_cli_help_invalid_args_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as result:
        main(arguments)
    assert result.value.code in (0, 2)
    assert list(tmp_path.iterdir()) == []


def test_cli_round_trip_and_sanitized_nonzero_errors(
    tmp_path: Path, store: JournalStore, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    archive = tmp_path / "cli.zip"
    assert main(["journal-backup", str(archive), "--journal-dir", str(store.root)]) == 0
    assert json.loads(capsys.readouterr().out)["entry_count"] == 2
    root = tmp_path / "new"
    assert main(["journal-restore", str(archive), "--journal-dir", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["entry_count"] == 2
    before = files(tmp_path)
    assert main(["journal-restore", str(archive), "--journal-dir", str(root)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "EDGE error:" in captured.err
    assert "Traceback" not in captured.err
    assert str(root) not in captured.err
    monkeypatch.setattr(recovery, "backup_journal", Mock(side_effect=OSError("PRIVATE-NOTE SQL /private/path")))
    assert main(["journal-backup", str(archive)]) == 1
    assert "PRIVATE" not in capsys.readouterr().err
    assert files(tmp_path) == before


def test_missing_paths_do_not_initialize_any_store(tmp_path: Path) -> None:
    before = files(tmp_path)
    with pytest.raises(JournalBackupError):
        backup_journal(tmp_path / "missing-source", tmp_path / "out.zip")
    with pytest.raises(JournalBackupError):
        restore_journal(tmp_path / "missing.zip", tmp_path / "missing-root")
    assert files(tmp_path) == before
    assert list(tmp_path.iterdir()) == []


def test_recovery_cli_does_not_import_providers_in_fresh_process(tmp_path: Path) -> None:
    script = """
import importlib.abc
import sys
class ForbidProviders(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith(('edge.data', 'openbb', 'nautilus_trader')):
            raise AssertionError('Provider or engine imported by recovery')
sys.meta_path.insert(0, ForbidProviders())
from edge.cli import main
assert main(['journal-backup', 'never.zip', '--journal-dir', 'missing']) == 1
assert main(['journal-restore', 'missing.zip', '--journal-dir', 'new']) == 1
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=tmp_path, capture_output=True,
        text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []
