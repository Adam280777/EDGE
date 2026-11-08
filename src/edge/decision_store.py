"""Separate versioned append-only decision SQLite; no read-time initialization."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from edge.application_storage_lock import storage_write_lock
from edge.decision_contracts import (
    DecisionInputError,
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
    normalize_utc,
)
from edge.errors import EdgeError
from edge.research_workflows import (
    CompanyIdentity,
    DatedCatalyst,
    ResearchItem,
    ResearchRevision,
    ValuationScenario,
    validate_revision,
)
from edge.trade_planning import SavedTradePlan, SizingInputs, TradingCosts

APPLICATION_ID = 0x45444453
SCHEMA_VERSION = 1
RecordKind = Literal["risk", "plan", "research"]


class DecisionStorageError(EdgeError):
    """Unknown schema, malformed content or failed atomic operation; no reset."""


class DecisionNotFoundError(EdgeError):
    """Requested revision does not exist."""


@dataclass(frozen=True)
class RiskPolicyRevision:
    """Explicit immutable policy revision; confirmation binds to id:revision."""

    record_id: str
    revision: int
    created_at: datetime
    policy: RiskPolicy

    def __post_init__(self) -> None:
        validate_revision(self.record_id, self.revision, self.created_at)
        object.__setattr__(self, "created_at", normalize_utc(self.created_at))
        if not isinstance(self.policy, RiskPolicy):
            raise DecisionInputError("RiskPolicy required.")
        if self.policy.confirmed_at is not None:
            if self.policy.revision_id != f"{self.record_id}:{self.revision}":
                raise DecisionInputError("Confirmation must bind to this exact id:revision.")
            if self.policy.confirmed_at > self.created_at:
                raise DecisionInputError("Confirmation cannot follow the stored revision timestamp.")


DecisionRecord = RiskPolicyRevision | SavedTradePlan | ResearchRevision
_TYPES = {
    cls.__name__: cls for cls in (
        RiskPolicyRevision, SavedTradePlan, ResearchRevision, CompanyIdentity,
        ResearchItem, DatedCatalyst, ValuationScenario, SizingInputs, TradingCosts,
        FinanceMoney, FxRate, ManualProvenance, PriceQuote, RiskPolicy,
    )
}
_KINDS = {RiskPolicyRevision: "risk", SavedTradePlan: "plan", ResearchRevision: "research"}
_TABLE = """CREATE TABLE decisions (
    kind TEXT NOT NULL CHECK(kind IN ('risk','plan','research')),
    record_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY(kind, record_id, revision)
) STRICT"""
_NO_UPDATE = """CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'immutable decision revision'); END"""
_NO_DELETE = """CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'immutable decision revision'); END"""
_INSERT = "INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?)"
_MESSAGE = "Cannot safely access decisions; no reset, migration or overwrite performed."


def _encode(value: object) -> object:
    if is_dataclass(value) and type(value).__name__ in _TYPES:
        return {"type": type(value).__name__, "fields": {
            field.name: _encode(getattr(value, field.name)) for field in fields(value)
        }}
    if isinstance(value, Decimal):
        return {"decimal": str(value)}
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if type(value) is date:
        return {"date": value.isoformat()}
    if isinstance(value, tuple):
        return {"tuple": [_encode(item) for item in value]}
    if value is None or type(value) in (str, int, bool):
        return value
    raise DecisionInputError("Unsupported decision payload type.")


def _decode(value: object) -> object:
    if value is None or type(value) in (str, int, bool):
        return value
    if not isinstance(value, dict):
        raise TypeError("Invalid decision encoding.")
    if set(value) == {"decimal"}:
        if not isinstance(value["decimal"], str):
            raise ValueError
        result = Decimal(value["decimal"])
        if not result.is_finite():
            raise ValueError
        return result
    if set(value) == {"datetime"}:
        return normalize_utc(datetime.fromisoformat(value["datetime"]))
    if set(value) == {"date"}:
        return date.fromisoformat(value["date"])
    if set(value) == {"tuple"} and isinstance(value["tuple"], list):
        return tuple(_decode(item) for item in value["tuple"])
    if set(value) == {"type", "fields"} and value["type"] in _TYPES:
        cls = _TYPES[value["type"]]
        if not isinstance(value["fields"], dict) or set(value["fields"]) != {
            field.name for field in fields(cls)
        }:
            raise ValueError
        return cls(**{name: _decode(item) for name, item in value["fields"].items()})
    raise ValueError("Unknown decision encoding.")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result


def _payload(record: DecisionRecord) -> str:
    result = json.dumps(_encode(record), ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(result) > 1_000_000:
        raise DecisionInputError("Decision revision exceeds 1 MB.")
    return result


def _parse(row: sqlite3.Row) -> DecisionRecord:
    try:
        payload = row["payload"]
        if len(payload) > 1_000_000 or hashlib.sha256(payload.encode()).hexdigest() != row["digest"]:
            raise ValueError
        record = _decode(json.loads(payload, object_pairs_hook=_pairs))
        if type(record) not in _KINDS or (
            _KINDS[type(record)], record.record_id, record.revision,
            record.created_at.isoformat(timespec="microseconds"),
        ) != (row["kind"], row["record_id"], row["revision"], row["created_at"]):
            raise ValueError
        if _payload(record) != payload:
            raise ValueError
        return record
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError,
            RecursionError, DecisionInputError):
        raise DecisionStorageError(_MESSAGE) from None


def _check_schema(connection: sqlite3.Connection) -> None:
    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
    ):
        raise DecisionStorageError(_MESSAGE)
    objects = {tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master"
    )}
    if objects != {
        ("table", "decisions", "decisions", _TABLE),
        ("index", "sqlite_autoindex_decisions_1", "decisions", None),
        ("trigger", "decisions_no_update", "decisions", _NO_UPDATE),
        ("trigger", "decisions_no_delete", "decisions", _NO_DELETE),
    }:
        raise DecisionStorageError(_MESSAGE)
    if [tuple(row) for row in connection.execute("PRAGMA quick_check")] != [("ok",)]:
        raise DecisionStorageError(_MESSAGE)


def _check_next(record: DecisionRecord, previous: DecisionRecord | None,
                policy_record: RiskPolicyRevision | None) -> None:
    expected = previous.revision + 1 if previous else 1
    if record.revision != expected:
        raise DecisionInputError(f"Next immutable revision must be {expected}.")
    if previous and record.created_at < previous.created_at:
        raise DecisionInputError("Revision timestamp cannot precede the previous revision.")
    if isinstance(record, (ResearchRevision, SavedTradePlan)) and previous and (
        record.identity != previous.identity or record.horizon != previous.horizon
    ):
        raise DecisionInputError("Identity and horizon cannot change within a decision chain.")
    if isinstance(record, SavedTradePlan) and record.policy.enabled and (
        not policy_record or policy_record.policy != record.policy
        or policy_record.created_at > record.created_at
    ):
        raise DecisionInputError("Plan policy confirmation is missing, stale or inconsistent.")


def validate_database(connection: sqlite3.Connection) -> None:
    """Read-only backup validator: schema, every payload and full append history.

    Connection must use sqlite3.Row. Replay implicit rowid insertion order, so
    historical plans reference the policy current WHEN appended, not today's.
    """
    _check_schema(connection)
    previous: dict[tuple[str, str], DecisionRecord] = {}
    try:
        for row in connection.execute("SELECT * FROM decisions ORDER BY rowid"):
            record = _parse(row)
            key = (_KINDS[type(record)], record.record_id)
            risk_record = None
            if isinstance(record, SavedTradePlan) and record.policy.enabled:
                policy_id, separator, revision = record.policy.revision_id.rpartition(":")
                if not separator or not revision.isdecimal():
                    raise DecisionInputError("Enabled plan needs a stored id:revision policy.")
                candidate = previous.get(("risk", policy_id))
                if isinstance(candidate, RiskPolicyRevision) and candidate.revision == int(revision):
                    risk_record = candidate
            _check_next(record, previous.get(key), risk_record)
            previous[key] = record
    except (DecisionInputError, sqlite3.Error):
        raise DecisionStorageError(_MESSAGE) from None


class DecisionStore:
    """Append risk/plan/research revisions; caller supplies IDs, revisions and dates.

    Sequential revisions are transactionally enforced per kind/id. Enabled plans
    must reference the exact most recent stored confirmed policy, never a stale
    confirmation. Historical plan reads preserve their original policy snapshot;
    reading an old plan is not permission to size/execute it under current policy.
    Hashes detect corruption, not authenticate users against malicious DB changes.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.database = self.root / "decisions.sqlite3"

    @contextmanager
    def _connect(self, *, write: bool = False, database: Path | None = None,
                 initialize: bool = False) -> Iterator[sqlite3.Connection]:
        connection = None
        try:
            path = self.database if database is None else database
            mode = "rw" if write else "ro"
            connection = sqlite3.connect(f"{path.as_uri()}?mode={mode}", uri=True, timeout=10)
            connection.row_factory = sqlite3.Row
            if write:
                connection.execute("BEGIN IMMEDIATE")
            if initialize:
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                for sql in (_TABLE, _NO_UPDATE, _NO_DELETE):
                    connection.execute(sql)
            else:
                _check_schema(connection)
            yield connection
            if write:
                connection.commit()
        except (sqlite3.Error, OSError):
            raise DecisionStorageError(_MESSAGE) from None
        finally:
            if connection is not None:
                connection.close()

    def _insert(self, connection: sqlite3.Connection, record: DecisionRecord) -> None:
        kind = _KINDS[type(record)]
        row = connection.execute(
            "SELECT * FROM decisions WHERE kind=? AND record_id=? ORDER BY revision DESC LIMIT 1",
            (kind, record.record_id),
        ).fetchone()
        previous = _parse(row) if row is not None else None
        policy_record = None
        if isinstance(record, SavedTradePlan) and record.policy.enabled:
            policy_id, separator, revision = record.policy.revision_id.rpartition(":")
            if not separator or not revision.isdecimal():
                raise DecisionInputError("Enabled plan needs a stored id:revision policy.")
            policy_row = connection.execute(
                "SELECT * FROM decisions WHERE kind='risk' AND record_id=? ORDER BY revision DESC LIMIT 1",
                (policy_id,),
            ).fetchone()
            candidate = _parse(policy_row) if policy_row else None
            if isinstance(candidate, RiskPolicyRevision) and candidate.revision == int(revision):
                policy_record = candidate
        _check_next(record, previous, policy_record)
        payload = _payload(record)
        connection.execute(_INSERT, (
            kind, record.record_id, record.revision,
            record.created_at.isoformat(timespec="microseconds"), payload,
            hashlib.sha256(payload.encode()).hexdigest(),
        ))

    def _initialize(self, record: DecisionRecord) -> bool:
        temporary = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".decisions-", suffix=".sqlite3", dir=self.root)
            temporary = Path(name)
            os.close(descriptor)
            with self._connect(write=True, database=temporary, initialize=True) as connection:
                self._insert(connection, record)
            try:
                os.link(temporary, self.database)
            except FileExistsError:
                return False
            return True
        except OSError:
            raise DecisionStorageError(_MESSAGE) from None
        finally:
            if temporary is not None:
                for suffix in ("-journal", "-wal", "-shm", ""):
                    try:
                        Path(str(temporary) + suffix).unlink(missing_ok=True)
                    except OSError:
                        raise DecisionStorageError(_MESSAGE) from None

    def append(self, record: DecisionRecord) -> DecisionRecord:
        if type(record) not in _KINDS:
            raise DecisionInputError("Only risk, plan and research revisions can be stored.")
        # Reconstruct to reject objects mutated using low-level bypasses of frozen.
        if _decode(_encode(record)) != record:
            raise DecisionInputError("Invalid immutable decision revision.")
        _payload(record)
        with storage_write_lock(self.root.parent):
            if not self.database.exists() and self._initialize(record):
                return record
            with self._connect(write=True) as connection:
                self._insert(connection, record)
        return record

    def get(self, kind: RecordKind, record_id: str, revision: int) -> DecisionRecord:
        self._kind(kind)
        # Timestamp is validated only for actual records; query IDs/revisions share limits.
        from datetime import UTC
        validate_revision(record_id, revision, datetime(2000, 1, 1, tzinfo=UTC))
        if not self.database.exists():
            raise DecisionNotFoundError("Decision revision does not exist.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM decisions WHERE kind=? AND record_id=? AND revision=?",
                (kind, record_id, revision),
            ).fetchone()
        if row is None:
            raise DecisionNotFoundError("Decision revision does not exist.")
        return _parse(row)

    @staticmethod
    def _kind(kind: RecordKind) -> None:
        if not isinstance(kind, str) or kind not in ("risk", "plan", "research"):
            raise DecisionInputError("kind must be risk, plan or research.")

    def list_records(self, kind: RecordKind, *, limit: int = 50,
                     offset: int = 0) -> tuple[DecisionRecord, ...]:
        self._kind(kind)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DecisionInputError("limit must be 1 to 100.")
        if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
            raise DecisionInputError("offset must be a bounded nonnegative integer.")
        if not self.database.exists():
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM decisions WHERE kind=? "
                "ORDER BY created_at DESC, record_id ASC, revision DESC LIMIT ? OFFSET ?",
                (kind, limit, offset),
            ).fetchall()
        return tuple(_parse(row) for row in rows)
