"""Versioned user-record bundles; separate from journal-only archive format.

Only registered, fully validated user stores and non-secret provider settings
are included. No credentials, vendor observations, simulator evidence or runtime
state. Hashes detect corruption, not authenticity. Consistency means ONLY a
cooperative locked cut of the five registered user stores plus settings. All
registered writer paths and internal links were independently tested. It does
not suspend external/manual SQL writers or establish identity, data rights,
freshness, financial/source acceptance or general production readiness.
"""

from __future__ import annotations

import ctypes
import hashlib
import importlib
import json
import os
import sqlite3
import stat
import sys
import tempfile
import time
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from edge.application_storage_lock import check_local_path, storage_write_lock
from edge.errors import EdgeError
from edge.provider_settings import ProviderSettings, SettingsError
from edge.validation import canonical_json

FORMAT = "edge.user-records-backup"
VERSION = 1
MAX_STORE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
MAX_ARCHIVE_BYTES = MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + 65536
MAX_STORES = 16
SETTINGS_MEMBER = "settings/provider-v1.json"
_ERROR = "Application backup/recovery failed safely; no existing destination replaced."


class ApplicationBackupError(EdgeError):
    """Unsupported, invalid, oversized or unavailable recovery input."""


@dataclass(frozen=True)
class StoreSpec:
    """Trusted CODE registration, never archive-supplied import/SQL instructions.

    validator must inspect the exact schema and EVERY stored record on the
    supplied read-only connection. Header checks alone are insufficient.
    """

    filename: str
    application_id: int
    schema_version: int
    validator: Callable[[sqlite3.Connection], None]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.filename, str)
            or Path(self.filename).name != self.filename
            or "/" in self.filename or "\\" in self.filename
            or not self.filename.endswith(".sqlite3")
            or type(self.application_id) is not int or self.application_id <= 0
            or type(self.schema_version) is not int or self.schema_version <= 0
            or not callable(self.validator)
        ):
            raise ApplicationBackupError(_ERROR)


def default_store_specs() -> tuple[StoreSpec, ...]:
    """Known owner-confirmed stores; full validators load only for present files."""
    from edge import journal_store, watchlist_store

    def journal(connection: sqlite3.Connection) -> None:
        journal_store._check_schema(connection)
        for row in connection.execute("SELECT * FROM journal_entries"):
            journal_store._parse_row(row)

    def watchlist(connection: sqlite3.Connection) -> None:
        watchlist_store._check_schema(connection)
        for row in connection.execute("SELECT * FROM watchlist_entries"):
            watchlist_store._parse_row(row)

    def owned_validator(module: str) -> Callable[[sqlite3.Connection], None]:
        def validate(connection: sqlite3.Connection) -> None:
            validator = getattr(importlib.import_module(module), "validate_database", None)
            if not callable(validator):
                raise ApplicationBackupError(_ERROR)
            validator(connection)
        return validate

    return (
        StoreSpec("research-journal.sqlite3", journal_store.APPLICATION_ID,
                  journal_store.SCHEMA_VERSION, journal),
        StoreSpec("watchlist.sqlite3", watchlist_store.APPLICATION_ID,
                  watchlist_store.SCHEMA_VERSION, watchlist),
        # Exact filename/IDs supplied by the respective implementation owners.
        StoreSpec("portfolio.sqlite3", 0x45445046, 1,
                  owned_validator("edge.portfolio_store")),
        StoreSpec("decisions.sqlite3", 0x45444453, 1,
                  owned_validator("edge.decision_store")),
        StoreSpec("manual-monitoring.sqlite3", 0x45444D31, 1,
                  owned_validator("edge.monitoring_store")),
    )


def _specs(stores: tuple[StoreSpec, ...] | None) -> dict[str, StoreSpec]:
    selected = default_store_specs() if stores is None else stores
    if (
        not isinstance(selected, tuple) or len(selected) > MAX_STORES
        or any(not isinstance(s, StoreSpec) for s in selected)
        or len({s.filename for s in selected}) != len(selected)
    ):
        raise ApplicationBackupError(_ERROR)
    return {s.filename: s for s in selected}


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _validate_database(path: Path, spec: StoreSpec) -> None:
    with closing(sqlite3.connect(
        f"{path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=1
    )) as connection:
        connection.row_factory = sqlite3.Row
        if (
            connection.execute("PRAGMA application_id").fetchone()[0]
            != spec.application_id
            or connection.execute("PRAGMA user_version").fetchone()[0]
            != spec.schema_version
            or connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete"
            or [tuple(r) for r in connection.execute("PRAGMA quick_check")] != [("ok",)]
        ):
            raise ApplicationBackupError(_ERROR)
        spec.validator(connection)


