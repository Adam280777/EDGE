"""Offline user-authored research, not verified financial facts or advice."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal

from edge.decision_contracts import (
    Currency,
    DecisionInputError,
    DecisionReadiness,
    DecisionStatus,
    FinanceMoney,
    ManualProvenance,
    normalize_utc,
    validate_whole_shares,
)


def text(value: object, field: str, *, optional: bool = False) -> None:
    """Validate bounded literal user text without silently rewriting it."""
    if (
        not isinstance(value, str)
        or (not optional and not value.strip())
        or len(value) > 10000
        or any(
            unicodedata.category(c) in ("Cc", "Cf", "Cs") and c not in "\n\t"
            for c in value
        )
    ):
        raise DecisionInputError(f"{field} must be bounded plain text.")


def number(value: object, field: str, *, minimum: Decimal | None = None) -> None:
    """Reject floats, non-finite and extreme inputs; fixed precision is reproducible."""
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or len(value.as_tuple().digits) > 18
        or not -12 <= value.as_tuple().exponent <= 12
        or value.copy_abs() > Decimal("1e18")
    ):
        raise DecisionInputError(f"{field} must be a bounded finite Decimal.")
    if minimum is not None and value < minimum:
        raise DecisionInputError(f"{field} must be at least {minimum}.")


def immutable_items(value: object, item_type: type, field: str) -> None:
    if (
        not isinstance(value, tuple)
        or len(value) > 100
        or any(not isinstance(item, item_type) for item in value)
    ):
        raise DecisionInputError(f"{field} must be a bounded immutable tuple.")


@dataclass(frozen=True)
class CompanyIdentity:
    symbol: str
    company_name: str
    exchange: str
    reporting_currency: Currency

    def __post_init__(self) -> None:
        for field in ("symbol", "company_name", "exchange"):
            text(getattr(self, field), field)
        if not self.symbol.isascii() or len(self.symbol) > 32:
            raise DecisionInputError("symbol must be a bounded ASCII identifier.")
        if self.reporting_currency not in ("GBP", "USD"):
            raise DecisionInputError("Reporting currency must be GBP or USD.")


@dataclass(frozen=True)
class ResearchItem:
    """A fact field is a user's claimed fact, never independently verified."""

    text: str
    provenance: ManualProvenance
    reporting_period: str
    unit: str

    def __post_init__(self) -> None:
        for field in ("text", "reporting_period", "unit"):
            text(getattr(self, field), field)
        if not isinstance(self.provenance, ManualProvenance):
            raise DecisionInputError("Research items need manual source and date.")


@dataclass(frozen=True)
class DatedCatalyst:
    description: str
    event_date: date
    provenance: ManualProvenance

    def __post_init__(self) -> None:
        text(self.description, "description")
        if type(self.event_date) is not date:
            raise DecisionInputError("Catalyst requires an explicit calendar date.")
        if not isinstance(self.provenance, ManualProvenance):
            raise DecisionInputError("Catalyst requires manual provenance.")


@dataclass(frozen=True)
class ValuationScenario:
    """User assumptions: revenue * operating margin * cash conversion -> FCF.

    Growth is constant over explicit years, then terminal growth. This simple
    enterprise DCF is hypothetical, not a forecast; debt/cash convert EV to equity.
    Rates are fractions, never implicit percentages. All cash uses one currency.
    """

    name: str
    provenance: ManualProvenance
    base_revenue: FinanceMoney
    growth_fraction: Decimal
    operating_margin_fraction: Decimal
    cash_conversion_fraction: Decimal
    discount_rate_fraction: Decimal
    terminal_growth_fraction: Decimal
    years: int
    cash: FinanceMoney
    debt: FinanceMoney
    diluted_shares: int

    def __post_init__(self) -> None:
        text(self.name, "scenario name")
        if not isinstance(self.provenance, ManualProvenance):
            raise DecisionInputError("Scenario requires user-declared provenance.")
        for field in ("base_revenue", "cash", "debt"):
            money = getattr(self, field)
            if not isinstance(money, FinanceMoney):
                raise DecisionInputError(f"{field} must be FinanceMoney.")
            number(money.amount, field, minimum=Decimal(0))
            if money.currency != self.base_revenue.currency:
                raise DecisionInputError("Scenario money must use one currency.")
        for field in (
            "growth_fraction", "operating_margin_fraction",
            "cash_conversion_fraction", "discount_rate_fraction",
            "terminal_growth_fraction",
        ):
            number(getattr(self, field), field)
        if not Decimal(-1) < self.growth_fraction <= Decimal(5):
            raise DecisionInputError("Growth fraction must be > -1 and <= 5.")
        if not 0 <= self.operating_margin_fraction <= 1:
            raise DecisionInputError("Margin fraction must be between zero and one.")
        if not 0 <= self.cash_conversion_fraction <= 1:
            raise DecisionInputError("Cash conversion must be between zero and one.")
        if not 0 < self.discount_rate_fraction <= 1:
            raise DecisionInputError("Discount rate must be > zero and <= one.")
        if not -1 < self.terminal_growth_fraction < self.discount_rate_fraction:
            raise DecisionInputError("Terminal growth must be > -1 and < discount rate.")
        with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
            if self.discount_rate_fraction - self.terminal_growth_fraction < Decimal("0.005"):
                raise DecisionInputError("Discount/terminal spread must be at least 0.005.")
        if type(self.years) is not int or not 1 <= self.years <= 30:
            raise DecisionInputError("DCF horizon must be 1 to 30 years.")
        validate_whole_shares(self.diluted_shares)


