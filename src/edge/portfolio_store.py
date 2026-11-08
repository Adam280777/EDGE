"""Dedicated append-only manual portfolio SQLite store, with no-clobber creation."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import fields, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from edge.application_storage_lock import StorageLockError, storage_write_lock
from edge.decision_contracts import (
    DecisionInputError,
    FxRate,
    ManualProvenance,
    PriceQuote,
)
from edge.portfolio import (
    Buy,
    CashDeposit,
    CashExchange,
    CashOpening,
    CashWithdrawal,
    Correction,
    Fee,
    Instrument,
    LedgerEntry,
    LedgerState,
    OpeningHolding,
    PortfolioEvent,
    PortfolioInputError,
    PortfolioSnapshot,
    PortfolioStorageError,
    Sell,
    _id,
    replay,
    snapshot,
    utc_timestamp,
    validate_event,
)

APPLICATION_ID = 0x45445046
SCHEMA_VERSION = 1
_SCHEMA = """CREATE TABLE portfolio_events (
    sequence INTEGER PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE CHECK (length(event_id) = 32),
    recorded_at TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    provenance TEXT NOT NULL CHECK (provenance = 'user-declared'),
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL
) STRICT"""
_UPDATE_GUARD = """CREATE TRIGGER portfolio_no_update BEFORE UPDATE ON portfolio_events
BEGIN SELECT RAISE(ABORT, 'portfolio is append-only'); END"""
_DELETE_GUARD = """CREATE TRIGGER portfolio_no_delete BEFORE DELETE ON portfolio_events
BEGIN SELECT RAISE(ABORT, 'portfolio is append-only'); END"""
_INSERT = """INSERT INTO portfolio_events (
    event_id, recorded_at, effective_at, provenance, event_type, payload
) VALUES (?, ?, ?, ?, ?, ?)"""
_MESSAGE = "Cannot safely access portfolio storage; it has not been reset or migrated."
_TYPES = {
    cls.__name__: cls
    for cls in (
        OpeningHolding,
        CashOpening,
        Buy,
        Sell,
        Fee,
        CashDeposit,
        CashWithdrawal,
        CashExchange,
        Correction,
    )
}


def _timestamp(value: datetime) -> str:
    return utc_timestamp(value).isoformat(timespec="microseconds")


def _payload(event: PortfolioEvent) -> str:
    data: dict[str, object] = {}
    for field in fields(event):
        value = getattr(event, field.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif isinstance(value, datetime):
            value = _timestamp(value)
        elif isinstance(value, date):
            value = value.isoformat()
        elif isinstance(value, Instrument):
            value = {
                "symbol": value.symbol,
                "market": value.market,
                "currency": value.currency,
            }
        elif isinstance(value, FxRate):
            value = {
                "from_currency": value.from_currency,
                "to_currency": value.to_currency,
                "rate": str(value.rate),
                "as_of": _timestamp(value.as_of),
                "provenance": {
                    "reference": value.provenance.reference,
                    "declared_at": _timestamp(value.provenance.declared_at),
                    "kind": value.provenance.kind,
                },
            }
        data[field.name] = value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parameters(entry: LedgerEntry) -> tuple[object, ...]:
    return (
        entry.event_id,
        _timestamp(entry.recorded_at),
        _timestamp(entry.event.effective_at),
        entry.provenance,
        type(entry.event).__name__,
        _payload(entry.event),
    )


def _parse(row: sqlite3.Row) -> LedgerEntry:
    """Reject noncanonical encodings and invalid declarations instead of fixing data."""
    try:
        event_type = _TYPES[row["event_type"]]
        data = json.loads(row["payload"])
        if type(data) is not dict or set(data) != {
            field.name for field in fields(event_type)
        }:
            raise ValueError
        for key in ("amount", "price", "fee", "total_cost"):
            if key in data:
                if not isinstance(data[key], str):
                    raise ValueError
                data[key] = Decimal(data[key])
        data["effective_at"] = datetime.fromisoformat(data["effective_at"])
        if "acquired_on" in data:
            data["acquired_on"] = date.fromisoformat(data["acquired_on"])
        if "instrument" in data:
            if type(data["instrument"]) is not dict:
                raise ValueError
            data["instrument"] = Instrument(**data["instrument"])
        if "fx_rate" in data:
            fx = data["fx_rate"]
            if type(fx) is not dict or not isinstance(fx["rate"], str):
                raise ValueError
            provenance = fx["provenance"]
            fx["rate"] = Decimal(fx["rate"])
            fx["as_of"] = datetime.fromisoformat(fx["as_of"])
            provenance["declared_at"] = datetime.fromisoformat(
                provenance["declared_at"]
            )
            fx["provenance"] = ManualProvenance(**provenance)
            data["fx_rate"] = FxRate(**fx)
        event = event_type(**data)
        validate_event(event)
        entry = LedgerEntry(
            row["event_id"],
            datetime.fromisoformat(row["recorded_at"]),
            event,
            row["provenance"],
        )
        if _parameters(entry) != (
            row["event_id"],
            row["recorded_at"],
            row["effective_at"],
            row["provenance"],
            row["event_type"],
            row["payload"],
        ):
            raise ValueError
        return entry
    except (
        PortfolioInputError,
        DecisionInputError,
        KeyError,
        TypeError,
        ValueError,
        ArithmeticError,
        OverflowError,
        RecursionError,
    ):
        raise PortfolioStorageError(_MESSAGE) from None


def _schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise PortfolioStorageError(_MESSAGE)
    objects = {
        tuple(row)
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master"
        )
    }
    if objects != {
        ("table", "portfolio_events", "portfolio_events", _SCHEMA),
        ("index", "sqlite_autoindex_portfolio_events_1", "portfolio_events", None),
        ("trigger", "portfolio_no_update", "portfolio_events", _UPDATE_GUARD),
        ("trigger", "portfolio_no_delete", "portfolio_events", _DELETE_GUARD),
    } or [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise PortfolioStorageError(_MESSAGE)


def validate_database(connection: sqlite3.Connection) -> None:
    """Read-only recovery validation: exact schema, every row, canonical FIFO replay.

    Accepts an already-open SQLite connection. Does not mutate schema, version or
    contents; temporarily selects sqlite3.Row results and restores caller state.
    """
    prior_factory = connection.row_factory
    try:
        connection.row_factory = sqlite3.Row
        _schema(connection)
        PortfolioStore._entries(connection)
    except (sqlite3.Error, OSError):
        raise PortfolioStorageError(_MESSAGE) from None
    finally:
        connection.row_factory = prior_factory


class PortfolioStore:
    """root/portfolio.sqlite3 only. Reads never create files or directories.

    append_many is one transaction, including replay validation; first creation
    publishes a fully committed temporary database by hard link without replacing
    any existing target. Unknown databases are refused, never migrated or reset.
    Writers cooperate with application recovery via root.parent's storage lock;
    the application root must exist (normal layout application_root/data).
    """

    def __init__(
        self, root: Path, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.root = root.resolve()
        self.database = self.root / "portfolio.sqlite3"
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
            target = self.database if database is None else database
            connection = sqlite3.connect(
                f"{target.as_uri()}?mode={'rw' if write else 'ro'}",
                uri=True,
                timeout=10,
            )
            connection.row_factory = sqlite3.Row
            if write:
                connection.execute("BEGIN IMMEDIATE")
            if initialize:
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                for sql in (_SCHEMA, _UPDATE_GUARD, _DELETE_GUARD):
                    connection.execute(sql)
            else:
                _schema(connection)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            raise PortfolioStorageError(_MESSAGE) from None
        finally:
            if connection is not None:
                connection.close()

    def _exists(self) -> bool:
        try:
            if self.database.is_symlink():
                raise PortfolioStorageError(_MESSAGE)
            return self.database.exists()
        except OSError:
            raise PortfolioStorageError(_MESSAGE) from None

    @staticmethod
    def _entries(connection: sqlite3.Connection) -> tuple[LedgerEntry, ...]:
        rows = tuple(
            connection.execute("SELECT * FROM portfolio_events ORDER BY sequence")
        )
        if any(row["sequence"] != index for index, row in enumerate(rows, start=1)):
            raise PortfolioStorageError(_MESSAGE)
        entries = tuple(_parse(row) for row in rows)
        try:
            replay(entries)
        except PortfolioInputError:
            raise PortfolioStorageError(_MESSAGE) from None
        return entries

    def list_entries(self) -> tuple[LedgerEntry, ...]:
        if not self._exists():
            return ()
        with self._connect() as connection:
            return self._entries(connection)

    def state(self) -> LedgerState:
        return replay(self.list_entries())

    def snapshot(
        self,
        *,
        quotes: Mapping[Instrument, PriceQuote],
        fx_rates: Iterable[FxRate],
        as_of: datetime,
        max_age: timedelta,
    ) -> PortfolioSnapshot:
        return snapshot(
            self.list_entries(),
            quotes=quotes,
            fx_rates=fx_rates,
            as_of=as_of,
            max_age=max_age,
        )

    def append(
        self, event: PortfolioEvent, *, event_id: str | None = None
    ) -> LedgerEntry:
        """Supply a stable UUID hex for safe retries; different UUIDs are distinct buys."""
        return self.append_many(
            (event,),
            event_ids=None if event_id is None else (event_id,),
        )[0]

    def append_many(
        self,
        events: Iterable[PortfolioEvent],
        *,
        event_ids: Iterable[str] | None = None,
    ) -> tuple[LedgerEntry, ...]:
        events = tuple(events)
        if not events or len(events) > 1000:
            raise PortfolioInputError("append_many requires 1..1000 explicit events.")
        for event in events:
            validate_event(event)
        events = tuple(
            replace(event, effective_at=utc_timestamp(event.effective_at))
            for event in events
        )
        ids = (
            tuple(uuid4().hex for _ in events)
            if event_ids is None
            else tuple(event_ids)
        )
        for event_id in ids:
            _id(event_id)
        if len(ids) != len(events) or len(set(ids)) != len(ids):
            raise PortfolioInputError(
                "One distinct UUID event_id is required per event."
            )
        recorded_at = utc_timestamp(self._clock())
        entries = tuple(
            LedgerEntry(event_id, recorded_at, event)
            for event_id, event in zip(ids, events, strict=True)
        )
        try:
            with storage_write_lock(self.root.parent):
                return self._append_entries(entries)
        except StorageLockError:
            raise PortfolioStorageError(_MESSAGE) from None

    def _append_entries(
        self, entries: tuple[LedgerEntry, ...]
    ) -> tuple[LedgerEntry, ...]:
        """Caller holds the cross-store lock through checks, validation and commit."""
        if not self._exists() and self._initialize(entries):
            return entries
        with self._connect(write=True) as connection:
            prior = self._entries(connection)
            existing = {entry.event_id: entry for entry in prior}
            if any(entry.event_id in existing for entry in entries):
                if not all(
                    entry.event_id in existing
                    and existing[entry.event_id].event == entry.event
                    for entry in entries
                ):
                    raise PortfolioInputError(
                        "UUID retry conflicts with existing immutable events."
                    )
                return tuple(existing[entry.event_id] for entry in entries)
            replay(prior + entries)
            connection.executemany(
                _INSERT, tuple(_parameters(entry) for entry in entries)
            )
        return entries

    def _initialize(self, entries: tuple[LedgerEntry, ...]) -> bool:
        # Validate before creating even a directory. Failed first writes leave no DB.
        replay(entries)
        temporary: Path | None = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(
                prefix=".portfolio-",
                suffix=".sqlite3",
                dir=self.root,
            )
            temporary = Path(name)
            os.close(descriptor)
            with self._connect(
                write=True, database=temporary, initialize=True
            ) as connection:
                connection.executemany(
                    _INSERT, tuple(_parameters(entry) for entry in entries)
                )
            try:
                os.link(temporary, self.database)
            except FileExistsError:
                return False
            return True
        except OSError:
            raise PortfolioStorageError(_MESSAGE) from None
        finally:
            if temporary is not None:
                for suffix in ("-journal", "-wal", "-shm", ""):
                    try:
                        Path(str(temporary) + suffix).unlink(missing_ok=True)
                    except OSError:
                        raise PortfolioStorageError(_MESSAGE) from None