def _snapshot(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + 10

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() >= deadline or total * page_size > MAX_STORE_BYTES:
            raise ApplicationBackupError(_ERROR)

    with closing(sqlite3.connect(
        f"{source.as_uri()}?mode=ro", uri=True, timeout=0.1
    )) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.execute("PRAGMA query_only=ON")
        page_size = reader.execute("PRAGMA page_size").fetchone()[0]
        if reader.execute("PRAGMA page_count").fetchone()[0] * page_size > MAX_STORE_BYTES:
            raise ApplicationBackupError(_ERROR)
        reader.backup(writer, pages=128, progress=progress, sleep=0.025)
        if writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise ApplicationBackupError(_ERROR)


def _settings(data: bytes) -> None:
    if not 0 < len(data) <= 4096:
        raise ApplicationBackupError(_ERROR)

    values = json.loads(data, object_pairs_hook=_unique_pairs)
    if not isinstance(values, dict) or set(values) != set(ProviderSettings.__dataclass_fields__):
        raise ApplicationBackupError(_ERROR)
    ProviderSettings(**values).validate()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ApplicationBackupError(_ERROR)
        result[key] = value
    return result


def _record(path: Path, member: str, spec: StoreSpec | None = None) -> dict:
    size = path.stat().st_size
    if not 0 < size <= (MAX_STORE_BYTES if spec else 4096):
        raise ApplicationBackupError(_ERROR)
    record = {"member": member, "bytes": size, "sha256": _hash(path)}
    if spec:
        record.update(application_id=spec.application_id, schema_version=spec.schema_version)
    return record


def backup_application(
    application_root: Path, destination: Path, *,
    stores: tuple[StoreSpec, ...] | None = None,
) -> dict[str, object]:
    """Snapshot registered stores plus settings; atomically publish absent ZIP.

    application_root/data and application_root/settings is the only layout.
    Unknown SQLite files fail closed except edge.sqlite3 (simulator, excluded).
    Missing registered stores remain explicitly absent; no empty DB is created.
    Consistency is certified only for the default registry's cooperating writers;
    custom StoreSpec registrations produce an explicitly uncertified bundle.
    Equal ticker strings are declarations, not cross-store referential links.
    """
    try:
        specs = _specs(stores)
        root = check_local_path(application_root)
        target = check_local_path(destination)
        if not root.is_dir() or not target.parent.is_dir() or os.path.lexists(target):
            raise ApplicationBackupError(_ERROR)
        with tempfile.TemporaryDirectory(prefix=".edge-bundle-", dir=target.parent) as temp:
            staging = Path(temp)
            entries = []
            absent = []
            with storage_write_lock(root):
                data_root = check_local_path(root / "data")
                if data_root.exists():
                    if not data_root.is_dir():
                        raise ApplicationBackupError(_ERROR)
                    unknown = {p.name for p in data_root.glob("*.sqlite3")} - specs.keys() - {"edge.sqlite3"}
                    if unknown:
                        raise ApplicationBackupError(_ERROR)
                for name, spec in specs.items():
                    source = check_local_path(data_root / name)
                    if not os.path.lexists(source):
                        absent.append(f"data/{name}")
                        continue
                    if not source.is_file():
                        raise ApplicationBackupError(_ERROR)
                    for suffix in ("-wal", "-shm", "-journal"):
                        check_local_path(Path(str(source) + suffix))
                    snapshot = staging / name
                    _snapshot(source, snapshot)
                    _validate_database(snapshot, spec)
                    entries.append(_record(snapshot, f"data/{name}", spec))
                source_settings = check_local_path(root / SETTINGS_MEMBER)
                if os.path.lexists(source_settings):
                    with source_settings.open("rb") as stream:
                        settings = stream.read(4097)
                    _settings(settings)
                    (staging / "provider-v1.json").write_bytes(settings)
                    entries.append(_record(staging / "provider-v1.json", SETTINGS_MEMBER))
                else:
                    absent.append(SETTINGS_MEMBER)
            if sum(e["bytes"] for e in entries) > MAX_TOTAL_BYTES:
                raise ApplicationBackupError(_ERROR)
            manifest = {
                "format": FORMAT, "version": VERSION,
                "consistency": "cooperative_locked_cut_registered_user_stores",
                "cross_store_consistency_verified": stores is None,
                "entries": entries, "absent": absent,
                "excluded": ["credentials", "vendor_state", "simulation_runs", "runtime"],
            }
            archive = staging / "bundle.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("manifest.json", canonical_json(manifest))
                for entry in entries:
                    bundle.write(staging / Path(entry["member"]).name, entry["member"])
            with archive.open("r+b") as stream:
                os.fsync(stream.fileno())
            check_local_path(target)
            os.link(archive, target)
            return manifest
    except (OSError, sqlite3.Error, EdgeError, SettingsError, ValueError, TypeError, KeyError,
            zipfile.BadZipFile):
        raise ApplicationBackupError(_ERROR) from None


def _publish_directory(source: Path, destination: Path) -> None:
    """Atomic directory publication WITHOUT replacement; fail on unsupported OS."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses an existing destination.
    elif sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        if rename is None:
            raise ApplicationBackupError(_ERROR)
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                           ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), "No-clobber publication failed.")
    else:
        raise ApplicationBackupError(_ERROR)


def restore_application(
    archive: Path, destination_root: Path, *,
    stores: tuple[StoreSpec, ...] | None = None,
) -> dict[str, object]:
    """Restore to an ABSENT application root; even existing empty roots refused.

    Returns the manifest. Use destination_root/data as a recovered data root;
    settings remain destination_root/settings. Never switches the active root.
    Journal-only archives remain handled solely by restore_journal().
    A manifest's consistency field describes the locked snapshot procedure,
    not authenticity or independent verification of an untrusted archive.
    """
    try:
        specs = _specs(stores)
        source = check_local_path(archive)
        target = check_local_path(destination_root)
        if os.path.lexists(target) or not target.parent.is_dir():
            raise ApplicationBackupError(_ERROR)
        with source.open("rb") as stream:
            metadata = os.fstat(stream.fileno())
            # Bound before ZipFile parses/allocates its central directory.
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= MAX_ARCHIVE_BYTES:
                raise ApplicationBackupError(_ERROR)
            with zipfile.ZipFile(stream) as bundle:
                members = bundle.infolist()
                names = [m.filename for m in members]
                allowed = {"manifest.json", SETTINGS_MEMBER} | {f"data/{n}" for n in specs}
                if (
                    not 1 <= len(members) <= MAX_STORES + 2
                    or len(set(names)) != len(names) or not set(names) <= allowed
                    or "manifest.json" not in names
                    or any(m.compress_type != zipfile.ZIP_STORED or m.flag_bits & 1
                           or m.compress_size != m.file_size
                           or stat.S_ISLNK(m.external_attr >> 16)
                           or m.is_dir() for m in members)
                    or not 0 < bundle.getinfo("manifest.json").file_size <= MAX_MANIFEST_BYTES
                    or sum(m.file_size for m in members) > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES
                ):
                    raise ApplicationBackupError(_ERROR)
                manifest = json.loads(
                    bundle.read("manifest.json"), object_pairs_hook=_unique_pairs
                )
                if (
                    not isinstance(manifest, dict)
                    or set(manifest) != {"format", "version", "consistency",
                                        "cross_store_consistency_verified", "entries",
                                        "absent", "excluded"}
                    or manifest["format"] != FORMAT or type(manifest["version"]) is not int
                    or manifest["version"] != VERSION
                    or manifest["consistency"] != "cooperative_locked_cut_registered_user_stores"
                    or type(manifest["cross_store_consistency_verified"]) is not bool
                    or (stores is not None and manifest["cross_store_consistency_verified"])
                    or manifest["excluded"] != ["credentials", "vendor_state", "simulation_runs", "runtime"]
                    or not isinstance(manifest["entries"], list)
                    or not isinstance(manifest["absent"], list)
                ):
                    raise ApplicationBackupError(_ERROR)
                entry_names = [e["member"] for e in manifest["entries"]]
                expected = allowed - {"manifest.json"}
                if (
                    len(set(entry_names)) != len(entry_names)
                    or set(entry_names) != set(names) - {"manifest.json"}
                    or len(set(manifest["absent"])) != len(manifest["absent"])
                    or set(manifest["absent"]) != expected - set(entry_names)
                ):
                    raise ApplicationBackupError(_ERROR)
                with tempfile.TemporaryDirectory(prefix=".edge-restore-", dir=target.parent) as temp:
                    staging = Path(temp) / "application"
                    staging.mkdir()
                    (staging / "data").mkdir()
                    (staging / "settings").mkdir()
                    for entry in manifest["entries"]:
                        member = entry["member"]
                        spec = specs.get(Path(member).name) if member.startswith("data/") else None
                        keys = {"member", "bytes", "sha256"} | (
                            {"application_id", "schema_version"} if spec else set()
                        )
                        if (
                            set(entry) != keys or type(entry["bytes"]) is not int
                            or not 0 < entry["bytes"] <= (MAX_STORE_BYTES if spec else 4096)
                            or bundle.getinfo(member).file_size != entry["bytes"]
                            or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64
                            or (spec and (
                                type(entry["application_id"]) is not int
                                or type(entry["schema_version"]) is not int
                                or entry["application_id"] != spec.application_id
                                or entry["schema_version"] != spec.schema_version
                            ))
                        ):
                            raise ApplicationBackupError(_ERROR)
                        output = staging / member
                        with bundle.open(member) as reader, output.open("xb") as writer:
                            remaining = entry["bytes"]
                            while remaining:
                                chunk = reader.read(min(1024 * 1024, remaining))
                                if not chunk:
                                    raise ApplicationBackupError(_ERROR)
                                writer.write(chunk)
                                remaining -= len(chunk)
                            if reader.read(1):
                                raise ApplicationBackupError(_ERROR)
                            writer.flush()
                            os.fsync(writer.fileno())
                        if _hash(output) != entry["sha256"]:
                            raise ApplicationBackupError(_ERROR)
                        if spec:
                            _validate_database(output, spec)
                        else:
                            _settings(output.read_bytes())
                    check_local_path(target)
                    _publish_directory(staging, target)
                return manifest
    except (OSError, sqlite3.Error, EdgeError, SettingsError, ValueError, TypeError, KeyError,
            AttributeError, zipfile.BadZipFile, RuntimeError):
        raise ApplicationBackupError(_ERROR) from None
