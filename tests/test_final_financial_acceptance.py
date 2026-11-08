"""Independent financial acceptance checks for the final manual workflows.

All cases use deterministic synthetic inputs and temporary SQLite stores. They
exercise calculations and user-declared observations only; no provider or trade
execution is involved.
"""

# Exact Decimal string literals keep all financial fixtures explicit.
# ruff: noqa: FURB157
import json
import sqlite3
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from edge.decision_contracts import (
    DecisionInputError,
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
)
from edge.decision_store import DecisionStore, RiskPolicyRevision
from edge.discovery import (
    CompanyObservations,
    FundamentalObservation,
    RankingRule,
    ScreenFilter,
    screen_companies,
)
from edge.monitoring import MonitoringRule, evaluate
from edge.monitoring_store import APPLICATION_ID as MONITORING_APPLICATION_ID
from edge.monitoring_store import MonitoringStore
from edge.monitoring_store import validate_database as validate_monitoring_database
from edge.portfolio import (
    Buy,
    CashExchange,
    CashOpening,
    Correction,
    Fee,
    Instrument,
    OpeningHolding,
    PortfolioInputError,
    PortfolioStorageError,
    Sell,
)
from edge.portfolio_store import PortfolioStore
from edge.research_workflows import (
    CompanyIdentity,
    ValuationScenario,
    evaluate_valuation,
)
from edge.trade_planning import (
    SizingInputs,
    TradingCosts,
    calculate_position_size,
)

NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)
RECORD_AT = NOW + timedelta(days=1)
GBP = Instrument("VOD", "UK", "GBP")
USD = Instrument("ACME", "US", "USD")


def provenance():
    return ManualProvenance("Synthetic acceptance observation", NOW)


def test_fifo_multiple_lots_partial_sale_fees_and_currency_separation(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "USD", Decimal("10000"), "opening USD cash"),
            CashOpening(NOW, "GBP", Decimal("500"), "opening GBP cash"),
        )
    )
    first = store.append(
        Buy(NOW, USD, 10, Decimal("100"), "USD", Decimal("5"), "USD", "first lot")
    )
    second = store.append(
        Buy(
            NOW + timedelta(minutes=1), USD, 5, Decimal("120"), "USD",
            Decimal("5"), "USD", "second lot",
        )
    )
    sale = store.append(
        Sell(
            NOW + timedelta(minutes=2), USD, 12, Decimal("130"), "USD",
            Decimal("6"), "USD", "partial FIFO sale",
        )
    )

    state = store.state()
    assert state.cash == {"USD": Decimal("9944"), "GBP": Decimal("500")}
    assert state.realized["USD"] == Decimal("307")
    assert len(state.sales) == 1
    assert state.sales[0].event_id == sale.event_id
    assert state.sales[0].proceeds == Decimal("1554")
    assert state.sales[0].cost == Decimal("1247")
    assert [(a.opening_event_id, a.quantity, a.cost) for a in state.sales[0].allocations] == [
        (first.event_id, 10, Decimal("1005")),
        (second.event_id, 2, Decimal("242")),
    ]
    assert [(lot.event_id, lot.quantity, lot.remaining_cost) for lot in state.lots] == [
        (second.event_id, 3, Decimal("363"))
    ]
    assert state.cash["USD"] - Decimal("10000") == Decimal("-56")


def test_oversell_and_insufficient_cash_are_atomic(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "USD", Decimal("100"), "opening cash"),
            CashOpening(NOW, "GBP", Decimal("0"), "opening cash"),
            OpeningHolding(
                NOW, USD, 2, Decimal("100"), date(2026, 1, 2), "declared opening holding"
            ),
        )
    )
    before = store.list_entries()

    with pytest.raises(PortfolioInputError, match="Insufficient USD cash"):
        store.append(
            Buy(
                NOW + timedelta(minutes=1), USD, 1, Decimal("101"), "USD",
                Decimal("0"), "USD", "unaffordable",
            )
        )
    with pytest.raises(PortfolioInputError, match="Oversell"):
        store.append(
            Sell(
                NOW + timedelta(minutes=1), USD, 3, Decimal("110"), "USD",
                Decimal("0"), "USD", "too many shares",
            )
        )
    assert store.list_entries() == before
    assert store.state().cash == {"USD": Decimal("100"), "GBP": Decimal("0")}
    assert [(lot.quantity, lot.remaining_cost) for lot in store.state().lots] == [
        (2, Decimal("100"))
    ]