@dataclass(frozen=True)
class ValuationResult:
    projected_cash_flows: tuple[FinanceMoney, ...]
    enterprise_value: FinanceMoney
    equity_value: FinanceMoney
    value_per_share: FinanceMoney
    terminal_value_fraction: Decimal
    limitations: tuple[str, ...]


def evaluate_valuation(scenario: ValuationScenario) -> ValuationResult:
    if not isinstance(scenario, ValuationScenario):
        raise DecisionInputError("Expected a ValuationScenario.")
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        revenue = scenario.base_revenue.amount
        currency = scenario.base_revenue.currency
        flows: list[FinanceMoney] = []
        present = Decimal(0)
        for year in range(1, scenario.years + 1):
            revenue *= 1 + scenario.growth_fraction
            flow = revenue * scenario.operating_margin_fraction * scenario.cash_conversion_fraction
            flows.append(FinanceMoney(flow, currency))
            present += flow / (1 + scenario.discount_rate_fraction) ** year
        terminal = flows[-1].amount * (1 + scenario.terminal_growth_fraction) / (
            scenario.discount_rate_fraction - scenario.terminal_growth_fraction
        )
        terminal_pv = terminal / (1 + scenario.discount_rate_fraction) ** scenario.years
        enterprise = present + terminal_pv
        equity = enterprise + scenario.cash.amount - scenario.debt.amount
        return ValuationResult(
            tuple(flows), FinanceMoney(enterprise, currency),
            FinanceMoney(equity, currency),
            FinanceMoney(equity / scenario.diluted_shares, currency),
            terminal_pv / enterprise if enterprise else Decimal(0),
            (
                "Hypothetical user inputs; identity, sources and financial facts unverified.",
                "Constant growth/margin/conversion; no independent forecast or market price.",
                "Dilution only through user-entered diluted shares; terminal value is sensitive.",
                "Negative equity value is preserved, not silently floored.",
            ),
        )


@dataclass(frozen=True)
class ResearchRevision:
    record_id: str
    revision: int
    created_at: datetime
    identity: CompanyIdentity
    horizon: Literal["swing", "longterm"]
    facts: tuple[ResearchItem, ...]
    assumptions: tuple[ResearchItem, ...]
    opinions: tuple[ResearchItem, ...]
    valuation_scenarios: tuple[ValuationScenario, ...]
    catalysts: tuple[DatedCatalyst, ...]
    bull_case: str
    bear_case: str
    mispricing_hypothesis: str
    invalidation: str
    risks: tuple[str, ...]
    missing_information: tuple[str, ...]
    conclusion: str

    def __post_init__(self) -> None:
        validate_revision(self.record_id, self.revision, self.created_at)
        object.__setattr__(self, "created_at", normalize_utc(self.created_at))
        if not isinstance(self.identity, CompanyIdentity):
            raise DecisionInputError("Research requires a manual company identity.")
        if self.horizon not in ("swing", "longterm"):
            raise DecisionInputError("Keep swing and longterm research separate.")
        for field in ("facts", "assumptions", "opinions"):
            immutable_items(getattr(self, field), ResearchItem, field)
        immutable_items(self.valuation_scenarios, ValuationScenario, "valuation_scenarios")
        immutable_items(self.catalysts, DatedCatalyst, "catalysts")
        if any(s.base_revenue.currency != self.identity.reporting_currency
               for s in self.valuation_scenarios):
            raise DecisionInputError("Valuation must use company reporting currency.")
        for field in ("bull_case", "bear_case", "mispricing_hypothesis", "invalidation", "conclusion"):
            text(getattr(self, field), field, optional=True)
        for field in ("risks", "missing_information"):
            immutable_items(getattr(self, field), str, field)
            for item in getattr(self, field):
                text(item, field)

    @property
    def readiness(self) -> DecisionReadiness:
        reasons = []
        for field in ("facts", "bull_case", "bear_case", "mispricing_hypothesis", "invalidation", "risks", "conclusion"):
            value = getattr(self, field)
            if not value or (isinstance(value, str) and not value.strip()):
                reasons.append(f"Missing {field}.")
        if self.missing_information:
            reasons.append("User has declared missing information.")
        sources = [item.provenance for item in (*self.facts, *self.assumptions, *self.opinions)]
        sources.extend(item.provenance for item in (*self.catalysts, *self.valuation_scenarios))
        if any(source.declared_at > self.created_at for source in sources):
            reasons.append("A manual declaration is dated after this revision.")
        return DecisionReadiness(
            DecisionStatus.INCOMPLETE if reasons else DecisionStatus.COMPLETE,
            tuple(reasons),
        )

    @property
    def limitations(self) -> tuple[str, ...]:
        return (
            "Identity, source citations, dates and claimed facts are user-declared and unverified.",
            "Facts, assumptions and opinions are separate user statements, not provider acceptance.",
            "Readiness is workflow completeness, never investment advice or a trade signal.",
        )


def validate_revision(record_id: str, revision: int, created_at: datetime) -> None:
    text(record_id, "record_id")
    if len(record_id) > 100:
        raise DecisionInputError("record_id is limited to 100 characters.")
    if type(revision) is not int or not 1 <= revision <= 2**31 - 1:
        raise DecisionInputError("revision must be a positive bounded integer.")
    normalize_utc(created_at)
