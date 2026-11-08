"""Explicit-only durable rules/alerts; reads never create a database."""

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from edge.application_storage_lock import storage_write_lock
from edge.decision_contracts import DecisionInputError, ManualProvenance, PriceQuote
from edge.monitoring import MonitoringRule, evaluate

APPLICATION_ID = 0x45444D31
SCHEMA_VERSION = 1
_SCHEMA = {
    "rules": "CREATE TABLE rules(id TEXT PRIMARY KEY,payload TEXT NOT NULL)",
    "quotes": "CREATE TABLE quotes(id TEXT PRIMARY KEY,symbol TEXT NOT NULL,market TEXT NOT NULL,payload TEXT NOT NULL)",
    "alerts": "CREATE TABLE alerts(id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,reason TEXT NOT NULL,observation_at TEXT,created_at TEXT NOT NULL,acknowledged_at TEXT,dedupe TEXT NOT NULL UNIQUE)",
}


@dataclass(frozen=True)
class SavedAlert:
    alert_id: str
    rule_id: str
    reason: str
    observation_at: str | None
    created_at: str
    acknowledged_at: str | None


class MonitoringStore:
    def __init__(self, root: Path):
        self.database = Path(root) / "manual-monitoring.sqlite3"

    def _open(self, write=False):
        if not self.database.exists() and not write:
            return None
        if write:
            self.database.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(
            self.database if write else f"{self.database.as_uri()}?mode=ro",
            uri=not write,
        )
        try:
            if write and con.execute("PRAGMA user_version").fetchone()[0] == 0:
                if con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall():
                    raise DecisionInputError("Unknown monitoring schema.")
                con.executescript(";".join(_SCHEMA.values()) + ";PRAGMA user_version=1;")
                con.execute(f"PRAGMA application_id={APPLICATION_ID}")
            if con.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise DecisionInputError("Unknown monitoring schema.")
            validate_database(con)
            return con
        except Exception:
            con.close()
            raise

    def rules(self) -> tuple[MonitoringRule, ...]:
        con = self._open()
        if con is None:
            return ()
        try:
            rows = con.execute("SELECT payload FROM rules ORDER BY rowid").fetchall()
            output = []
            for (raw,) in rows:
                item = json.loads(raw)
                provenance = item.pop("provenance")
                item["provenance"] = ManualProvenance(
                    provenance["reference"], datetime.fromisoformat(provenance["declared_at"])
                )
                for name in ("stop", "target"):
                    item[name] = Decimal(item[name]) if item[name] is not None else None
                output.append(MonitoringRule(**item))
            return tuple(output)
        finally:
            con.close()

    def append_rule(self, rule: MonitoringRule) -> None:
        with storage_write_lock(self.database.parent.parent):
            self._append_rule(rule)

    def _append_rule(self, rule: MonitoringRule) -> None:
        if not isinstance(rule, MonitoringRule):
            raise DecisionInputError("MonitoringRule is required.")
        con = self._open(write=True)
        try:
            with con:
                con.execute(
                    "INSERT INTO rules VALUES(?,?)",
                    (rule.rule_id, json.dumps(asdict(rule), default=str, allow_nan=False)),
                )
        finally:
            con.close()

    def append_quote(self, symbol: str, market: str, quote: PriceQuote) -> None:
        with storage_write_lock(self.database.parent.parent):
            self._append_quote(symbol, market, quote)

    def _append_quote(self, symbol: str, market: str, quote: PriceQuote) -> None:
        import re

        if (
            not re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", symbol)
            or not isinstance(quote, PriceQuote)
            or (market, quote.unit) not in (
                ("US", "USD"), ("UK", "GBP"), ("UK", "GBp")
            )
        ):
            raise DecisionInputError("Explicit matching instrument and quote required.")
        con = self._open(write=True)
        try:
            with con:
                con.execute(
                    "INSERT INTO quotes VALUES(?,?,?,?)",
                    (uuid4().hex, symbol, market,
                     json.dumps(asdict(quote), default=str, allow_nan=False)),
                )
        finally:
            con.close()

    def quotes(self) -> dict[tuple[str, str], PriceQuote]:
        output: dict[tuple[str, str], PriceQuote] = {}
        for identity, quote in self._quote_rows():
            if identity not in output or quote.as_of >= output[identity].as_of:
                output[identity] = quote
        return output

    def quote_history(self) -> dict[tuple[str, str], tuple[PriceQuote, ...]]:
        """Every recorded manual quote per instrument, oldest observation first (read-only)."""
        output: dict[tuple[str, str], list[PriceQuote]] = {}
        for identity, quote in self._quote_rows():
            output.setdefault(identity, []).append(quote)
        return {key: tuple(sorted(items, key=lambda q: q.as_of)) for key, items in output.items()}

    def _quote_rows(self) -> list[tuple[tuple[str, str], PriceQuote]]:
        con = self._open()
        if con is None:
            return []
        try:
            output = []
            for symbol, market, payload in con.execute(
                "SELECT symbol,market,payload FROM quotes ORDER BY rowid"
            ):
                item = json.loads(payload)
                provenance = item["provenance"]
                quote = PriceQuote(
                    Decimal(item["amount"]), item["unit"],
                    datetime.fromisoformat(item["as_of"]),
                    ManualProvenance(provenance["reference"],
                                     datetime.fromisoformat(provenance["declared_at"])),
                )
                output.append(((symbol, market), quote))
            return output
        finally:
            con.close()

    def check(self, *, as_of: datetime):
        with storage_write_lock(self.database.parent.parent):
            return self._check(as_of=as_of)

    def _check(self, *, as_of: datetime):
        from edge.decision_contracts import normalize_utc

        as_of = normalize_utc(as_of)
        quotes = self.quotes()
        findings = tuple(
            finding
            for rule in self.rules()
            for finding in evaluate(rule, quotes.get((rule.symbol, rule.market)), as_of=as_of)
        )
        if not findings:
            return ()
        con = self._open(write=True)
        try:
            with con:
                for finding in findings:
                    observed = finding.observation_at.isoformat() if finding.observation_at else None
                    dedupe = json.dumps((finding.rule_id, finding.reason, observed))
                    con.execute(
                        "INSERT OR IGNORE INTO alerts VALUES(?,?,?,?,?,?,?)",
                        (uuid4().hex, finding.rule_id, finding.reason, observed,
                         as_of.astimezone(UTC).isoformat(), None, dedupe),
                    )
        finally:
            con.close()
        return findings

    def alerts(self) -> tuple[SavedAlert, ...]:
        con = self._open()
        if con is None:
            return ()
        try:
            return tuple(SavedAlert(*row) for row in con.execute(
                "SELECT id,rule_id,reason,observation_at,created_at,acknowledged_at "
                "FROM alerts ORDER BY rowid DESC LIMIT 200"
            ))
        finally:
            con.close()

    def acknowledge(self, alert_id: str, *, as_of: datetime):
        from edge.decision_contracts import normalize_utc

        as_of = normalize_utc(as_of)
        with storage_write_lock(self.database.parent.parent):
            return self._acknowledge(alert_id, as_of=as_of)

    def _acknowledge(self, alert_id: str, *, as_of: datetime):
        con = self._open()
        if con is None:
            raise DecisionInputError("Alert does not exist.")
        con.close()
        con = self._open(write=True)
        try:
            with con:
                result = con.execute(
                    "UPDATE alerts SET acknowledged_at=? WHERE id=? AND acknowledged_at IS NULL",
                    (as_of.astimezone(UTC).isoformat(), alert_id),
                )
                if result.rowcount != 1:
                    raise DecisionInputError("Alert missing or already acknowledged.")
        finally:
            con.close()