def test_retry_uuid_is_idempotent_but_distinct_ids_are_distinct_events(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "USD", Decimal("1000"), "opening cash"),
            CashOpening(NOW, "GBP", Decimal("0"), "opening cash"),
        )
    )
    event = Buy(
        NOW + timedelta(minutes=1), USD, 1, Decimal("10"), "USD",
        Decimal("0"), "USD", "manual purchase",
    )
    event_id = "1" * 32
    first = store.append(event, event_id=event_id)
    retry = store.append(event, event_id=event_id)
    assert retry == first
    assert len(store.list_entries()) == 3
    assert store.state().cash["USD"] == Decimal("990")
    with pytest.raises(PortfolioInputError, match="conflicts"):
        store.append(
            Buy(
                NOW + timedelta(minutes=1), USD, 2, Decimal("10"), "USD",
                Decimal("0"), "USD", "different event",
            ),
            event_id=event_id,
        )
    assert len(store.list_entries()) == 3


def test_gbp_and_usd_cash_convert_only_on_explicit_exchange(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "USD", Decimal("1000"), "opening USD cash"),
            CashOpening(NOW, "GBP", Decimal("0"), "opening GBP cash"),
        )
    )
    assert store.state().cash == {"USD": Decimal("1000"), "GBP": Decimal("0")}

    fx = FxRate("USD", "GBP", Decimal("0.8"), NOW, provenance())
    store.append(
        CashExchange(
            NOW + timedelta(minutes=1), Decimal("100"), fx, Decimal("1"), "USD",
            "explicit user-declared conversion",
        )
    )
    assert store.state().cash == {"USD": Decimal("899"), "GBP": Decimal("80")}


def test_gbp_pence_is_normalized_once_in_ledger(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "GBP", Decimal("1000"), "opening GBP cash"),
            CashOpening(NOW, "USD", Decimal("0"), "opening USD cash"),
        )
    )
    store.append(
        Buy(
            NOW + timedelta(minutes=1), GBP, 2, Decimal("123.45"), "GBp",
            Decimal("0"), "GBP", "manual GBp quote",
        )
    )
    state = store.state()
    assert state.cash["GBP"] == Decimal("997.5310")
    assert state.lots[0].remaining_cost == Decimal("2.4690")


def test_snapshot_requires_explicit_fresh_inputs_and_never_fabricates_totals(tmp_path):
    store = PortfolioStore(tmp_path / "portfolio", clock=lambda: RECORD_AT)
    store.append_many(
        (
            CashOpening(NOW, "USD", Decimal("500"), "opening USD cash"),
            CashOpening(NOW, "GBP", Decimal("1000"), "opening GBP cash"),
            OpeningHolding(
                NOW, USD, 10, Decimal("1000"), date(2026, 1, 2), "declared opening holding"
            ),
        )
    )
    fx = FxRate("USD", "GBP", Decimal("0.8"), NOW, provenance())
    quote = PriceQuote(Decimal("110"), "USD", NOW, provenance())
    complete = store.snapshot(
        quotes={USD: quote}, fx_rates=(fx,), as_of=RECORD_AT, max_age=timedelta(days=2)
    )
    assert complete.readiness.status.value == "complete"
    assert complete.cash_total_gbp == Decimal("1400.0")
    assert complete.holdings_total_gbp == Decimal("880.0")
    assert complete.equity_total_gbp == Decimal("2280.0")
    assert complete.cost_total_gbp == Decimal("800.0")
    assert complete.unrealized_total_gbp == Decimal("80.0")

    missing = store.snapshot(
        quotes={}, fx_rates=(), as_of=RECORD_AT, max_age=timedelta(days=2)
    )
    assert missing.readiness.status.value == "incomplete"
    assert missing.holdings[0].market_value is None
    assert missing.holdings_total_gbp is None
    assert missing.equity_total_gbp is None
    assert missing.cash_total_gbp is None

    stale = store.snapshot(
        quotes={USD: quote}, fx_rates=(fx,), as_of=RECORD_AT + timedelta(days=2),
        max_age=timedelta(days=1),
    )
    assert stale.readiness.status.value == "incomplete"
    assert stale.equity_total_gbp is None


