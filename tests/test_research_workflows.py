"""Hypothetical manual research and DCF tests; never real company data."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext

import pytest

from edge.decision_contracts import (
    DecisionInputError,
    DecisionStatus,
    FinanceMoney,
    ManualProvenance,
)
from edge.research_workflows import (
    CompanyIdentity,
    DatedCatalyst,
    ResearchItem,
    ResearchRevision,
    ValuationScenario,
    evaluate_valuation,
)

D = Decimal
NOW = datetime(2026, 11, 7, 15, tzinfo=UTC)
SOURCE = ManualProvenance("Synthetic manual assumptions", NOW)


def scenario() -> ValuationScenario:
    return ValuationScenario(
        "User base", SOURCE, FinanceMoney(D(1000), "GBP"), D(".1"), D(".2"),
        D(".5"), D(".1"), D("0"), 2,
        FinanceMoney(D(100), "GBP"), FinanceMoney(D(50), "GBP"), 100,
    )


def research() -> ResearchRevision:
    item = ResearchItem("User revenue claim", SOURCE, "FY2025", "GBP")
    return ResearchRevision(
        "research", 1, NOW, CompanyIdentity("SYN", "Synthetic", "TEST", "GBP"),
        "longterm", (item,), (replace(item, text="Growth assumption"),),
        (replace(item, text="Mispricing opinion"),), (scenario(),), (),
        "Margins expand", "Margins contract", "Market may underrate durable margins",
        "Margins fail", ("Dilution",), (), "Manual research, not a trade signal",
    )


def test_research_distinct_claims_assumptions_opinions_and_readiness() -> None:
    revision = research()
    assert revision.facts[0].text != revision.assumptions[0].text != revision.opinions[0].text
    assert revision.facts[0].provenance.kind == "user-declared"
    assert "unverified" in revision.limitations[0]
    assert revision.readiness.status == DecisionStatus.COMPLETE
    assert replace(revision, bear_case="").readiness.status == DecisionStatus.INCOMPLETE
    assert replace(revision, bear_case="   ").readiness.status == DecisionStatus.INCOMPLETE
    assert replace(revision, missing_information=("Debt maturity",)).readiness.reasons
    with pytest.raises(FrozenInstanceError):
        revision.bull_case = "Changed"


def test_reproducible_dcf_equation_and_context_independence() -> None:
    result = evaluate_valuation(scenario())
    assert tuple(f.amount for f in result.projected_cash_flows) == (D(110), D(121))
    assert result.enterprise_value.amount == D(1200)  # 110/1.1 + 121/1.21 + 1210/1.21
    assert result.equity_value.amount == D(1250)
    assert result.value_per_share.amount == D("12.5")
    with localcontext() as ctx:
        ctx.prec = 80
        assert result.terminal_value_fraction == D(1000) / D(1200)
    with localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = ROUND_DOWN
        ctx.traps[Inexact] = True
        assert evaluate_valuation(scenario()) == result
    assert "Hypothetical" in result.limitations[0]


def test_zero_flows_and_negative_equity_not_invented_floor() -> None:
    result = evaluate_valuation(replace(
        scenario(), operating_margin_fraction=D(0),
        cash=FinanceMoney(D(0), "GBP"), debt=FinanceMoney(D(100), "GBP"),
    ))
    assert result.enterprise_value.amount == 0
    assert result.equity_value.amount == -100
    assert result.value_per_share.amount == -1
    assert result.terminal_value_fraction == 0


@pytest.mark.parametrize("updates", [
    {"growth_fraction": D("-1")}, {"growth_fraction": D("6")},
    {"operating_margin_fraction": D("1.01")}, {"cash_conversion_fraction": D("-0.1")},
    {"discount_rate_fraction": D(0)}, {"terminal_growth_fraction": D(".1")},
    {"terminal_growth_fraction": D(".099")}, {"years": 0}, {"years": 31},
    {"years": True}, {"diluted_shares": 0}, {"diluted_shares": 1.5},
    {"growth_fraction": 0.1}, {"growth_fraction": D("NaN")},
    {"debt": FinanceMoney(D(1), "USD")},
    {"base_revenue": FinanceMoney(D(-1), "GBP")},
])
def test_valuation_safeguards(updates: dict) -> None:
    with pytest.raises(DecisionInputError):
        replace(scenario(), **updates)


@pytest.mark.parametrize("updates", [
    {"facts": []}, {"horizon": "both"}, {"revision": True},
    {"created_at": datetime(2026, 11, 7)}, {"record_id": ""},
    {"risks": ("bad\x00text",)},
    {"identity": CompanyIdentity("SYN", "Synthetic", "TEST", "USD")},
])
def test_revision_validation(updates: dict) -> None:
    with pytest.raises(DecisionInputError):
        replace(research(), **updates)


def test_manual_provenance_period_units_and_catalyst_required() -> None:
    with pytest.raises(DecisionInputError):
        ResearchItem("Claim", SOURCE, "", "GBP")
    with pytest.raises(DecisionInputError):
        ResearchItem("Claim", None, "FY2025", "GBP")
    catalyst = DatedCatalyst("User earnings estimate", date(2026, 11, 20), SOURCE)
    assert catalyst.provenance == SOURCE
    with pytest.raises(DecisionInputError):
        replace(catalyst, event_date=NOW)
