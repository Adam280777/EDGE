"""User-bundle acceptance on temporary data, never existing user stores."""

import hashlib
import json
import sqlite3
import threading
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from edge import application_backup as backup
from edge.journal import JournalDraft
from edge.journal_backup import backup_journal
from edge.journal_store import JournalStore
from edge.provider_settings import ProviderSettings, save_settings
from edge.watchlist import WatchlistDraft
from edge.watchlist_store import WatchlistStore


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "original"
    root.mkdir()
    JournalStore(root / "data").create(JournalDraft(
        "swing", "TEST", "Declared", "Temporary", date(2020, 1, 1),
        "User thesis", "", "", "", "",
    ))
    WatchlistStore(root / "data").create(WatchlistDraft("TEST", "Declared"))
    save_settings(root / "data", ProviderSettings())
    return root


def rewrite(source, destination, transform):
    with zipfile.ZipFile(source) as bundle:
        entries = {n: bundle.read(n) for n in bundle.namelist()}
    transform(entries)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name, contents in entries.items():
            bundle.writestr(name, contents)


def test_roundtrip_all_known_stores_settings_and_exclusions(root, tmp_path):
    original = {p.name: p.read_bytes() for p in (root / "data").glob("*.sqlite3")}
    (root / "data" / "edge.sqlite3").write_bytes(b"excluded simulator")
    (root / "settings" / "secret.env").write_text("not included")
    archive = tmp_path / "bundle.zip"
    manifest = backup.backup_application(root, archive)
    assert manifest["cross_store_consistency_verified"] is True
    assert set(manifest["absent"]) == {
        "data/portfolio.sqlite3", "data/decisions.sqlite3",
        "data/manual-monitoring.sqlite3",
    }
    target = tmp_path / "restored"
    assert backup.restore_application(archive, target) == manifest
    assert len(JournalStore(target / "data").list_entries()) == 1
    assert len(WatchlistStore(target / "data").list_entries()) == 1
    assert (target / "settings" / "provider-v1.json").is_file()
    assert not (target / "data" / "edge.sqlite3").exists()
    assert not (target / "settings" / "secret.env").exists()
    assert original == {
        p.name: p.read_bytes() for p in (root / "data").glob("*.sqlite3")
        if p.name != "edge.sqlite3"
    }


def test_missing_stores_explicit_and_reads_never_initialize(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    archive = tmp_path / "empty.zip"
    result = backup.backup_application(root, archive)
    assert result["entries"] == []
    assert len(result["absent"]) == 6
    assert not (root / "data").exists()
    target = tmp_path / "restored"
    backup.restore_application(archive, target)
    assert list((target / "data").iterdir()) == []


def test_unknown_store_fails_not_silently_omitted(root, tmp_path):
    (root / "data" / "unknown.sqlite3").write_bytes(b"unknown")
    archive = tmp_path / "bundle.zip"
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, archive)
    assert not archive.exists()


def test_no_archive_overwrite_and_no_empty_root_overwrite(root, tmp_path):
    archive = tmp_path / "bundle.zip"
    backup.backup_application(root, archive)
    before = archive.read_bytes()
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, archive)
    assert archive.read_bytes() == before
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(archive, target)
    assert list(target.iterdir()) == []


def test_journal_archive_compatibility_untouched(root, tmp_path):
    archive = tmp_path / "journal.zip"
    backup_journal(root / "data", archive)
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(archive, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()


@pytest.mark.parametrize("mutation", ["hash", "version", "path", "setting_secret", "schema"])
def test_tampered_bundle_rejected_before_publication(root, tmp_path, mutation):
    original = tmp_path / "original.zip"
    altered = tmp_path / "altered.zip"
    backup.backup_application(root, original)

    def change(entries):
        manifest = json.loads(entries["manifest.json"])
        if mutation == "hash":
            manifest["entries"][0]["sha256"] = "0" * 64
        elif mutation == "version":
            manifest["version"] = 2
        elif mutation == "path":
            entries["../evil"] = b"bad"
        elif mutation == "schema":
            manifest["entries"][0]["application_id"] = 1
        else:
            member = backup.SETTINGS_MEMBER
            values = json.loads(entries[member])
            values["credential"] = "forbidden"
            entries[member] = json.dumps(values).encode()
            record = next(e for e in manifest["entries"] if e["member"] == member)
            record.update(bytes=len(entries[member]),
                          sha256=hashlib.sha256(entries[member]).hexdigest())
        entries["manifest.json"] = json.dumps(manifest).encode()

    rewrite(original, altered, change)
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(altered, tmp_path / "restored")
    assert not (tmp_path / "restored").exists()
    assert not list(tmp_path.glob(".edge-restore-*"))


def test_archive_size_bounded_before_zip_constructor(tmp_path, monkeypatch):
    archive = tmp_path / "large.zip"
    archive.write_bytes(b"not parsed")
    monkeypatch.setattr(backup, "MAX_ARCHIVE_BYTES", 1)
    monkeypatch.setattr(backup.zipfile, "ZipFile",
                        lambda *a, **k: pytest.fail("ZipFile must not parse"))
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(archive, tmp_path / "restored")


def test_compressed_bomb_and_duplicate_members_rejected(tmp_path):
    for duplicate in (False, True):
        archive = tmp_path / f"bad-{duplicate}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("manifest.json", b"0" * 100000)
            if duplicate:
                with pytest.warns(UserWarning):
                    bundle.writestr("manifest.json", b"other")
        with pytest.raises(backup.ApplicationBackupError):
            backup.restore_application(archive, tmp_path / "restored")


def test_invalid_settings_no_archive(root, tmp_path):
    (root / backup.SETTINGS_MEMBER).write_text('{"secret":"forbidden"}')
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, tmp_path / "bad.zip")
    assert not (tmp_path / "bad.zip").exists()