def test_risk_sizing_is_disabled_by_default_and_confirmed_size_respects_budget():
    entry = PriceQuote(Decimal("100"), "GBP", NOW, provenance())
    stop = PriceQuote(Decimal("95"), "GBP", NOW, provenance())
    target = PriceQuote(Decimal("110"), "GBP", NOW, provenance())
    inputs = SizingInputs(
        equity_gbp=FinanceMoney(Decimal("10000"), "GBP"),
        max_cash_risk_gbp=FinanceMoney(Decimal("100"), "GBP"),
        available_cash=FinanceMoney(Decimal("5000"), "GBP"),
        exposure_limit_gbp=FinanceMoney(Decimal("10000"), "GBP"),
        current_exposure_gbp=FinanceMoney(Decimal("0"), "GBP"),
        entry=entry,
        stop=stop,
        targets=(target,),
        costs=TradingCosts(
            FinanceMoney(Decimal("1"), "GBP"),
            FinanceMoney(Decimal("1"), "GBP"),
            Decimal("0"),
            Decimal("0"),
        ),
        fx_to_gbp=None,
    )
    disabled = calculate_position_size(RiskPolicy(), inputs)
    assert disabled.shares == 0
    assert disabled.readiness.status.value == "blocked"
    assert disabled.policy_revision_id is None

    policy = RiskPolicy(True, Decimal("0.01"), NOW, "confirmed-revision-1")
    sized = calculate_position_size(policy, inputs)
    assert sized.readiness.status.value == "complete"
    assert sized.shares == 19
    assert sized.estimated_loss_gbp.amount == Decimal("97")
    assert sized.estimated_loss_gbp.amount <= Decimal("100")
    assert sized.estimated_cash_required == FinanceMoney(Decimal("1901"), "GBP")
    assert sized.policy_revision_id == "confirmed-revision-1"
    assert any("freshness unverified" in item for item in sized.assumptions)


def test_risk_sizing_converts_gbp_and_pence_friction_without_double_scaling():
    entry = PriceQuote(Decimal("10000"), "GBp", NOW, provenance())
    stop = PriceQuote(Decimal("9500"), "GBp", NOW, provenance())
    target = PriceQuote(Decimal("11000"), "GBp", NOW, provenance())
    inputs = SizingInputs(
        FinanceMoney(Decimal("10000"), "GBP"),
        FinanceMoney(Decimal("100"), "GBP"),
        FinanceMoney(Decimal("5000"), "GBP"),
        FinanceMoney(Decimal("10000"), "GBP"),
        FinanceMoney(Decimal("0"), "GBP"),
        entry,
        stop,
        (target,),
        TradingCosts(
            FinanceMoney(Decimal("1"), "GBP"),
            FinanceMoney(Decimal("1"), "GBP"),
            Decimal("100"),
            Decimal("50"),
        ),
        None,
    )
    result = calculate_position_size(
        RiskPolicy(True, Decimal("0.01"), NOW, "confirmed-gbp-revision"), inputs
    )
    assert result.shares == 14
    assert result.estimated_loss_gbp.amount == Decimal("100")
    assert result.estimated_cash_required == FinanceMoney(Decimal("1415"), "GBP")


def test_sizing_is_bounded_by_available_cash_and_user_exposure():
    entry = PriceQuote(Decimal("100"), "GBP", NOW, provenance())
    stop = PriceQuote(Decimal("95"), "GBP", NOW, provenance())
    target = PriceQuote(Decimal("110"), "GBP", NOW, provenance())
    policy = RiskPolicy(True, Decimal("0.01"), NOW, "confirmed-capacity-revision")

    def size(cash: str, exposure_limit: str, current_exposure: str):
        inputs = SizingInputs(
            FinanceMoney(Decimal("10000"), "GBP"),
            FinanceMoney(Decimal("100"), "GBP"),
            FinanceMoney(Decimal(cash), "GBP"),
            FinanceMoney(Decimal(exposure_limit), "GBP"),
            FinanceMoney(Decimal(current_exposure), "GBP"),
            entry,
            stop,
            (target,),
            TradingCosts(
                FinanceMoney(Decimal("1"), "GBP"),
                FinanceMoney(Decimal("1"), "GBP"),
                Decimal("0"),
                Decimal("0"),
            ),
            None,
        )
        return calculate_position_size(policy, inputs)

    cash_limited = size("500", "10000", "0")
    assert cash_limited.shares == 4
    assert cash_limited.estimated_cash_required.amount <= Decimal("500")
    exposure_limited = size("5000", "500", "100")
    assert exposure_limited.shares == 4
    assert exposure_limited.added_exposure_gbp.amount <= Decimal("400")


