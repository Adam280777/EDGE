"""Append-only isolated storage; all filesystem operations use pytest temp dirs."""

import socket
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from edge.decision_contracts import (
    DecisionInputError,
    FinanceMoney,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
)
from edge.decision_store import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    DecisionNotFoundError,
    DecisionStorageError,
    DecisionStore,
    RiskPolicyRevision,
    validate_database,
)
from edge.research_workflows import CompanyIdentity, ResearchItem, ResearchRevision
from edge.trade_planning import SavedTradePlan, SizingInputs, TradingCosts

D = Decimal
NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)
SOURCE = ManualProvenance("Synthetic local declaration", NOW)


def money(value: str) -> FinanceMoney:
    return FinanceMoney(D(value), "GBP")


def quote(value: str) -> PriceQuote:
    return PriceQuote(D(value), "GBP", NOW, SOURCE)


def risk(revision: int = 1, enabled: bool = True) -> RiskPolicyRevision:
    created = NOW + timedelta(seconds=revision)
    return RiskPolicyRevision(
        "account", revision, created,
        RiskPolicy(True, D(".01"), NOW, f"account:{revision}") if enabled else RiskPolicy(),
    )


def research() -> ResearchRevision:
    return ResearchRevision(
        "note", 1, NOW, CompanyIdentity("SYN", "Synthetic", "TEST", "GBP"), "longterm",
        (ResearchItem("Claim", SOURCE, "FY2025", "GBP"),), (), (), (), (),
        "Bull", "Bear", "Mispricing hypothesis", "Invalidation", ("Dilution",),
        (), "Research only",
    )


def plan(enabled: bool = True) -> SavedTradePlan:
    return SavedTradePlan(
        "plan", 1, NOW + timedelta(seconds=5), research().identity, "swing", "Thesis",
        (quote("99"), quote("101")), "Trigger", "Stop rationale", "Target rationale",
        "Days", (), "Bull", "Bear", "Invalidation", ("Gap",), ("Unverified quote",),
        SizingInputs(money("10000"), money("100"), money("10000"), money("10000"),
                     money("0"), quote("100"), quote("95"), (quote("110"),),
                     TradingCosts(money("1"), money("1"), D(0), D(0)), None),
        risk().policy if enabled else RiskPolicy(),
    )


def test_missing_reads_no_side_effects(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "absent")
    assert store.list_records("risk") == ()
    with pytest.raises(DecisionNotFoundError):
        store.get("research", "note", 1)
    assert not store.root.exists()


def test_round_trip_all_record_kinds_and_only_separate_database(tmp_path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("No network allowed.")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    store = DecisionStore(tmp_path / "data")
    for record, kind in ((risk(), "risk"), (research(), "research"), (plan(), "plan")):
        assert store.append(record) == record
        assert DecisionStore(store.root).get(kind, record.record_id, 1) == record
        assert store.list_records(kind) == (record,)
    assert {p.name for p in store.root.iterdir()} == {"decisions.sqlite3"}
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE decisions SET revision=9")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM decisions")
    assert not hasattr(store, "update") and not hasattr(store, "delete")


