"""Offline deterministic sizing; examples are synthetic user declarations."""

import socket
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from edge.decision_contracts import (
    DecisionInputError,
    DecisionStatus,
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
)
from edge.research_workflows import CompanyIdentity
from edge.trade_planning import (
    SavedTradePlan,
    SizingInputs,
    TradingCosts,
    calculate_position_size,
)

D = Decimal
NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)
SOURCE = ManualProvenance("Synthetic user input; not market data", NOW)


def money(amount: str, currency: str = "GBP") -> FinanceMoney:
    return FinanceMoney(D(amount), currency)


def quote(amount: str, unit: str = "GBP") -> PriceQuote:
    return PriceQuote(D(amount), unit, NOW, SOURCE)


def inputs(unit: str = "GBP") -> SizingInputs:
    cash_currency = "USD" if unit == "USD" else "GBP"
    scale = D(100) if unit == "GBp" else D(1)
    return SizingInputs(
        money("10000"), money("100"), money("10000", cash_currency),
        money("10000"), money("0"),
        quote(str(100 * scale), unit), quote(str(95 * scale), unit),
        (quote(str(110 * scale), unit), quote(str(120 * scale), unit)),
        TradingCosts(money("1", cash_currency), money("1", cash_currency), D(0), D(0)),
        FxRate("USD", "GBP", D("0.8"), NOW, SOURCE) if unit == "USD" else None,
    )


def policy() -> RiskPolicy:
    return RiskPolicy(True, D("0.01"), NOW, "account:1")


def plan(enabled: bool = True) -> SavedTradePlan:
    return SavedTradePlan(
        "plan", 1, NOW, CompanyIdentity("SYN", "Synthetic", "TEST", "GBP"),
        "swing", "User thesis", (quote("99"), quote("101")),
        "User chart trigger", "User technical invalidation", "User resistance levels",
        "5 to 20 trading days", (), "Bull hypothesis", "Bear hypothesis",
        "Thesis false below support", ("Earnings gap",), ("No verified latest chart",),
        inputs(), policy() if enabled else RiskPolicy(),
    )


def test_disabled_unconfirmed_and_disabled_confirmed() -> None:
    for risk in (RiskPolicy(), RiskPolicy(False, D("0.01")), replace(policy(), enabled=False)):
        result = calculate_position_size(risk, inputs())
        assert result.shares == 0
        assert result.readiness.status == DecisionStatus.BLOCKED
        assert "confirmed" in result.readiness.reasons[0]
    with pytest.raises(DecisionInputError):
        RiskPolicy(True, D("0.01"))


def test_exact_fees_risk_floor_and_derived_rewards() -> None:
    result = calculate_position_size(policy(), inputs())
    assert result.shares == 19  # floor((100 - 2) / 5)
    assert result.estimated_loss_gbp == money("97")
    assert result.estimated_cash_required == money("1901")
    assert result.added_exposure_gbp == money("1900")
    assert result.binding_limits == ("risk",)
    with localcontext() as ctx:
        ctx.prec = 80
        assert result.target_reward_risk == (D(188) / 97, D(378) / 97)
    assert result.policy_revision_id == "account:1"


@pytest.mark.parametrize("unit", ["GBP", "GBp", "USD"])
def test_common_units_and_explicit_fx(unit: str) -> None:
    result = calculate_position_size(policy(), inputs(unit))
    if unit == "USD":
        assert result.shares == 24
        assert result.estimated_loss_gbp == money("97.6")
        assert result.estimated_cash_required == money("2401", "USD")
    else:
        assert result.shares == 19
        assert result.estimated_loss_gbp == money("97")


def test_spread_slippage_original_quote_units_and_exact_context() -> None:
    basic = inputs("GBp")
    costs = replace(basic.costs, spread_per_share=D("10"), slippage_per_side=D("5"))
    explicit = replace(basic, costs=costs)
    expected = calculate_position_size(policy(), explicit)
    assert expected.shares == 18  # floor(98 / (5 + .2))
    assert expected.estimated_loss_gbp == money("95.6")
    with localcontext() as ctx:
        ctx.prec = 6
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert calculate_position_size(policy(), explicit) == expected