def test_decision_store_binds_confirmation_to_immutable_revision(tmp_path):
    store = DecisionStore(tmp_path / "decisions")
    assert store.list_records("risk") == ()
    assert not store.database.exists()

    first_policy = RiskPolicy(True, Decimal("0.01"), NOW, "risk-config:1")
    first = RiskPolicyRevision("risk-config", 1, NOW, first_policy)
    store.append(first)
    assert store.get("risk", "risk-config", 1) == first

    second_time = NOW + timedelta(hours=1)
    second_policy = RiskPolicy(True, Decimal("0.02"), second_time, "risk-config:2")
    second = RiskPolicyRevision("risk-config", 2, second_time, second_policy)
    store.append(second)
    assert store.list_records("risk") == (second, first)
    assert store.get("risk", "risk-config", 1) == first

    with pytest.raises(DecisionInputError, match="exact id:revision"):
        RiskPolicyRevision("risk-config", 3, second_time, second_policy)
    with pytest.raises(DecisionInputError, match="revision"):
        store.append(
            RiskPolicyRevision(
                "risk-config", 4, second_time + timedelta(hours=1),
                RiskPolicy(True, Decimal("0.02"), second_time, "risk-config:4"),
            )
        )
    assert store.list_records("risk") == (second, first)


def test_discovery_uses_explicit_filters_lexicographic_ranks_and_exclusions():
    identity = lambda symbol: CompanyIdentity(symbol, f"Company {symbol}", "XNAS", "USD")

    def observation(metric, value, *, classification="claimed-reported", unit="x"):
        return FundamentalObservation(
            metric, None if value is None else Decimal(value), unit, "FY2025",
            NOW, provenance(), classification,
        )

    companies = (
        CompanyObservations(identity("ALFA"), (
            observation("quality", "8"), observation("growth", "2"),
        )),
        CompanyObservations(identity("BRAVO"), (
            observation("quality", "8"), observation("growth", "2"),
        )),
        CompanyObservations(identity("CHARLIE"), (
            observation("quality", "6"), observation("growth", "9"),
        )),
        CompanyObservations(identity("MISSING"), (observation("growth", "9"),)),
        CompanyObservations(identity("ESTIMATE"), (
            observation("quality", "9", classification="user-estimate"),
            observation("growth", "99"),
        )),
        CompanyObservations(identity("BADUNIT"), (
            observation("quality", "9", unit="%"), observation("growth", "99"),
        )),
    )
    filters = (ScreenFilter(
        "quality", "x", Decimal("5"), None, "FY2025", NOW - timedelta(days=1),
    ),)
    ranking = (
        RankingRule("quality", "x", "higher", "FY2025", NOW - timedelta(days=1)),
        RankingRule("growth", "x", "higher", "FY2025", NOW - timedelta(days=1)),
    )
    results = screen_companies(companies, filters, ranking, as_of=NOW)
    assert [r.identity.symbol for r in results] == [
        "ALFA", "BRAVO", "CHARLIE", "BADUNIT", "ESTIMATE", "MISSING",
    ]
    assert [(r.identity.symbol, r.rank) for r in results[:3]] == [
        ("ALFA", 1), ("BRAVO", 2), ("CHARLIE", 3),
    ]
    assert all(r.included for r in results[:3])
    assert all(not r.included and r.rank is None for r in results[3:])
    by_symbol = {r.identity.symbol: r for r in results}
    assert by_symbol["BADUNIT"].exclusions == ("quality: unit-mismatch",)
    assert by_symbol["ESTIMATE"].exclusions == ("quality: classification-excluded",)
    assert by_symbol["MISSING"].exclusions == ("quality: missing-data",)
    assert "not expected return" in results[0].limitations[1]


