"""Journal-only, versioned snapshots; never import into or replace a journal.

The archive hash detects accidental damage, not malicious tampering. Publication
requires same-filesystem hard links, as does JournalStore's first save.
"""

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import time
import zipfile
from contextlib import closing
from pathlib import Path
from typing import BinaryIO

from edge.journal import JournalError
from edge.journal_store import APPLICATION_ID, SCHEMA_VERSION, _check_schema, _parse_row
from edge.validation import canonical_json

FORMAT = "edge.research-journal-backup"
VERSION = 1
DATABASE = "research-journal.sqlite3"
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 16384
# Stored DB + manifest + generous ZIP headers, never an unbounded directory parse.
MAX_ARCHIVE_BYTES = MAX_SNAPSHOT_BYTES + MAX_MANIFEST_BYTES + 65536
BACKUP_TIMEOUT_SECONDS = 10
_CHUNK = 1024 * 1024
_BACKUP_ERROR = (
    "Journal backup failed: use a valid journal and an absent destination file "
    "in an existing writable directory. No existing file was replaced."
)
_RESTORE_ERROR = (
    "Journal restore failed: use a valid supported backup and a new or empty "
    "journal directory with an existing parent. No existing journal was replaced."
)


class JournalBackupError(JournalError):
    """A snapshot could not be safely validated or published."""


def _absent(path: Path) -> bool:
    # Unlike Path.exists(), this also detects dangling symlinks.
    return not os.path.lexists(path)


def _check_path(path: Path) -> None:
    """Reject symlinks and ALL Windows reparse points, including junctions.

    Check lexical ancestors before resolving anything: resolve() would hide the
    redirection being rejected. Missing components are allowed for new targets.
    This is a path-based check, not protection against hostile filesystem races.
    """
    path = path.absolute()
    for component in (*reversed(path.parents), path):
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0)
            & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise JournalBackupError(_RESTORE_ERROR)


def _validate(database: Path) -> int:
    """Validate all records, not just a page or the application/version headers."""
    with closing(sqlite3.connect(
        f"{database.as_uri()}?mode=ro&immutable=1", uri=True, timeout=10
    )) as connection:
        # Only private, closed snapshots are immutable; NEVER the running source.
        connection.row_factory = sqlite3.Row
        if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise JournalBackupError(_RESTORE_ERROR)
        _check_schema(connection)
        count = 0
        for row in connection.execute("SELECT * FROM journal_entries"):
            _parse_row(row)
            count += 1
        return count


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _sync(path: Path) -> None:
    # Windows _commit/fsync requires a writable descriptor. Private files only.
    with path.open("r+b") as stream:
        os.fsync(stream.fileno())


def _snapshot(source: Path, destination: Path) -> None:
    """Use SQLite's online backup API, including committed WAL content.

    A deadline also bounds SQLite's otherwise unbounded busy/restart retries.
    """
    deadline = time.monotonic() + BACKUP_TIMEOUT_SECONDS

    def progress(status: int, remaining: int, total: int) -> None:
        if time.monotonic() >= deadline:
            raise JournalBackupError(_BACKUP_ERROR)

    with closing(sqlite3.connect(
        f"{source.as_uri()}?mode=ro", uri=True, timeout=0.1
    )) as reader, closing(sqlite3.connect(destination)) as writer:
        reader.execute("PRAGMA query_only=ON")
        # Check size before allocating a potentially oversized private snapshot.
        pages = reader.execute("PRAGMA page_count").fetchone()[0]
        page_size = reader.execute("PRAGMA page_size").fetchone()[0]
        if pages * page_size > MAX_SNAPSHOT_BYTES:
            raise JournalBackupError(_BACKUP_ERROR)
        reader.backup(writer, pages=128, progress=progress, sleep=0.05)
        # A WAL source can pass its journal mode to the snapshot. Normalize ONLY
        # the private copy to a standalone DB before packaging or publishing it.
        if writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
            raise JournalBackupError(_BACKUP_ERROR)


def backup_journal(journal_root: Path, destination: Path) -> dict[str, object]:
    """Publish one complete archive, refusing any existing destination.

    The destination's parent must already exist. No source SQL writes, raw-file
    copies, provider calls, migration or journal initialization are performed.
    """
    try:
        source = journal_root.absolute() / DATABASE
        destination = destination.absolute()
        _check_path(source)
        _check_path(destination)
        if (
            not source.is_file()
            or not _absent(destination)
            or not destination.parent.is_dir()
        ):
            raise JournalBackupError(_BACKUP_ERROR)
        with tempfile.TemporaryDirectory(
            prefix=".edge-journal-backup-", dir=destination.parent
        ) as directory:
            staging = Path(directory)
            snapshot = staging / DATABASE
            _snapshot(source, snapshot)
            size = snapshot.stat().st_size
            if not 0 < size <= MAX_SNAPSHOT_BYTES:
                raise JournalBackupError(_BACKUP_ERROR)
            count = _validate(snapshot)
            manifest: dict[str, object] = {
                "format": FORMAT,
                "version": VERSION,
                "application_id": APPLICATION_ID,
                "schema_version": SCHEMA_VERSION,
                "snapshot_bytes": size,
                "snapshot_sha256": _digest(snapshot),
                "entry_count": count,
            }
            archive = staging / "backup.zip"
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
                bundle.writestr("manifest.json", canonical_json(manifest))
                bundle.write(snapshot, DATABASE)
            _sync(archive)
            # Atomic and no-clobber on Windows/POSIX. Unsupported FS fails closed.
            _check_path(archive)
            _check_path(destination)
            os.link(archive, destination)
        return manifest
    except (OSError, sqlite3.Error, JournalError, ValueError, zipfile.BadZipFile):
        raise JournalBackupError(_BACKUP_ERROR) from None