@pytest.mark.parametrize(
    "updates,shares,binding",
    [
        ({"available_cash": money("1001")}, 10, "cash"),
        ({"max_cash_risk_gbp": money("22")}, 4, "risk"),
        ({"exposure_limit_gbp": money("1500"), "current_exposure_gbp": money("1000")}, 5, "exposure"),
    ],
)
def test_all_user_limits(updates: dict, shares: int, binding: str) -> None:
    result = calculate_position_size(policy(), replace(inputs(), **updates))
    assert result.shares == shares
    assert binding in result.binding_limits


@pytest.mark.parametrize(
    "updates",
    [
        {"available_cash": money("0")}, {"max_cash_risk_gbp": money("1")},
        {"exposure_limit_gbp": money("0")}, {"available_cash": money("100")},
        {"current_exposure_gbp": money("11000")},
        {"targets": (quote("100.01"),)},
        {"stop": quote("0.01"), "costs": TradingCosts(money("1"), money("1"), D("1"), D("1"))},
    ],
)
def test_unusable_size_explicit_blocked_reason(updates: dict) -> None:
    result = calculate_position_size(policy(), replace(inputs(), **updates))
    assert result.shares == 0
    assert result.readiness.status == DecisionStatus.BLOCKED
    assert result.readiness.reasons


@pytest.mark.parametrize(
    "updates",
    [
        {"stop": quote("100")}, {"stop": quote("101")},
        {"targets": ()}, {"targets": (quote("90"),)},
        {"targets": (quote("120"), quote("110"))},
        {"targets": (quote("110"), quote("110"))},
        {"stop": quote("9500", "GBp")},
        {"available_cash": money("10000", "USD")},
        {"equity_gbp": money("10000", "USD")},
        {"max_cash_risk_gbp": money("-1")},
        {"fx_to_gbp": FxRate("USD", "GBP", D(".8"), NOW, SOURCE)},
        {"costs": TradingCosts(money("1", "USD"), money("1", "USD"), D(0), D(0))},
    ],
)
def test_invalid_inputs_rejected(updates: dict) -> None:
    with pytest.raises(DecisionInputError):
        replace(inputs(), **updates)


def test_missing_or_reversed_fx_never_inferred() -> None:
    with pytest.raises(DecisionInputError):
        replace(inputs("USD"), fx_to_gbp=None)
    with pytest.raises(DecisionInputError):
        replace(inputs("USD"), fx_to_gbp=FxRate("GBP", "USD", D("1.25"), NOW, SOURCE))


@pytest.mark.parametrize("value", [0.1, "0.1", D("NaN"), D("Infinity"), D("-1")])
def test_costs_do_not_coerce_float_or_bad_numbers(value: object) -> None:
    with pytest.raises(DecisionInputError):
        TradingCosts(money("1"), money("1"), value, D(0))


def test_plan_immutable_complete_thesis_disabled_sizing_and_no_network(monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("No network allowed.")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    saved = plan(False)
    assert saved.sizing.shares == 0
    with pytest.raises(FrozenInstanceError):
        saved.thesis = "Changed"
    for update in ({"horizon": "both"}, {"stop_reason": ""}, {"uncertainty": ()},
                   {"entry_zone": (quote("101"), quote("102"))}):
        with pytest.raises(DecisionInputError):
            replace(saved, **update)


def test_bounded_budget_invariants_across_cash_and_risk_grid() -> None:
    for cash in ("101", "201", "1001", "10000"):
        for risk in ("2", "7", "23", "100"):
            given = replace(inputs(), available_cash=money(cash), max_cash_risk_gbp=money(risk))
            result = calculate_position_size(policy(), given)
            assert type(result.shares) is int and result.shares >= 0
            assert result.estimated_cash_required.amount <= given.available_cash.amount
            assert result.estimated_loss_gbp.amount <= given.max_cash_risk_gbp.amount
            assert result.added_exposure_gbp.amount <= given.exposure_limit_gbp.amount