def test_user_valuation_scenario_is_reproducible_and_explicitly_hypothetical():
    scenario = ValuationScenario(
        name="Synthetic base case",
        provenance=provenance(),
        base_revenue=FinanceMoney(Decimal("100000000"), "GBP"),
        growth_fraction=Decimal("0"),
        operating_margin_fraction=Decimal("0.1"),
        cash_conversion_fraction=Decimal("0.8"),
        discount_rate_fraction=Decimal("0.1"),
        terminal_growth_fraction=Decimal("0.02"),
        years=1,
        cash=FinanceMoney(Decimal("10000000"), "GBP"),
        debt=FinanceMoney(Decimal("5000000"), "GBP"),
        diluted_shares=100_000_000,
    )
    result = evaluate_valuation(scenario)
    assert result.projected_cash_flows == (FinanceMoney(Decimal("8000000"), "GBP"),)
    assert result.enterprise_value.amount == Decimal("100000000")
    assert result.equity_value.amount == Decimal("105000000")
    assert result.value_per_share.amount == Decimal("1.05")
    assert any("unverified" in limitation for limitation in result.limitations)


def test_stale_monitoring_observation_is_reason_only_never_a_threshold_result(tmp_path):
    rule = MonitoringRule(
        "a" * 32, "ACME", "US", "USD", Decimal("95"), Decimal("110"),
        "unchanged", 60, provenance(),
    )
    stale = PriceQuote(
        Decimal("80"), "USD", NOW - timedelta(minutes=5), provenance()
    )
    findings = evaluate(rule, stale, as_of=NOW)
    assert tuple(item.reason for item in findings) == (
        "quote_stale_no_threshold_evaluation",
    )

    fresh_below_stop = PriceQuote(Decimal("90"), "USD", NOW, provenance())
    assert tuple(item.reason for item in evaluate(
        rule, fresh_below_stop, as_of=NOW
    )) == ("manual_quote_at_or_below_stop",)


def test_monitoring_check_persists_deduplicated_findings_without_changing_rules(
    tmp_path,
):
    store = MonitoringStore(tmp_path / "data")
    rule = MonitoringRule(
        "b" * 32, "ACME", "US", "USD", Decimal("95"), None,
        "unchanged", 60, provenance(),
    )
    store.append_rule(rule)
    store.append_quote(
        "ACME", "US",
        PriceQuote(Decimal("90"), "USD", NOW, provenance()),
    )
    original = store.rules()
    first = store.check(as_of=NOW)
    second = store.check(as_of=NOW + timedelta(seconds=1))
    assert tuple(item.reason for item in first) == ("manual_quote_at_or_below_stop",)
    assert second == first
    assert store.rules() == original
    alerts = store.alerts()
    assert len(alerts) == 1
    assert alerts[0].reason == "manual_quote_at_or_below_stop"
    assert alerts[0].acknowledged_at is None


def test_monitoring_recovery_validator_rejects_orphan_alert_rule_reference(tmp_path):
    store = MonitoringStore(tmp_path / "data")
    store.append_rule(MonitoringRule(
        "c" * 32, "ACME", "US", "USD", Decimal("95"), None,
        "unchanged", 60, provenance(),
    ))
    store.append_quote("ACME", "US", PriceQuote(Decimal("90"), "USD", NOW, provenance()))
    store.check(as_of=NOW)
    with sqlite3.connect(store.database) as connection:
        connection.execute("UPDATE alerts SET rule_id=?", ("d" * 32,))
    with sqlite3.connect(store.database) as connection, pytest.raises(DecisionInputError):
        validate_monitoring_database(connection)


