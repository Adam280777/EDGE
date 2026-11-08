"""Persistence safety tests use pytest temporary directories exclusively."""

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

import edge.portfolio_store as portfolio_store_module
from edge.decision_contracts import DecisionStatus, FxRate, ManualProvenance, PriceQuote
from edge.portfolio import (
    Buy,
    CashDeposit,
    CashExchange,
    CashOpening,
    CashWithdrawal,
    Correction,
    Instrument,
    OpeningHolding,
    PortfolioInputError,
    PortfolioStorageError,
    Sell,
)
from edge.portfolio_store import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    PortfolioStore,
    validate_database,
)

D = Decimal
NOW = datetime(2026, 11, 7, 12, tzinfo=UTC)
US = Instrument("ACME", "US", "USD")
SOURCE = "explicit test declaration"


def store(root):
    return PortfolioStore(root, clock=lambda: NOW)


def openings():
    return (
        CashOpening(NOW, "GBP", D("10000"), SOURCE),
        CashOpening(NOW, "USD", D("10000"), SOURCE),
    )


def buy():
    return Buy(NOW, US, 10, D("100"), "USD", D("5"), "USD", SOURCE)


def test_missing_reads_never_initialize_or_touch_sibling_data(tmp_path):
    root = tmp_path / "missing"
    assert store(root).list_entries() == ()
    assert store(root).state().cash == {}
    assert not root.exists()
    sentinel = tmp_path / "journal.sqlite3"
    sentinel.write_bytes(b"untouched journal marker")
    store(tmp_path).append_many(openings())
    assert sentinel.read_bytes() == b"untouched journal marker"


def test_roundtrip_canonical_utc_uuid_and_immutable_entries(tmp_path):
    ledger = store(tmp_path)
    written = ledger.append_many((*openings(), buy()))
    assert ledger.list_entries() == written
    assert ledger.state().cash["USD"] == D("8995")
    assert all(len(entry.event_id) == 32 for entry in written)
    assert all(entry.provenance == "user-declared" for entry in written)
    with sqlite3.connect(ledger.database) as connection:
        assert (
            connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT recorded_at FROM portfolio_events"
        ).fetchone()[0] == NOW.isoformat(timespec="microseconds")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("UPDATE portfolio_events SET payload='{}'")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM portfolio_events")
    with pytest.raises(AttributeError):
        written[-1].event.quantity = 100


def test_store_snapshot_uses_shared_manual_quote_and_fx_contract(tmp_path):
    ledger = store(tmp_path)
    ledger.append_many((*openings(), buy()))
    provenance = ManualProvenance(SOURCE, NOW)
    result = ledger.snapshot(
        quotes={US: PriceQuote(D("110"), "USD", NOW, provenance)},
        fx_rates=(FxRate("USD", "GBP", D("0.8"), NOW, provenance),),
        as_of=NOW,
        max_age=timedelta(hours=1),
    )
    assert result.readiness.status == DecisionStatus.COMPLETE
    assert result.holdings[0].unrealized == D("95")


def test_bad_first_create_and_failed_batch_leave_no_db(tmp_path):
    ledger = store(tmp_path / "absent")
    with pytest.raises(PortfolioInputError, match="CashOpening"):
        ledger.append(buy())
    assert not ledger.root.exists()
    with pytest.raises(PortfolioInputError, match="Insufficient"):
        ledger.append_many(
            (
                CashOpening(NOW, "USD", D("1000"), SOURCE),
                buy(),
            )
        )
    assert not ledger.database.exists()
    assert not ledger.root.exists()


def test_existing_batch_rollback_preserves_bytes_and_ledger(tmp_path):
    ledger = store(tmp_path)
    original = ledger.append_many((*openings(), buy()))
    before = ledger.database.read_bytes()
    with pytest.raises(PortfolioInputError, match="Oversell"):
        ledger.append_many(
            (
                CashDeposit(NOW, "USD", D("1"), SOURCE),
                Sell(NOW, US, 11, D("110"), "USD", D("0"), "USD", SOURCE),
            )
        )
    assert ledger.list_entries() == original
    assert ledger.database.read_bytes() == before


def test_explicit_uuid_retry_is_idempotent_but_equal_buys_with_new_ids_append(tmp_path):
    ledger = store(tmp_path)
    ledger.append_many(openings())
    event_id = uuid4().hex
    original = ledger.append(buy(), event_id=event_id)
    retry = ledger.append(buy(), event_id=event_id)
    assert retry == original
    assert len(ledger.list_entries()) == 3
    with pytest.raises(PortfolioInputError, match="conflicts"):
        ledger.append(replace(buy(), price=D("101")), event_id=event_id)
    ledger.append(buy())
    assert len(ledger.state().lots) == 2
    assert len(ledger.list_entries()) == 4


