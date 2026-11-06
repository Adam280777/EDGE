"""Local append-only journal API. The SQLite file is not tamper-proof."""

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from edge.journal import (
    Horizon,
    JournalDraft,
    JournalEntry,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    JournalSummary,
    SourceReference,
    _check_horizon,
    _check_id,
    _draft_document,
    _utc,
    validate_draft,
)
from edge.validation import canonical_json

APPLICATION_ID = 0x45444A52
SCHEMA_VERSION = 1
_SCHEMA = """CREATE TABLE journal_entries (
    entry_id TEXT PRIMARY KEY NOT NULL,
    created_at TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance = 'user-authored'),
    horizon TEXT NOT NULL CHECK (horizon IN ('swing', 'long-term')),
    symbol TEXT NOT NULL,
    company_name TEXT NOT NULL,
    title TEXT NOT NULL,
    research_date TEXT NOT NULL,
    thesis TEXT NOT NULL,
    bull_case TEXT NOT NULL,
    bear_case TEXT NOT NULL,
    catalysts TEXT NOT NULL,
    invalidation TEXT NOT NULL,
    sources_json TEXT NOT NULL
)"""
_INSERT = """INSERT INTO journal_entries (
    entry_id, created_at, provenance, horizon, symbol, company_name, title,
    research_date, thesis, bull_case, bear_case, catalysts, invalidation, sources_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_STORAGE_MESSAGE = (
    "Cannot safely access the research journal; it has not been reset or migrated."
)


def _check_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise JournalStorageError(_STORAGE_MESSAGE)
    objects = {
        (row["type"], row["name"], row["tbl_name"], row["sql"])
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
        )
    }
    if objects != {
        ("table", "journal_entries", "journal_entries", _SCHEMA),
        ("index", "sqlite_autoindex_journal_entries_1", "journal_entries", None),
    }:
        raise JournalStorageError(_STORAGE_MESSAGE)
    if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise JournalStorageError(_STORAGE_MESSAGE)


def _parse_row(row: sqlite3.Row) -> JournalEntry:
    """Do not silently normalize damaged or manually changed stored evidence."""
    try:
        _check_id(row["entry_id"])
        created_at = datetime.fromisoformat(row["created_at"])
        if (
            _utc(created_at).isoformat(timespec="microseconds") != row["created_at"]
            or row["provenance"] != "user-authored"
        ):
            raise ValueError
        sources_document = json.loads(row["sources_json"])
        if not isinstance(sources_document, list) or len(sources_document) > 20:
            raise ValueError
        sources = []
        for source in sources_document:
            if not isinstance(source, dict) or set(source) != {
                "label",
                "url",
                "source_date",
            }:
                raise ValueError
            sources.append(
                SourceReference(
                    source["label"],
                    source["url"],
                    date.fromisoformat(source["source_date"])
                    if source["source_date"] is not None
                    else None,
                )
            )
        draft = JournalDraft(
            horizon=row["horizon"],
            symbol=row["symbol"],
            company_name=row["company_name"],
            title=row["title"],
            research_date=date.fromisoformat(row["research_date"]),
            thesis=row["thesis"],
            bull_case=row["bull_case"],
            bear_case=row["bear_case"],
            catalysts=row["catalysts"],
            invalidation=row["invalidation"],
            sources=tuple(sources),
        )
        if (
            validate_draft(draft) != draft
            or draft.research_date.isoformat() != row["research_date"]
            or canonical_json(_draft_document(draft)["sources"]) != row["sources_json"]
        ):
            raise ValueError
        return JournalEntry(row["entry_id"], created_at, "user-authored", draft)
    except (JournalInputError, TypeError, ValueError, OverflowError, RecursionError):
        raise JournalStorageError(_STORAGE_MESSAGE) from None


class JournalStore:
    """Short-lived connections to root/research-journal.sqlite3; no read-time setup."""

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.root = root.resolve()
        self.database = self.root / "research-journal.sqlite3"
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
                # Check before acquiring a write lock, then again within the transaction.
                _check_schema(connection)
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                    _check_schema(connection)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            raise JournalStorageError(_STORAGE_MESSAGE) from None
        finally:
            if connection is not None:
                connection.close()  # Also rolls back a failed write transaction.

    def _initialize(self, parameters: tuple[object, ...]) -> bool:
        """Publish a complete first entry, or let the caller append to a race winner.

        A same-directory hard link is atomic and refuses an existing destination
        on Windows and POSIX. Never use replacing rename or remove the target.
        Unsupported hard links fail closed without a fallback publication method.
        """
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".research-journal-", suffix=".sqlite3", dir=self.root
            )
            temporary = Path(name)
            os.close(descriptor)
            with self._connect(
                write=True, database=temporary, initialize=True
            ) as connection:
                connection.execute(_INSERT, parameters)
            # Commit and close before publishing: readers never see a partial DB,
            # and Windows has no open SQLite handles on the temporary file.
            try:
                os.link(temporary, self.database)
            except FileExistsError:
                return False
            return True
        except OSError:
            raise JournalStorageError(_STORAGE_MESSAGE) from None
        finally:
            if temporary is not None:
                # Only this attempt's uniquely reserved files, never target files
                # or a glob that could include another initializer's evidence.
                for suffix in ("-journal", "-wal", "-shm", ""):
                    try:
                        Path(str(temporary) + suffix).unlink(missing_ok=True)
                    except OSError:
                        raise JournalStorageError(_STORAGE_MESSAGE) from None

    def create(self, draft: JournalDraft) -> JournalEntry:
        draft = validate_draft(draft)
        created_at = _utc(
            self._clock()
        )  # Exactly one clock sample, before any file writes.
        entry = JournalEntry(uuid4().hex, created_at, "user-authored", draft)
        document = _draft_document(draft)
        parameters = (
            entry.entry_id,
            created_at.isoformat(timespec="microseconds"),
            entry.provenance,
            draft.horizon,
            draft.symbol,
            draft.company_name,
            draft.title,
            document["research_date"],
            draft.thesis,
            draft.bull_case,
            draft.bear_case,
            draft.catalysts,
            draft.invalidation,
            canonical_json(document["sources"]),
        )
        if not self._exists() and self._initialize(parameters):
            return entry
        # Another initializer may have won publication. Validate its database and
        # append the same entry (no new ID or clock sample), never replace it.
        with self._connect(write=True) as connection:
            connection.execute(_INSERT, parameters)
        return entry

    def list_entries(
        self, *, horizon: Horizon | None = None, limit: int = 50, offset: int = 0
    ) -> list[JournalSummary]:
        if horizon is not None:
            _check_horizon(horizon)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise JournalInputError("limit must be an integer from 1 to 100.")
        if type(offset) is not int or offset < 0:
            raise JournalInputError("offset must be a nonnegative integer.")
        if not self._exists():
            return []
        with self._connect() as connection:
            sql = "SELECT * FROM journal_entries"
            parameters: tuple[object, ...] = ()
            if horizon is not None:
                sql += " WHERE horizon=?"
                parameters = (horizon,)
            sql += " ORDER BY created_at DESC, entry_id DESC LIMIT ? OFFSET ?"
            # SQLite integers are signed 64-bit; larger offsets necessarily return no rows.
            rows = connection.execute(sql, (*parameters, limit, min(offset, 2**63 - 1)))
            entries = [_parse_row(row) for row in rows]
        return [
            JournalSummary(
                entry.entry_id,
                entry.created_at,
                entry.draft.horizon,
                entry.draft.symbol,
                entry.draft.title,
                entry.draft.research_date,
            )
            for entry in entries
        ]

    def _exists(self) -> bool:
        try:
            return self.database.exists()
        except OSError:
            raise JournalStorageError(_STORAGE_MESSAGE) from None

    def get(self, entry_id: str) -> JournalEntry:
        _check_id(entry_id)
        if self._exists():
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM journal_entries WHERE entry_id=?", (entry_id,)
                ).fetchone()
                if row is not None:
                    return _parse_row(row)
        raise JournalNotFoundError("Journal entry not found.")