def test_monitoring_recovery_validator_rejects_schema_without_uniqueness(tmp_path):
    database = tmp_path / "weak-monitoring.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute(f"PRAGMA application_id={MONITORING_APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute("CREATE TABLE rules(id TEXT,payload TEXT)")
        connection.execute("CREATE TABLE quotes(id TEXT,symbol TEXT,market TEXT,payload TEXT)")
        connection.execute(
            "CREATE TABLE alerts(id TEXT,rule_id TEXT,reason TEXT,observation_at TEXT,"
            "created_at TEXT,acknowledged_at TEXT,dedupe TEXT)"
        )
        with pytest.raises(DecisionInputError):
            validate_monitoring_database(connection)
    finally:
        connection.close()


@pytest.mark.parametrize("reference_kind", ["correction", "linked_fee"])
def test_portfolio_recovery_validator_rejects_orphan_internal_reference(
    tmp_path, reference_kind
):
    from edge.portfolio_store import validate_database

    store = PortfolioStore(tmp_path / "data", clock=lambda: RECORD_AT)
    store.append_many((
        CashOpening(NOW, "USD", Decimal("1000"), "Opening USD cash"),
        CashOpening(NOW, "GBP", Decimal("0"), "Opening GBP cash"),
    ))
    buy_id = "1" * 32
    store.append(
        Buy(
            NOW + timedelta(minutes=1), USD, 2, Decimal("10"), "USD",
            Decimal("0"), "USD", "Manual buy",
        ),
        event_id=buy_id,
    )
    if reference_kind == "correction":
        store.append(
            Correction(
                NOW + timedelta(minutes=2), buy_id, "Manual correction", "Manual source"
            ),
            event_id="2" * 32,
        )
        event_type = "Correction"
    else:
        store.append(
            Fee(
                NOW + timedelta(minutes=2), "USD", Decimal("1"),
                "Linked fee", buy_id,
            ),
            event_id="3" * 32,
        )
        event_type = "Fee"

    with sqlite3.connect(store.database) as connection:
        connection.execute("DROP TRIGGER portfolio_no_update")
        connection.execute(
            "UPDATE portfolio_events SET payload=replace(payload, ?, ?) WHERE event_type=?",
            (buy_id, "0" * 32, event_type),
        )
        connection.execute(
            "CREATE TRIGGER portfolio_no_update BEFORE UPDATE ON portfolio_events\n"
            "BEGIN SELECT RAISE(ABORT, 'portfolio is append-only'); END"
        )
    with (
        sqlite3.connect(f"{store.database.as_uri()}?mode=ro", uri=True) as connection,
        pytest.raises(PortfolioStorageError),
    ):
        validate_database(connection)


def test_monitoring_opt_in_is_session_only_and_leaving_workspace_disables_it(tmp_path):
    from streamlit.testing.v1 import AppTest

    from edge.monitoring_store import MonitoringStore

    app_root = tmp_path / "application"
    data_root = app_root / "data"
    app_root.mkdir()
    store = MonitoringStore(data_root)
    rule = MonitoringRule(
        "e" * 32, "ACME", "US", "USD", Decimal("95"), None,
        "unchanged", 86400, provenance(),
    )
    store.append_rule(rule)
    store.append_quote(
        "ACME", "US", PriceQuote(Decimal("90"), "USD", NOW, provenance())
    )
    script = (
        "import streamlit as st\n"
        "from pathlib import Path\n"
        "from app import main\n"
        "st.session_state['edge_workspace'] = 'Monitoring'\n"
        f"main(Path({str(data_root)!r}))\n"
    )
    app = AppTest.from_string(script, default_timeout=30).run()
    enabled_key = f"edge_monitor_enabled_{data_root.resolve()}"
    active_key = f"edge_monitor_active_{data_root.resolve()}"
    assert app.checkbox(key=enabled_key).value is False
    app.checkbox(key=enabled_key).set_value(True).run()
    assert not app.exception
    assert store.rules() == (rule,)
    assert len(store.alerts()) == 1
    assert store.alerts()[0].reason == "manual_quote_at_or_below_stop"

    app.radio(key="edge_workspace").set_value("Manual portfolio").run()
    assert app.session_state[enabled_key] is False
    assert app.session_state[active_key] is False
    assert store.rules() == (rule,)
    assert len(store.alerts()) == 1

    reopened = AppTest.from_string(script, default_timeout=30).run()
    assert reopened.checkbox(key=enabled_key).value is False
    assert reopened.session_state.get(active_key, False) is False
    assert store.rules() == (rule,)
    assert len(store.alerts()) == 1


def test_application_bundle_roundtrips_all_stores_settings_and_internal_links(
    tmp_path,
):
    from edge.application_backup import backup_application, restore_application
    from edge.decision_store import DecisionStore, RiskPolicyRevision
    from edge.journal import JournalDraft
    from edge.journal_store import JournalStore
    from edge.portfolio import Correction, Fee
    from edge.provider_settings import ProviderSettings, load_settings, save_settings
    from edge.research_workflows import CompanyIdentity
    from edge.trade_planning import SavedTradePlan
    from edge.watchlist import WatchlistDraft
    from edge.watchlist_store import WatchlistStore

    app_root = tmp_path / "source-app"
    data_root = app_root / "data"
    app_root.mkdir()

    journal = JournalStore(data_root, clock=lambda: RECORD_AT)
    journal.create(JournalDraft(
        "swing", "ACME", "Manual identity", "Journal note", date(2026, 1, 1),
        "User-authored thesis", "", "", "", "",
    ))
    watchlist = WatchlistStore(data_root, clock=lambda: RECORD_AT)
    watchlist.create(WatchlistDraft("ACME", "Manual identity", "Watch only"))
    settings = ProviderSettings(plan_label="starter")
    save_settings(data_root, settings)

    ledger = PortfolioStore(data_root, clock=lambda: RECORD_AT)
    ledger.append_many((
        CashOpening(NOW, "USD", Decimal("1000"), "Opening USD cash"),
        CashOpening(NOW, "GBP", Decimal("200"), "Opening GBP cash"),
    ))
    initial_buy = ledger.append(Buy(
        NOW + timedelta(minutes=1), USD, 2, Decimal("10"), "USD",
        Decimal("1"), "USD", "Manual buy to be corrected",
    ))
    ledger.append_many((
        Correction(
            NOW + timedelta(minutes=2), initial_buy.event_id,
            "Replace declared trade", "Manual correction",
        ),
        Buy(
            NOW + timedelta(minutes=2), USD, 2, Decimal("11"), "USD",
            Decimal("1"), "USD", "Replacement buy",
        ),
    ))
    sale = ledger.append(Sell(
        NOW + timedelta(minutes=3), USD, 1, Decimal("12"), "USD",
        Decimal("1"), "USD", "Manual sale",
    ))
    ledger.append(Fee(
        NOW + timedelta(minutes=3), "USD", Decimal("0.5"),
        "Linked sale fee", sale.event_id,
    ))
    expected_ledger = ledger.list_entries()

    risk_store = DecisionStore(data_root)
    policy = RiskPolicy(True, Decimal("0.01"), NOW, "risk:1")
    risk_revision = RiskPolicyRevision("risk", 1, NOW + timedelta(minutes=1), policy)
    risk_store.append(risk_revision)
    identity = CompanyIdentity("ACME", "Manual identity", "NASDAQ", "USD")
    usd_quote = PriceQuote(Decimal("100"), "USD", NOW, provenance())
    usd_stop = PriceQuote(Decimal("95"), "USD", NOW, provenance())
    usd_target = PriceQuote(Decimal("110"), "USD", NOW, provenance())
    fx = FxRate("USD", "GBP", Decimal("0.8"), NOW, provenance())
    inputs = SizingInputs(
        FinanceMoney(Decimal("10000"), "GBP"),
        FinanceMoney(Decimal("100"), "GBP"),
        FinanceMoney(Decimal("5000"), "USD"),
        FinanceMoney(Decimal("10000"), "GBP"),
        FinanceMoney(Decimal("0"), "GBP"),
        usd_quote,
        usd_stop,
        (usd_target,),
        TradingCosts(
            FinanceMoney(Decimal("1"), "USD"),
            FinanceMoney(Decimal("1"), "USD"),
            Decimal("0"),
            Decimal("0"),
        ),
        fx,
    )
    plan = SavedTradePlan(
        "plan", 1, NOW + timedelta(minutes=2), identity, "swing",
        "Manual thesis",
        (PriceQuote(Decimal("99"), "USD", NOW, provenance()),
         PriceQuote(Decimal("101"), "USD", NOW, provenance())),
        "Explicit trigger", "Stop rationale", "Target rationale", "Days",
        (), "Bull", "Bear", "Invalidate below stop", ("Gap risk",),
        ("Prices are user-declared",), inputs, policy,
    )
    risk_store.append(plan)

    monitoring = MonitoringStore(data_root)
    rule = MonitoringRule(
        "f" * 32, "ACME", "US", "USD", Decimal("95"), None,
        "unchanged", 86400, provenance(),
    )
    monitoring.append_rule(rule)
    monitoring.append_quote(
        "ACME", "US", PriceQuote(Decimal("90"), "USD", NOW, provenance())
    )
    monitoring.check(as_of=NOW)
    expected_alerts = monitoring.alerts()
    assert len(expected_alerts) == 1

    archive = tmp_path / "application.zip"
    manifest = backup_application(app_root, archive)
    assert manifest["consistency"] == "cooperative_locked_cut_registered_user_stores"
    assert manifest["cross_store_consistency_verified"] is True
    assert {entry["member"] for entry in manifest["entries"]} == {
        "data/research-journal.sqlite3",
        "data/watchlist.sqlite3",
        "data/portfolio.sqlite3",
        "data/decisions.sqlite3",
        "data/manual-monitoring.sqlite3",
        "settings/provider-v1.json",
    }

    restored_root = tmp_path / "restored-app"
    restored_manifest = restore_application(archive, restored_root)
    assert restored_manifest == manifest
    assert len(JournalStore(restored_root / "data").list_entries()) == 1
    assert len(WatchlistStore(restored_root / "data").list_entries()) == 1
    restored_ledger = PortfolioStore(restored_root / "data")
    assert restored_ledger.list_entries() == expected_ledger
    assert restored_ledger.state().sales[0].realized == Decimal("-1.0")
    restored_decisions = DecisionStore(restored_root / "data")
    assert restored_decisions.get("risk", "risk", 1) == risk_revision
    assert restored_decisions.get("plan", "plan", 1) == plan
    restored_monitoring = MonitoringStore(restored_root / "data")
    assert restored_monitoring.rules() == (rule,)
    assert restored_monitoring.alerts() == expected_alerts
    assert load_settings(restored_root / "data") == settings


def test_recovery_manifest_rejects_altered_scope_and_custom_registry_overclaim(tmp_path):
    from edge.application_backup import (
        ApplicationBackupError,
        StoreSpec,
        backup_application,
        restore_application,
    )

    def tamper_manifest(source, destination, **updates):
        with zipfile.ZipFile(source) as bundle:
            members = {name: bundle.read(name) for name in bundle.namelist()}
        manifest = json.loads(members["manifest.json"])
        manifest.update(updates)
        members["manifest.json"] = json.dumps(manifest).encode()
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_STORED) as bundle:
            for name, contents in members.items():
                bundle.writestr(name, contents)

    default_root = tmp_path / "default-app"
    default_root.mkdir()
    default_archive = tmp_path / "default.zip"
    manifest = backup_application(default_root, default_archive)
    assert manifest["consistency"] == "cooperative_locked_cut_registered_user_stores"
    assert manifest["cross_store_consistency_verified"] is True
    assert restore_application(default_archive, tmp_path / "default-restored") == manifest

    altered_default = tmp_path / "altered-default.zip"
    tamper_manifest(
        default_archive, altered_default,
        consistency="cooperative_lock_per_store_snapshots",
    )
    with pytest.raises(ApplicationBackupError):
        restore_application(altered_default, tmp_path / "altered-default-restored")
    assert not (tmp_path / "altered-default-restored").exists()

    custom_root = tmp_path / "custom-app"
    custom_data = custom_root / "data"
    custom_data.mkdir(parents=True)
    database = custom_data / "sample.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA application_id=1234")
        connection.execute("PRAGMA user_version=1")
        connection.execute("CREATE TABLE sample(value INTEGER NOT NULL)")
        connection.execute("INSERT INTO sample VALUES (7)")

    def validate_sample(connection):
        assert [tuple(row) for row in connection.execute("SELECT value FROM sample")] == [(7,)]

    custom_specs = (StoreSpec("sample.sqlite3", 1234, 1, validate_sample),)
    custom_archive = tmp_path / "custom.zip"
    custom_manifest = backup_application(
        custom_root, custom_archive, stores=custom_specs
    )
    assert custom_manifest["consistency"] == "cooperative_locked_cut_registered_user_stores"
    assert custom_manifest["cross_store_consistency_verified"] is False
    assert restore_application(
        custom_archive, tmp_path / "custom-restored", stores=custom_specs
    ) == custom_manifest

    overclaimed = tmp_path / "custom-overclaimed.zip"
    tamper_manifest(
        custom_archive, overclaimed, cross_store_consistency_verified=True
    )
    with pytest.raises(ApplicationBackupError):
        restore_application(
            overclaimed, tmp_path / "custom-overclaimed-restored", stores=custom_specs
        )
    assert not (tmp_path / "custom-overclaimed-restored").exists()