def validate_database(connection: sqlite3.Connection) -> None:
    """Validate every local record before recovery or normal access."""
    import re

    from edge.decision_contracts import normalize_utc

    if (
        connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID
        or connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
        or connection.execute("PRAGMA quick_check").fetchone()[0] != "ok"
    ):
        raise DecisionInputError("Incompatible monitoring database.")
    expected = {
        "rules": ("id", "payload"),
        "quotes": ("id", "symbol", "market", "payload"),
        "alerts": ("id", "rule_id", "reason", "observation_at", "created_at", "acknowledged_at", "dedupe"),
    }
    objects = connection.execute(
        "SELECT name,type,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if {(row[0], row[1]) for row in objects} != {(name, "table") for name in expected}:
        raise DecisionInputError("Unknown monitoring schema objects.")
    if any(row[2] != _SCHEMA[row[0]] for row in objects):
        raise DecisionInputError("Monitoring constraints or schema SQL are incompatible.")
    for table, columns in expected.items():
        actual = tuple(row[1] for row in connection.execute(f"PRAGMA table_info({table})"))
        if actual != columns:
            raise DecisionInputError("Incompatible monitoring columns.")
    rule_ids = set()
    for key, payload in connection.execute("SELECT id,payload FROM rules"):
        item = json.loads(payload)
        provenance = item.pop("provenance")
        if set(provenance) != {"reference", "declared_at", "kind"}:
            raise DecisionInputError("Unknown monitoring provenance.")
        item["provenance"] = ManualProvenance(
            provenance["reference"], datetime.fromisoformat(provenance["declared_at"]),
            provenance["kind"],
        )
        for field in ("stop", "target"):
            item[field] = Decimal(item[field]) if item[field] is not None else None
        rule = MonitoringRule(**item)
        if rule.rule_id != key:
            raise DecisionInputError("Rule identity mismatch.")
        rule_ids.add(key)
    for key, symbol, market, payload in connection.execute("SELECT * FROM quotes"):
        item = json.loads(payload)
        provenance = item["provenance"]
        if set(provenance) != {"reference", "declared_at", "kind"}:
            raise DecisionInputError("Unknown quote provenance.")
        quote = PriceQuote(
            Decimal(item["amount"]), item["unit"], datetime.fromisoformat(item["as_of"]),
            ManualProvenance(provenance["reference"],
                             datetime.fromisoformat(provenance["declared_at"]), provenance["kind"]),
        )
        if (
            set(item) != {"amount", "unit", "as_of", "provenance"}
            or not re.fullmatch(r"[a-f0-9]{32}", key)
            or not re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", symbol)
            or (market, quote.unit) not in (("US", "USD"), ("UK", "GBP"), ("UK", "GBp"))
        ):
            raise DecisionInputError("Quote identity mismatch.")
    reasons = {
        "thesis_review_user_declared", "thesis_invalidated_user_declared",
        "quote_missing_no_threshold_evaluation", "quote_unit_mismatch_no_threshold_evaluation",
        "quote_future_no_threshold_evaluation", "quote_stale_no_threshold_evaluation",
        "manual_quote_at_or_below_stop", "manual_quote_at_or_above_target",
    }
    seen = set()
    for row in connection.execute("SELECT * FROM alerts"):
        key, rule_id, reason, observed, created, acknowledged, dedupe = row
        created_at = normalize_utc(datetime.fromisoformat(created))
        if observed is not None:
            normalize_utc(datetime.fromisoformat(observed))
        if acknowledged is not None and normalize_utc(datetime.fromisoformat(acknowledged)) < created_at:
            raise DecisionInputError("Alert acknowledgement precedes creation.")
        if (
            not re.fullmatch(r"[a-f0-9]{32}", key) or rule_id not in rule_ids
            or reason not in reasons or dedupe != json.dumps((rule_id, reason, observed))
            or dedupe in seen
        ):
            raise DecisionInputError("Invalid alert evidence.")
        seen.add(dedupe)