def test_sequential_revisions_immutable_and_horizon_identity_separate(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    first = research()
    store.append(first)
    second = replace(first, revision=2, created_at=NOW + timedelta(seconds=1), conclusion="New hypothesis")
    store.append(second)
    assert store.get("research", "note", 1) == first
    assert store.list_records("research") == (second, first)
    for record in (
        first, replace(first, revision=4), replace(first, revision=3, horizon="swing"),
        replace(first, revision=3, identity=replace(first.identity, symbol="OTHER")),
        replace(first, revision=3, created_at=NOW),
    ):
        with pytest.raises(DecisionInputError):
            store.append(record)
    assert len(store.list_records("research")) == 2


def test_enabled_plan_requires_latest_exact_stored_policy_confirmation(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    with pytest.raises(DecisionInputError):
        store.append(plan())
    assert not store.database.exists()
    store.append(risk())
    store.append(plan())
    store.append(risk(2, False))
    with pytest.raises(DecisionInputError, match="stale"):
        store.append(replace(plan(), revision=2))
    # Historical plan keeps snapshot, but this is not current-policy permission.
    assert store.get("plan", "plan", 1).policy == risk().policy
    store.append(replace(plan(False), revision=2))
    assert store.get("plan", "plan", 2).sizing.shares == 0
    third = risk(3)
    store.append(third)
    store.append(replace(plan(), revision=3, policy=third.policy))
    with pytest.raises(DecisionInputError):
        replace(third, revision=4)  # old confirmation cannot bind new revision


def test_unconfirmed_policy_can_be_saved_without_enabling_and_zero_plan(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    disabled = RiskPolicyRevision("account", 1, NOW, RiskPolicy(False, D(".01")))
    assert store.append(disabled).policy.enabled is False
    assert store.append(plan(False)).sizing.shares == 0


def test_unknown_and_future_schema_never_reset(tmp_path) -> None:
    root = tmp_path / "data"
    root.mkdir()
    database = root / "decisions.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preserve')")
    before = database.read_bytes()
    store = DecisionStore(root)
    for action in (lambda: store.list_records("risk"), lambda: store.append(risk())):
        with pytest.raises(DecisionStorageError):
            action()
    assert database.read_bytes() == before


def test_damaged_hash_and_unexpected_sql_objects_rejected(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    store.append(risk())
    with sqlite3.connect(store.database) as connection:
        connection.execute("CREATE TABLE unexpected(value TEXT)")
    with pytest.raises(DecisionStorageError):
        store.list_records("risk")
    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP TABLE unexpected")
        # A privileged user can bypass triggers: corruption is rejected on read.
        connection.execute("DROP TRIGGER decisions_no_update")
        connection.execute("UPDATE decisions SET digest='bad'")
        connection.execute(
            "CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions\n"
            "BEGIN SELECT RAISE(ABORT, 'immutable decision revision'); END"
        )
    with pytest.raises(DecisionStorageError):
        store.get("risk", "account", 1)


def test_failure_before_first_publication_no_empty_database(tmp_path, monkeypatch) -> None:
    store = DecisionStore(tmp_path / "data")
    def fail(*args, **kwargs):
        raise OSError("Simulated publication failure")
    monkeypatch.setattr("edge.decision_store.os.link", fail)
    with pytest.raises(DecisionStorageError):
        store.append(risk())
    assert not store.database.exists()
    assert list(store.root.iterdir()) == []


def test_first_publication_race_keeps_both_records(tmp_path) -> None:
    root = tmp_path / "data"
    records = (research(), replace(research(), record_id="other"))
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(lambda item: DecisionStore(root).append(item), records))
    assert results == records
    assert len(DecisionStore(root).list_records("research")) == 2


def test_concurrent_same_next_revision_only_one_succeeds(tmp_path) -> None:
    root = tmp_path / "data"
    DecisionStore(root).append(research())
    next_record = replace(research(), revision=2)
    def append():
        try:
            DecisionStore(root).append(next_record)
            return True
        except DecisionInputError:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: append(), range(2)))
    assert sorted(outcomes) == [False, True]
    assert len(DecisionStore(root).list_records("research")) == 2


def test_query_and_confirmation_validation(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    for action in (
        lambda: store.list_records("other"),
        lambda: store.list_records("risk", limit=True),
        lambda: store.list_records("risk", offset=-1),
        lambda: store.get("risk", "account", True),
        lambda: replace(risk(), policy=replace(risk().policy, revision_id="other")),
        lambda: replace(risk(), created_at=NOW - timedelta(seconds=1)),
        lambda: store.append(RiskPolicy()),
    ):
        with pytest.raises(DecisionInputError):
            action()


def test_full_backup_validator_checks_all_payloads_and_historical_policy(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    for record in (risk(), plan(), risk(2, False), replace(plan(False), revision=2), research()):
        store.append(record)
    with sqlite3.connect(f"{store.database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        validate_database(connection)
    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP TRIGGER decisions_no_delete")
        connection.execute("DELETE FROM decisions WHERE kind='risk' AND revision=1")
        connection.execute(
            "CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions\n"
            "BEGIN SELECT RAISE(ABORT, 'immutable decision revision'); END"
        )
    with sqlite3.connect(f"{store.database.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        with pytest.raises(DecisionStorageError):
            validate_database(connection)


def test_bypassed_frozen_model_revalidated_before_writing(tmp_path) -> None:
    store = DecisionStore(tmp_path / "data")
    record = research()
    object.__setattr__(record, "revision", True)
    with pytest.raises(DecisionInputError):
        store.append(record)
    assert not store.database.exists()
