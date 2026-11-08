"""Separate local append-only storage for user-authored watchlist notes."""

from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from edge.application_storage_lock import StorageLockError, storage_write_lock
from edge.watchlist import (
    WatchlistDraft,
    WatchlistEntry,
    WatchlistInputError,
    WatchlistNotFoundError,
    WatchlistStorageError,
    WatchlistSummary,
    normalize_created_at,
    validate_draft,
)

APPLICATION_ID = 0x4544574C
SCHEMA_VERSION = 1
_SCHEMA = """CREATE TABLE watchlist_entries (
    watchlist_id TEXT PRIMARY KEY NOT NULL CHECK (length(watchlist_id) = 32),
    created_at TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance = 'user-authored'),
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL,
    notes TEXT NOT NULL,
    horizon TEXT CHECK (horizon IS NULL OR horizon IN ('both', 'swing', 'longterm'))
) STRICT"""
_INSERT = """INSERT INTO watchlist_entries (
    watchlist_id, created_at, provenance, symbol, company_name, notes, horizon
) VALUES (?, ?, ?, ?, ?, ?, ?)"""
_STORAGE_MESSAGE = (
    "Cannot safely access the local watchlist; it has not been reset or migrated."
)


def _check_id(watchlist_id: object) -> None:
    if (
        not isinstance(watchlist_id, str)
        or re.fullmatch(r"[a-f0-9]{32}", watchlist_id) is None
    ):
        raise WatchlistInputError(
            "watchlist_id must be a lowercase hexadecimal 32-character identifier."
        )


def _check_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise WatchlistStorageError(_STORAGE_MESSAGE)
    objects = {
        (row["type"], row["name"], row["tbl_name"], row["sql"])
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
        )
    }
    if objects != {
        ("table", "watchlist_entries", "watchlist_entries", _SCHEMA),
        ("index", "sqlite_autoindex_watchlist_entries_1", "watchlist_entries", None),
    }:
        raise WatchlistStorageError(_STORAGE_MESSAGE)
    if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise WatchlistStorageError(_STORAGE_MESSAGE)


def _parse_row(row: sqlite3.Row) -> WatchlistEntry:
    """Reject malformed or manually changed records rather than normalizing them."""
    try:
        _check_id(row["watchlist_id"])
        created_at = datetime.fromisoformat(row["created_at"])
        created_at = normalize_created_at(created_at)
        if (
            created_at.isoformat(timespec="microseconds") != row["created_at"]
            or row["provenance"] != "user-authored"
        ):
            raise ValueError
        draft = WatchlistDraft(
            symbol=row["symbol"],
            company_name=row["company_name"],
            notes=row["notes"],
            horizon=row["horizon"],
        )
        if validate_draft(draft) != draft:
            raise ValueError
        return WatchlistEntry(row["watchlist_id"], created_at, "user-authored", draft)
    except (
        WatchlistInputError,
        TypeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        raise WatchlistStorageError(_STORAGE_MESSAGE) from None


class WatchlistStore:
    """Short-lived connections to root/watchlist.sqlite3; reads never initialize it."""

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.root = root.resolve()
        self.database = self.root / "watchlist.sqlite3"
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    @contextmanager
    def _connect(
        self,
        *,
        write: bool = False,
        database: Path | None = None,
        initialize: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            mode = "rw" if write else "ro"
            database = self.database if database is None else database
            connection = sqlite3.connect(
                f"{database.as_uri()}?mode={mode}", uri=True, timeout=10
            )
            connection.row_factory = sqlite3.Row
            if initialize:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                connection.execute(_SCHEMA)
            else:
                _check_schema(connection)
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                    _check_schema(connection)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            raise WatchlistStorageError(_STORAGE_MESSAGE) from None
        finally:
            if connection is not None:
                connection.close()

    def _exists(self) -> bool:
        try:
            return self.database.exists()
        except OSError:
            raise WatchlistStorageError(_STORAGE_MESSAGE) from None

    def _initialize(self, parameters: tuple[object, ...]) -> bool:
        """Publish a complete first entry atomically without replacing any target."""
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".watchlist-", suffix=".sqlite3", dir=self.root
            )
            temporary = Path(name)
            os.close(descriptor)
            with self._connect(
                write=True, database=temporary, initialize=True
            ) as connection:
                connection.execute(_INSERT, parameters)
            try:
                os.link(temporary, self.database)
            except FileExistsError:
                return False
            return True
        except OSError:
            raise WatchlistStorageError(_STORAGE_MESSAGE) from None
        finally:
            if temporary is not None:
                for suffix in ("-journal", "-wal", "-shm", ""):
                    try:
                        Path(str(temporary) + suffix).unlink(missing_ok=True)
                    except OSError:
                        raise WatchlistStorageError(_STORAGE_MESSAGE) from None

    def create(self, draft: WatchlistDraft) -> WatchlistEntry:
        draft = validate_draft(draft)
        created_at = normalize_created_at(self._clock())
        entry = WatchlistEntry(uuid4().hex, created_at, "user-authored", draft)
        parameters = (
            entry.watchlist_id,
            created_at.isoformat(timespec="microseconds"),
            entry.provenance,
            draft.symbol,
            draft.company_name,
            draft.notes,
            draft.horizon,
        )
        try:
            with storage_write_lock(self.root.parent):
                if not self._exists() and self._initialize(parameters):
                    return entry
                with self._connect(write=True) as connection:
                    connection.execute(_INSERT, parameters)
        except StorageLockError:
            raise WatchlistStorageError(_STORAGE_MESSAGE) from None
        return entry

    def list_entries(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[WatchlistSummary]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise WatchlistInputError("limit must be an integer from 1 to 100.")
        if type(offset) is not int or offset < 0:
            raise WatchlistInputError("offset must be a nonnegative integer.")
        if not self._exists():
            return []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM watchlist_entries "
                "ORDER BY created_at DESC, watchlist_id DESC LIMIT ? OFFSET ?",
                (limit, min(offset, 2**63 - 1)),
            )
            entries = [_parse_row(row) for row in rows]
        return [
            WatchlistSummary(
                entry.watchlist_id,
                entry.created_at,
                entry.draft.symbol,
                entry.draft.company_name,
                entry.draft.horizon,
            )
            for entry in entries
        ]

    def get(self, watchlist_id: str) -> WatchlistEntry:
        _check_id(watchlist_id)
        if self._exists():
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM watchlist_entries WHERE watchlist_id=?",
                    (watchlist_id,),
                ).fetchone()
                if row is not None:
                    return _parse_row(row)
        raise WatchlistNotFoundError("Watchlist item not found.")