def test_full_row_validation_not_only_headers(root, tmp_path):
    connection = sqlite3.connect(root / "data" / "watchlist.sqlite3")
    connection.execute("UPDATE watchlist_entries SET symbol = '<bad>'")
    connection.commit()
    connection.close()
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, tmp_path / "bad.zip")


def test_online_snapshot_includes_committed_wal(tmp_path):
    root = tmp_path / "application"
    data = root / "data"
    data.mkdir(parents=True)
    source = data / "test.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA application_id=1234")
    connection.execute("PRAGMA user_version=1")
    connection.execute("CREATE TABLE facts(value INTEGER)")
    connection.execute("INSERT INTO facts VALUES(7)")
    connection.commit()

    def validate(reader):
        assert [tuple(r) for r in reader.execute("SELECT * FROM facts")] == [(7,)]

    specs = (backup.StoreSpec("test.sqlite3", 1234, 1, validate),)
    archive = tmp_path / "wal.zip"
    try:
        manifest = backup.backup_application(root, archive, stores=specs)
        assert manifest["cross_store_consistency_verified"] is False
    finally:
        connection.close()
    backup.restore_application(archive, tmp_path / "restored", stores=specs)


def test_owner_registered_new_stores_roundtrip(root, tmp_path):
    from edge.decision_contracts import ManualProvenance, RiskPolicy
    from edge.decision_store import DecisionStore, RiskPolicyRevision
    from edge.monitoring import MonitoringRule
    from edge.monitoring_store import MonitoringStore
    from edge.portfolio import CashOpening
    from edge.portfolio_store import PortfolioStore

    now = datetime(2026, 11, 7, tzinfo=UTC)
    PortfolioStore(root / "data", clock=lambda: now).append_many((
        CashOpening(now, "GBP", Decimal(100), "Explicit user opening"),
    ))
    DecisionStore(root / "data").append(
        RiskPolicyRevision("a" * 32, 1, now, RiskPolicy())
    )
    MonitoringStore(root / "data").append_rule(MonitoringRule(
        "b" * 32, "TEST", "US", "USD", Decimal(10), None,
        "unchanged", 60, ManualProvenance("Manual threshold", now),
    ))
    archive = tmp_path / "complete.zip"
    result = backup.backup_application(root, archive)
    assert result["absent"] == []
    target = tmp_path / "recovered"
    backup.restore_application(archive, target)
    assert len(MonitoringStore(target / "data").rules()) == 1
    assert len(DecisionStore(target / "data").list_records("risk")) == 1
    specs = {s.filename: s for s in backup.default_store_specs()}
    backup._validate_database(target / "data" / "portfolio.sqlite3",
                              specs["portfolio.sqlite3"])


def test_invalid_owner_schema_rejected(root, tmp_path):
    connection = sqlite3.connect(root / "data" / "portfolio.sqlite3")
    connection.execute("PRAGMA application_id=1")
    connection.execute("PRAGMA user_version=1")
    connection.close()
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, tmp_path / "bad.zip")


def test_manifest_duplicate_keys_rejected(root, tmp_path):
    archive = tmp_path / "valid.zip"
    altered = tmp_path / "duplicate.zip"
    backup.backup_application(root, archive)

    def change(entries):
        raw = entries["manifest.json"]
        entries["manifest.json"] = b'{"version":1,' + raw[1:]

    rewrite(archive, altered, change)
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(altered, tmp_path / "recovered")


def test_backup_suspends_cooperating_writers_through_settings(root, tmp_path, monkeypatch):
    from edge.application_storage_lock import StorageLockError, storage_write_lock

    observations = []
    original_snapshot = backup._snapshot
    original_settings = backup._settings

    def check_busy():
        def writer():
            try:
                with storage_write_lock(root, timeout_seconds=0):
                    observations.append("unexpected writer")
            except StorageLockError:
                observations.append("suspended")
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()

    def snapshot(source, destination):
        check_busy()
        original_snapshot(source, destination)

    def settings(contents):
        check_busy()
        original_settings(contents)

    monkeypatch.setattr(backup, "_snapshot", snapshot)
    monkeypatch.setattr(backup, "_settings", settings)
    manifest = backup.backup_application(root, tmp_path / "locked.zip")
    assert observations == ["suspended"] * 3
    # Independent QA separately proved registered legacy/new writer coverage.
    # The claim does NOT include non-cooperating writers or provider acceptance.
    assert manifest["cross_store_consistency_verified"] is True


def test_restore_publication_race_no_clobber(root, tmp_path, monkeypatch):
    archive = tmp_path / "bundle.zip"
    backup.backup_application(root, archive)
    target = tmp_path / "restored"
    original = backup._publish_directory

    def raced(source, destination):
        destination.mkdir()
        (destination / "marker").write_text("existing")
        original(source, destination)

    monkeypatch.setattr(backup, "_publish_directory", raced)
    with pytest.raises(backup.ApplicationBackupError):
        backup.restore_application(archive, target)
    assert (target / "marker").read_text() == "existing"


def test_reparse_ancestor_refused(root, tmp_path, monkeypatch):
    original = Path.lstat

    class Junction:
        st_mode = 0o40755
        st_file_attributes = 0x400

    def lstat(path):
        return Junction() if path == root else original(path)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(backup.ApplicationBackupError):
        backup.backup_application(root, tmp_path / "bad.zip")
