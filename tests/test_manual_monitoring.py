"""Exact local thresholds, stale reasons, durable alerts and safe empty reads."""

import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from edge.decision_contracts import DecisionInputError, ManualProvenance, PriceQuote
from edge.monitoring import MonitoringRule, evaluate
from edge.monitoring_store import MonitoringStore, validate_database

NOW = datetime(2020, 1, 1, tzinfo=UTC)
SOURCE = ManualProvenance("User statement", NOW)


def rule(**changes):
    values = {
        "rule_id": "a" * 32, "symbol": "TEST", "market": "UK", "unit": "GBp",
        "stop": Decimal(90), "target": Decimal(120), "thesis_status": "unchanged",
        "max_age_seconds": 60, "provenance": SOURCE,
    }
    return MonitoringRule(**(values | changes))


def test_no_data_is_never_a_safe_price():
    result = evaluate(rule(), None, as_of=NOW)
    assert result[0].reason == "quote_missing_no_threshold_evaluation"


@pytest.mark.parametrize("amount,reason", [
    ("90", "manual_quote_at_or_below_stop"),
    ("120", "manual_quote_at_or_above_target"),
])
def test_exact_threshold_boundaries(amount, reason):
    quote = PriceQuote(Decimal(amount), "GBp", NOW, SOURCE)
    assert evaluate(rule(), quote, as_of=NOW)[0].reason == reason


def test_stale_and_wrong_unit_do_not_trigger_stop():
    quote = PriceQuote(Decimal(1), "GBp", NOW, SOURCE)
    assert evaluate(rule(), quote, as_of=NOW + timedelta(seconds=61))[0].reason == "quote_stale_no_threshold_evaluation"
    quote = PriceQuote(Decimal(1), "GBP", NOW, SOURCE)
    assert evaluate(rule(), quote, as_of=NOW)[0].reason == "quote_unit_mismatch_no_threshold_evaluation"


def test_store_empty_durable_dedupe_ack_reopen(tmp_path):
    store = MonitoringStore(tmp_path / "data")
    assert store.rules() == ()
    assert store.quotes() == {}
    assert store.alerts() == ()
    assert not store.database.exists()
    store.append_rule(rule())
    store.append_quote("TEST", "UK", PriceQuote(Decimal(90), "GBp", NOW, SOURCE))
    store.check(as_of=NOW)
    store.check(as_of=NOW)
    assert len(store.alerts()) == 1
    alert = store.alerts()[0]
    store.acknowledge(alert.alert_id, as_of=NOW + timedelta(seconds=1))
    reopened = MonitoringStore(tmp_path / "data")
    assert reopened.alerts()[0].acknowledged_at is not None
    reopened.check(as_of=NOW)
    assert len(reopened.alerts()) == 1
    with sqlite3.connect(store.database) as con:
        validate_database(con)


def test_unknown_db_not_replaced(tmp_path):
    store = MonitoringStore(tmp_path / "data")
    store.database.parent.mkdir()
    with sqlite3.connect(store.database) as con:
        con.execute("CREATE TABLE private_notes(value TEXT)")
    before = store.database.read_bytes()
    with pytest.raises(DecisionInputError):
        store.append_rule(rule())
    assert store.database.read_bytes() == before


def test_columns_without_primary_unique_constraints_are_rejected():
    from edge.monitoring_store import APPLICATION_ID

    with sqlite3.connect(":memory:") as con:
        con.executescript(
            "CREATE TABLE rules(id TEXT,payload TEXT);"
            "CREATE TABLE quotes(id TEXT,symbol TEXT,market TEXT,payload TEXT);"
            "CREATE TABLE alerts(id TEXT,rule_id TEXT,reason TEXT,observation_at TEXT,"
            "created_at TEXT,acknowledged_at TEXT,dedupe TEXT);PRAGMA user_version=1;"
        )
        con.execute(f"PRAGMA application_id={APPLICATION_ID}")
        with pytest.raises(DecisionInputError):
            validate_database(con)


def test_quote_history_is_ordered_read_only_and_matches_latest(tmp_path):
    store = MonitoringStore(tmp_path / "data")
    assert store.quote_history() == {}
    assert not store.database.exists()
    later, earlier = NOW + timedelta(hours=2), NOW + timedelta(hours=1)
    store.append_quote("TEST", "UK", PriceQuote(Decimal(95), "GBp", later, SOURCE))
    store.append_quote("TEST", "UK", PriceQuote(Decimal(91), "GBp", earlier, SOURCE))
    store.append_quote("OTHER", "UK", PriceQuote(Decimal(5), "GBP", NOW, SOURCE))
    history = store.quote_history()
    assert [q.amount for q in history[("TEST", "UK")]] == [Decimal(91), Decimal(95)]
    assert [q.amount for q in history[("OTHER", "UK")]] == [Decimal(5)]
    assert store.quotes()[("TEST", "UK")] == history[("TEST", "UK")][-1]
    assert store.quote_history() == history