def test_batch_idempotence_rejects_partial_conflict_and_duplicate_ids(tmp_path):
    ledger = store(tmp_path)
    ids = (uuid4().hex, uuid4().hex)
    original = ledger.append_many(openings(), event_ids=ids)
    assert ledger.append_many(openings(), event_ids=ids) == original
    with pytest.raises(PortfolioInputError):
        ledger.append_many(openings(), event_ids=(ids[0], uuid4().hex))
    with pytest.raises(PortfolioInputError):
        ledger.append_many(openings(), event_ids=(ids[0], ids[0]))
    assert ledger.list_entries() == original


def test_correction_and_replacement_are_immutable_atomic_and_replayable(tmp_path):
    ledger = store(tmp_path)
    ledger.append_many(openings())
    original = ledger.append(buy())
    ledger.append_many(
        (
            Correction(NOW, original.event_id, "price correction", SOURCE),
            replace(buy(), price=D("90")),
        )
    )
    assert ledger.state().lots[0].remaining_cost == D("905")
    assert len(ledger.list_entries()) == 5
    assert ledger.list_entries()[2] == original
    ledger.append(Sell(NOW, US, 1, D("110"), "USD", D("0"), "USD", SOURCE))
    records = ledger.list_entries()
    with pytest.raises(PortfolioInputError, match="Oversell"):
        ledger.append(
            Correction(NOW, records[-2].event_id, "remove active buy", SOURCE)
        )
    assert ledger.list_entries() == records


def test_exchange_and_opening_holding_roundtrip_no_synthetic_transactions(tmp_path):
    ledger = store(tmp_path)
    provenance = ManualProvenance(SOURCE, NOW)
    records = ledger.append_many(
        (
            *openings(),
            OpeningHolding(NOW, US, 4, D("100"), date(2025, 1, 1), SOURCE),
            CashExchange(
                NOW,
                D("100"),
                FxRate("USD", "GBP", D("0.8"), NOW, provenance),
                D("1"),
                "GBP",
                SOURCE,
            ),
        )
    )
    assert ledger.list_entries() == records
    assert ledger.state().cash == {"GBP": D("10079"), "USD": D("9900")}
    assert ledger.state().lots[0].remaining_cost == D("100")


@pytest.mark.parametrize(
    "kind", ["appid", "version", "extra_table", "extra_trigger", "payload", "sequence"]
)
def test_incompatible_or_tampered_database_refused_never_reset(tmp_path, kind):
    ledger = store(tmp_path)
    ledger.append_many(openings())
    with sqlite3.connect(ledger.database) as connection:
        if kind == "appid":
            connection.execute("PRAGMA application_id=1")
        elif kind == "version":
            connection.execute("PRAGMA user_version=99")
        elif kind == "extra_table":
            connection.execute("CREATE TABLE foreign_data (value TEXT)")
        elif kind == "extra_trigger":
            connection.execute(
                "CREATE TRIGGER other AFTER INSERT ON portfolio_events BEGIN SELECT 1; END"
            )
        else:
            # Simulate tampering, then restore the exact guard to isolate row validation.
            guard = connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='portfolio_no_update'"
            ).fetchone()[0]
            connection.execute("DROP TRIGGER portfolio_no_update")
            if kind == "payload":
                connection.execute(
                    "UPDATE portfolio_events SET payload='{}' WHERE sequence=1"
                )
            else:
                connection.execute(
                    "UPDATE portfolio_events SET sequence=9 WHERE sequence=1"
                )
            connection.execute(guard)
    before = ledger.database.read_bytes()
    with pytest.raises(PortfolioStorageError):
        ledger.list_entries()
    with pytest.raises(PortfolioStorageError):
        ledger.append(CashDeposit(NOW, "GBP", D("1"), SOURCE))
    assert ledger.database.read_bytes() == before


@pytest.mark.parametrize("contents", [b"", b"not a database"])
def test_preexisting_unknown_file_untouched(tmp_path, contents):
    ledger = store(tmp_path)
    ledger.database.write_bytes(contents)
    with pytest.raises(PortfolioStorageError):
        ledger.append_many(openings())
    assert ledger.database.read_bytes() == contents


