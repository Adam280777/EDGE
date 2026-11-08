"""Isolated shared-contract tests; no providers, storage or implicit clocks."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from edge.decision_contracts import (
    REPORTING_CURRENCY,
    DecisionInputError,
    DecisionReadiness,
    DecisionStatus,
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
    normalize_utc,
    validate_whole_shares,
)

NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)


def provenance():
    return ManualProvenance("Explicit manual input", NOW)


@pytest.mark.parametrize("value", [1, 1.2, True, "1", Decimal("NaN"),
                                    Decimal("Infinity"), Decimal("-Infinity")])
def test_money_requires_finite_decimal(value):
    with pytest.raises(DecisionInputError):
        FinanceMoney(value, "GBP")


@pytest.mark.parametrize("currency", ["GBp", "gbp", "EUR", None, 1])
def test_cash_currency_is_explicit(currency):
    with pytest.raises(DecisionInputError):
        FinanceMoney(Decimal(1), currency)


def test_signed_separate_cash_and_reporting_currency():
    assert REPORTING_CURRENCY == "GBP"
    assert FinanceMoney(Decimal("-1.2345"), "GBP").amount == Decimal("-1.2345")
    assert FinanceMoney(Decimal(0), "USD").currency == "USD"
    with pytest.raises(FrozenInstanceError):
        FinanceMoney(Decimal(1), "GBP").amount = Decimal(2)


def test_provenance_is_declaration_not_vendor_acceptance():
    assert provenance().kind == "user-declared"
    assert not hasattr(provenance(), "calculation_eligible")
    with pytest.raises(DecisionInputError):
        ManualProvenance("FMP", NOW, "vendor-accepted")


@pytest.mark.parametrize("reference", ["", " ", "a" * 201, "x\nx", "\u200bx"])
def test_provenance_reference_bounded(reference):
    with pytest.raises(DecisionInputError):
        ManualProvenance(reference, NOW)


def test_aware_instant_normalizes_to_utc():
    local = datetime(2026, 11, 7, 17, tzinfo=timezone(timedelta(hours=2)))
    assert normalize_utc(local) == NOW
    assert ManualProvenance("Declared", local).declared_at.tzinfo is UTC
    assert PriceQuote(Decimal(1), "GBP", local, provenance()).as_of.tzinfo is UTC


@pytest.mark.parametrize("timestamp", [None, "2026-11-07", datetime(2026, 11, 7)])
def test_naive_or_non_timestamp_rejected(timestamp):
    with pytest.raises(DecisionInputError):
        normalize_utc(timestamp)


@pytest.mark.parametrize("unit,amount,currency", [
    ("GBp", "123.456", "GBP"), ("GBP", "1.23456", "GBP"),
    ("USD", "1.23456", "USD"),
])
def test_price_units_no_rounding_or_double_adjustment(unit, amount, currency):
    quote = PriceQuote(Decimal(amount), unit, NOW, provenance())
    expected = Decimal(amount) / 100 if unit == "GBp" else Decimal(amount)
    assert quote.per_share_money() == FinanceMoney(expected, currency)
    assert quote.per_share_money() == quote.per_share_money()


@pytest.mark.parametrize("amount", [Decimal(0), Decimal(-1), Decimal("NaN"), 1.5])
def test_price_positive_and_exact(amount):
    with pytest.raises(DecisionInputError):
        PriceQuote(amount, "GBP", NOW, provenance())


def test_price_requires_explicit_unit_and_manual_provenance():
    with pytest.raises(DecisionInputError):
        PriceQuote(Decimal(1), "gbp", NOW, provenance())
    with pytest.raises(DecisionInputError):
        PriceQuote(Decimal(1), "GBP", NOW, None)


def test_fx_orientation_signed_conversion_no_implicit_inverse():
    rate = FxRate("USD", "GBP", Decimal("0.8"), NOW, provenance())
    assert rate.convert(FinanceMoney(Decimal("-10.125"), "USD")) == FinanceMoney(
        Decimal("-8.1000"), "GBP"
    )
    with pytest.raises(DecisionInputError):
        rate.convert(FinanceMoney(Decimal(10), "GBP"))
    inverse = FxRate("GBP", "USD", Decimal("1.25"), NOW, provenance())
    assert inverse.convert(FinanceMoney(Decimal(8), "GBP")).amount == Decimal(10)


@pytest.mark.parametrize("changes", [
    {"from_currency": "GBp"}, {"to_currency": "USD"}, {"rate": Decimal(0)},
    {"rate": Decimal(-1)}, {"rate": 0.8}, {"provenance": None},
    {"as_of": datetime(2026, 11, 7)},
])
def test_fx_rejects_ambiguous_invalid_inputs(changes):
    fields = {
        "from_currency": "USD", "to_currency": "GBP", "rate": Decimal("0.8"),
        "as_of": NOW, "provenance": provenance(),
    }
    with pytest.raises(DecisionInputError):
        FxRate(**(fields | changes))


@pytest.mark.parametrize("status", [DecisionStatus.INCOMPLETE, DecisionStatus.BLOCKED])
def test_noncomplete_requires_specific_reasons(status):
    assert DecisionReadiness(status, ("missing_fx",)).reasons == ("missing_fx",)
    with pytest.raises(DecisionInputError):
        DecisionReadiness(status)


def test_readiness_not_vendor_acceptance_and_no_success_shaped_failure():
    assert DecisionReadiness(DecisionStatus.COMPLETE).status == DecisionStatus.COMPLETE
    with pytest.raises(DecisionInputError):
        DecisionReadiness(DecisionStatus.COMPLETE, ("missing_price",))
    with pytest.raises(DecisionInputError):
        DecisionReadiness("complete")
    with pytest.raises(DecisionInputError):
        DecisionReadiness(DecisionStatus.BLOCKED, ["missing_price"])


def test_risk_disabled_without_percentage_default():
    policy = RiskPolicy()
    assert not policy.enabled
    assert policy.max_risk_fraction is None
    assert policy.confirmed_at is None
    with pytest.raises(DecisionInputError):
        policy.require_enabled()
    draft = RiskPolicy(max_risk_fraction=Decimal("0.01"))
    with pytest.raises(DecisionInputError):
        draft.require_enabled()


def test_risk_requires_explicit_limit_and_confirmation():
    policy = RiskPolicy(True, Decimal("0.01"), NOW, "user-confirmed-1")
    policy.require_enabled()
    assert policy.long_only and policy.whole_shares
    assert policy.max_risk_fraction == Decimal("0.01")
    with pytest.raises(FrozenInstanceError):
        policy.enabled = False


@pytest.mark.parametrize("changes", [
    {"enabled": True}, {"enabled": 1}, {"max_risk_fraction": Decimal(0)},
    {"max_risk_fraction": Decimal("1.01")}, {"max_risk_fraction": 0.01},
    {"confirmed_at": NOW}, {"revision_id": "r1"},
    {"confirmed_at": NOW, "revision_id": "r1"},
    {"long_only": False}, {"whole_shares": False}, {"long_only": 1},
    {"enabled": True, "max_risk_fraction": Decimal("0.01"),
     "confirmed_at": datetime(2026, 11, 7), "revision_id": "r1"},
])
def test_invalid_risk_policy(changes):
    with pytest.raises(DecisionInputError):
        RiskPolicy(**changes)


@pytest.mark.parametrize("quantity", [-1, 0, True, 1.0, Decimal(1), "1"])
def test_whole_shares_no_coercion_or_shorting(quantity):
    with pytest.raises(DecisionInputError):
        validate_whole_shares(quantity)


def test_whole_share_zero_requires_explicit_permission():
    assert validate_whole_shares(1) == 1
    assert validate_whole_shares(0, allow_zero=True) == 0
    with pytest.raises(DecisionInputError):
        validate_whole_shares(1, allow_zero=1)