def _unpack(backup: Path, snapshot: Path) -> dict[str, object]:
    """Read only fixed members; never extract archive paths."""
    # Check the SAME opened file that ZipFile will parse, before its constructor
    # reads/allocates the central directory (infolist() is already too late).
    with backup.open("rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= MAX_ARCHIVE_BYTES
        ):
            raise JournalBackupError(_RESTORE_ERROR)
        return _unpack_bounded(stream, snapshot)


def _unpack_bounded(stream: BinaryIO, snapshot: Path) -> dict[str, object]:
    with zipfile.ZipFile(stream, "r") as bundle:
        members = bundle.infolist()
        if (
            len(members) != 2
            or {item.filename for item in members} != {"manifest.json", DATABASE}
            or any(
                item.compress_type != zipfile.ZIP_STORED
                or item.flag_bits & 1
                or item.compress_size != item.file_size
                for item in members
            )
            or not 0 < bundle.getinfo("manifest.json").file_size <= MAX_MANIFEST_BYTES
            or not 0 < bundle.getinfo(DATABASE).file_size <= MAX_SNAPSHOT_BYTES
        ):
            raise JournalBackupError(_RESTORE_ERROR)
        raw_manifest = bundle.read("manifest.json")
        manifest = json.loads(raw_manifest)
        if (
            not isinstance(manifest, dict)
            or set(manifest) != {
                "format", "version", "application_id", "schema_version",
                "snapshot_bytes", "snapshot_sha256", "entry_count",
            }
            or canonical_json(manifest).encode("utf-8") != raw_manifest
            or manifest["format"] != FORMAT
            or type(manifest["version"]) is not int
            or manifest["version"] != VERSION
            or type(manifest["application_id"]) is not int
            or manifest["application_id"] != APPLICATION_ID
            or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != SCHEMA_VERSION
            or type(manifest["entry_count"]) is not int
            or manifest["entry_count"] < 0
            or type(manifest["snapshot_bytes"]) is not int
            or not 0 < manifest["snapshot_bytes"] <= MAX_SNAPSHOT_BYTES
            or manifest["snapshot_bytes"] != bundle.getinfo(DATABASE).file_size
            or not isinstance(manifest["snapshot_sha256"], str)
            or len(manifest["snapshot_sha256"]) != 64
        ):
            raise JournalBackupError(_RESTORE_ERROR)
        digest = hashlib.sha256()
        size = 0
        with bundle.open(DATABASE) as reader, snapshot.open("xb") as writer:
            while chunk := reader.read(_CHUNK):
                size += len(chunk)
                if size > manifest["snapshot_bytes"]:
                    raise JournalBackupError(_RESTORE_ERROR)
                digest.update(chunk)
                writer.write(chunk)
        if (
            size != manifest["snapshot_bytes"]
            or digest.hexdigest() != manifest["snapshot_sha256"]
            or _validate(snapshot) != manifest["entry_count"]
        ):
            raise JournalBackupError(_RESTORE_ERROR)
        return manifest


def restore_journal(backup: Path, journal_root: Path) -> dict[str, object]:
    """Validate then publish a standalone DB into a NEW or EMPTY root only.

    The root's parent must exist. Refuse existing journals, including empty or
    damaged DB files and dangling symlinks. Never merge, migrate, reset or delete
    target data. Clean up only this attempt's staging and newly created empty root.
    """
    created_root = False
    root = journal_root.absolute()
    try:
        _check_path(backup)
        _check_path(root)
        if (
            not backup.is_file()
            or not root.parent.is_dir()
            or root.is_symlink()
            or (not _absent(root) and (not root.is_dir() or any(root.iterdir())))
        ):
            raise JournalBackupError(_RESTORE_ERROR)
        with tempfile.TemporaryDirectory(
            prefix=".edge-journal-restore-", dir=root.parent
        ) as directory:
            snapshot = Path(directory) / DATABASE
            manifest = _unpack(backup, snapshot)
            _sync(snapshot)
            _check_path(root)
            if _absent(root):
                # Do not claim ownership of a root concurrently created by others.
                try:
                    root.mkdir()
                    created_root = True
                except FileExistsError:
                    pass
            if not root.is_dir() or any(root.iterdir()):
                raise JournalBackupError(_RESTORE_ERROR)
            _check_path(snapshot)
            _check_path(root / DATABASE)
            os.link(snapshot, root / DATABASE)
        return manifest
    except (
        OSError, sqlite3.Error, JournalError, ValueError, RecursionError,
        zipfile.BadZipFile, zipfile.LargeZipFile, EOFError,
    ):
        raise JournalBackupError(_RESTORE_ERROR) from None
    finally:
        if created_root:
            try:
                _check_path(root)
                root.rmdir()  # Only removes our empty root, never its contents.
            except (OSError, JournalBackupError):
                pass