def test_publish_failure_cleans_temporary_and_preserves_absent_target(
    tmp_path, monkeypatch
):
    ledger = store(tmp_path)

    def fail_link(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(os, "link", fail_link)
    with pytest.raises(PortfolioStorageError):
        ledger.append_many(openings())
    assert list(tmp_path.iterdir()) == []


def test_publish_race_never_clobbers_existing_target(tmp_path, monkeypatch):
    ledger = store(tmp_path)
    original_link = os.link

    def competing_link(source, destination):
        destination.write_bytes(b"competing target must survive")
        return original_link(source, destination)

    monkeypatch.setattr(os, "link", competing_link)
    with pytest.raises(PortfolioStorageError):
        ledger.append_many(openings())
    assert ledger.database.read_bytes() == b"competing target must survive"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["portfolio.sqlite3"]


def test_chronology_clock_future_and_invalid_amount_rollback(tmp_path):
    ledger = store(tmp_path)
    records = ledger.append_many(openings())
    with pytest.raises(PortfolioInputError, match="chronology"):
        ledger.append(CashDeposit(NOW - timedelta(seconds=1), "GBP", D("1"), SOURCE))
    with pytest.raises(PortfolioInputError, match="before"):
        ledger.append(CashDeposit(NOW + timedelta(seconds=1), "GBP", D("1"), SOURCE))
    with pytest.raises(PortfolioInputError, match="Decimal"):
        ledger.append(CashWithdrawal(NOW, "GBP", 1.0, SOURCE))
    assert ledger.list_entries() == records


def test_public_recovery_validator_is_read_only_and_restores_connection_state(tmp_path):
    ledger = store(tmp_path)
    ledger.append_many((*openings(), buy()))
    before = ledger.database.read_bytes()
    with sqlite3.connect(f"{ledger.database.as_uri()}?mode=ro", uri=True) as connection:
        assert connection.row_factory is None
        validate_database(connection)
        assert connection.row_factory is None
    assert ledger.database.read_bytes() == before


def test_public_recovery_validator_refuses_invalid_db_without_row_factory_leak(
    tmp_path,
):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE other (value TEXT)")
        with pytest.raises(PortfolioStorageError):
            validate_database(connection)
        assert connection.row_factory is None


@pytest.mark.parametrize("event_id", [[], 1, "not-a-uuid", uuid4().hex.upper()])
def test_invalid_retry_uuid_is_input_error_not_crash(tmp_path, event_id):
    with pytest.raises(PortfolioInputError, match="UUID"):
        store(tmp_path).append(openings()[0], event_id=event_id)
    assert not (tmp_path / "portfolio.sqlite3").exists()


def test_write_lock_covers_reads_commit_first_publish_retry_and_failure(
    tmp_path, monkeypatch
):
    ledger = store(tmp_path)
    held = False
    acquisitions = []
    exists = ledger._exists
    connect = ledger._connect
    initialize = ledger._initialize

    @contextmanager
    def lock(root):
        nonlocal held
        assert root == tmp_path.parent
        assert not held
        held = True
        acquisitions.append(root)
        try:
            yield
        finally:
            held = False

    def check_exists():
        assert held
        return exists()

    def check_initialize(records):
        assert held
        return initialize(records)

    @contextmanager
    def check_connect(**kwargs):
        assert held
        with connect(**kwargs) as connection:
            yield connection
            assert held  # includes the subsequent underlying commit
        assert held

    monkeypatch.setattr(portfolio_store_module, "storage_write_lock", lock)
    monkeypatch.setattr(ledger, "_exists", check_exists)
    monkeypatch.setattr(ledger, "_connect", check_connect)
    monkeypatch.setattr(ledger, "_initialize", check_initialize)
    ids = (uuid4().hex, uuid4().hex)
    ledger.append_many(openings(), event_ids=ids)
    ledger.append_many(openings(), event_ids=ids)
    ledger.append(buy())
    with pytest.raises(PortfolioInputError, match="Insufficient"):
        ledger.append(CashWithdrawal(NOW, "USD", D("100000"), SOURCE))
    assert len(acquisitions) == 4
    assert not held


def test_sqlite_insert_failure_rolls_back_existing_batch_and_first_create(
    tmp_path, monkeypatch
):
    ledger = store(tmp_path)
    records = ledger.append_many(openings())
    before = ledger.database.read_bytes()
    monkeypatch.setattr(
        portfolio_store_module, "_INSERT", "INSERT INTO missing_table VALUES (?)"
    )
    with pytest.raises(PortfolioStorageError):
        ledger.append(CashDeposit(NOW, "GBP", D("1"), SOURCE))
    assert ledger.list_entries() == records
    assert ledger.database.read_bytes() == before
    empty = store(tmp_path / "other")
    with pytest.raises(PortfolioStorageError):
        empty.append_many(openings())
    assert not empty.database.exists()
    assert list(empty.root.iterdir()) == []


def test_public_write_normalizes_aware_input_to_utc_without_assuming_timezone(tmp_path):
    offset = timezone(timedelta(hours=2))
    local_time = NOW.astimezone(offset)
    ledger = store(tmp_path)
    written = ledger.append(CashOpening(local_time, "GBP", D("0"), SOURCE))
    assert written.event.effective_at == NOW
    assert written.event.effective_at.tzinfo is UTC
    assert ledger.list_entries() == (written,)
