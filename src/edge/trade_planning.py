"""Deterministic manual long-only whole-share sizing; never executes trades."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Literal

from edge.decision_contracts import (
    DecisionInputError,
    DecisionReadiness,
    DecisionStatus,
    FinanceMoney,
    FxRate,
    PriceQuote,
    RiskPolicy,
    normalize_utc,
)
from edge.research_workflows import (
    CompanyIdentity,
    DatedCatalyst,
    immutable_items,
    number,
    text,
    validate_revision,
)


@dataclass(frozen=True)
class TradingCosts:
    entry_fee: FinanceMoney
    exit_fee: FinanceMoney
    spread_per_share: Decimal
    slippage_per_side: Decimal

    def __post_init__(self) -> None:
        for field in ("entry_fee", "exit_fee"):
            money = getattr(self, field)
            if not isinstance(money, FinanceMoney):
                raise DecisionInputError("Fees must be FinanceMoney, not quote pence.")
            number(money.amount, field, minimum=Decimal(0))
        if self.entry_fee.currency != self.exit_fee.currency:
            raise DecisionInputError("Entry and exit fees must use the same currency.")
        number(self.spread_per_share, "spread_per_share", minimum=Decimal(0))
        number(self.slippage_per_side, "slippage_per_side", minimum=Decimal(0))


@dataclass(frozen=True)
class SizingInputs:
    equity_gbp: FinanceMoney
    max_cash_risk_gbp: FinanceMoney
    available_cash: FinanceMoney
    exposure_limit_gbp: FinanceMoney
    current_exposure_gbp: FinanceMoney
    entry: PriceQuote
    stop: PriceQuote
    targets: tuple[PriceQuote, ...]
    costs: TradingCosts
    fx_to_gbp: FxRate | None

    def __post_init__(self) -> None:
        for field in (
            "equity_gbp", "max_cash_risk_gbp", "available_cash",
            "exposure_limit_gbp", "current_exposure_gbp",
        ):
            money = getattr(self, field)
            if not isinstance(money, FinanceMoney):
                raise DecisionInputError(f"{field} must be FinanceMoney.")
            number(money.amount, field, minimum=Decimal(0))
            if field != "available_cash" and money.currency != "GBP":
                raise DecisionInputError(f"{field} must be GBP.")
        if not isinstance(self.entry, PriceQuote) or not isinstance(self.stop, PriceQuote):
            raise DecisionInputError("Entry and stop require explicit PriceQuote inputs.")
        immutable_items(self.targets, PriceQuote, "targets")
        if not self.targets:
            raise DecisionInputError("At least one explicit target is required.")
        if not isinstance(self.costs, TradingCosts):
            raise DecisionInputError("Explicit trading costs required, including zero costs.")
        currency = "USD" if self.entry.unit == "USD" else "GBP"
        for quote in (self.entry, self.stop, *self.targets):
            number(quote.amount, "price", minimum=Decimal(0))
            if quote.unit != self.entry.unit:
                raise DecisionInputError("Entry, stop and targets must use one price unit.")
        if self.available_cash.currency != currency or self.costs.entry_fee.currency != currency:
            raise DecisionInputError("Cash and fees must use quote cash currency.")
        if self.stop.amount >= self.entry.amount:
            raise DecisionInputError("Long stop must be strictly below entry.")
        if any(target.amount <= self.entry.amount for target in self.targets):
            raise DecisionInputError("Long targets must be strictly above entry.")
        if tuple(t.amount for t in self.targets) != tuple(sorted({t.amount for t in self.targets})):
            raise DecisionInputError("Targets must be distinct and ascending.")
        if currency == "USD":
            if not isinstance(self.fx_to_gbp, FxRate) or (
                self.fx_to_gbp.from_currency, self.fx_to_gbp.to_currency
            ) != ("USD", "GBP"):
                raise DecisionInputError("Explicit dated USD to GBP FX required.")
            number(self.fx_to_gbp.rate, "FX", minimum=Decimal(0))
        elif self.fx_to_gbp is not None:
            raise DecisionInputError("GBP quotes need no FX; do not apply FX twice.")


@dataclass(frozen=True)
class PositionSizeResult:
    readiness: DecisionReadiness
    shares: int
    risk_budget_gbp: FinanceMoney
    estimated_cash_required: FinanceMoney
    estimated_loss_gbp: FinanceMoney
    added_exposure_gbp: FinanceMoney
    target_reward_risk: tuple[Decimal, ...]
    binding_limits: tuple[str, ...]
    policy_revision_id: str | None
    assumptions: tuple[str, ...]


def calculate_position_size(policy: RiskPolicy, inputs: SizingInputs) -> PositionSizeResult:
    """Cash/risk/exposure floor including round-trip fees and adverse fills.

    Spread is full bid/ask width in original quote units; half on each side.
    Slippage is adverse per side in quote units. Entry fee consumes cash.
    Risk includes entry AND stop-exit fees. Exposure uses entry fill not fees.
    No cross-currency cash pooling, automatic FX inversion or policy updates.
    """
    if not isinstance(policy, RiskPolicy) or not isinstance(inputs, SizingInputs):
        raise DecisionInputError("RiskPolicy and SizingInputs are required.")
    zero = FinanceMoney(Decimal(0), "GBP")
    assumptions = (
        "Manual quote/FX identity and freshness unverified; no live prices.",
        "Stop fill is an estimate, not guaranteed; gaps may exceed planned loss.",
        "Fixed fees; full spread split per side; adverse slippage each side.",
        "No execution, cash pooling, leverage, fractional shares or auto policy changes.",
    )

    def blocked(reason: str, budget: FinanceMoney = zero) -> PositionSizeResult:
        return PositionSizeResult(
            DecisionReadiness(DecisionStatus.BLOCKED, (reason,)), 0, budget,
            FinanceMoney(Decimal(0), inputs.available_cash.currency), zero, zero,
            (), (), policy.revision_id, assumptions,
        )

    if not policy.enabled:
        return blocked("Risk is disabled until explicitly configured and confirmed.")
    policy.require_enabled()
    with localcontext(Context(prec=80, rounding=ROUND_HALF_EVEN)):
        number(policy.max_risk_fraction, "max_risk_fraction")
        fx = inputs.fx_to_gbp.rate if inputs.fx_to_gbp else Decimal(1)
        scale = Decimal("0.01") if inputs.entry.unit == "GBp" else Decimal(1)
        friction = (inputs.costs.spread_per_share / 2 + inputs.costs.slippage_per_side) * scale
        entry_fill = inputs.entry.per_share_money().amount + friction
        stop_fill = inputs.stop.per_share_money().amount - friction
        if stop_fill <= 0:
            return blocked("Estimated stop fill must remain positive after costs.")
        budget = min(inputs.equity_gbp.amount * policy.max_risk_fraction,
                     inputs.max_cash_risk_gbp.amount)
        budget_money = FinanceMoney(budget, "GBP")
        fees = inputs.costs.entry_fee.amount + inputs.costs.exit_fee.amount
        per_share_loss = (entry_fill - stop_fill) * fx
        capacity = inputs.exposure_limit_gbp.amount - inputs.current_exposure_gbp.amount
        if budget <= fees * fx:
            return blocked("Risk budget does not cover round-trip fees.", budget_money)
        if inputs.available_cash.amount <= inputs.costs.entry_fee.amount:
            return blocked("Available quote-currency cash does not cover entry fee.", budget_money)
        if capacity <= 0:
            return blocked("No remaining user-entered exposure capacity.", budget_money)
        ceilings = {
            "cash": int((inputs.available_cash.amount - inputs.costs.entry_fee.amount) // entry_fill),
            "risk": int((budget - fees * fx) // per_share_loss),
            "exposure": int(capacity // (entry_fill * fx)),
        }
        shares = min(ceilings.values())
        if shares <= 0:
            return blocked("Cash, risk or exposure capacity cannot fund one whole share.", budget_money)
        cash_required = shares * entry_fill + inputs.costs.entry_fee.amount
        loss = shares * per_share_loss + fees * fx
        rewards = tuple(
            ((target.per_share_money().amount - friction - entry_fill) * shares - fees) * fx / loss
            for target in inputs.targets
        )
        if any(reward <= 0 for reward in rewards):
            return blocked("At least one target has no positive net reward after costs.", budget_money)
        return PositionSizeResult(
            DecisionReadiness(DecisionStatus.COMPLETE), shares, budget_money,
            FinanceMoney(cash_required, inputs.available_cash.currency),
            FinanceMoney(loss, "GBP"), FinanceMoney(shares * entry_fill * fx, "GBP"),
            rewards, tuple(name for name, ceiling in ceilings.items() if ceiling == shares),
            policy.revision_id, assumptions,
        )


@dataclass(frozen=True)
class SavedTradePlan:
    record_id: str
    revision: int
    created_at: datetime
    identity: CompanyIdentity
    horizon: Literal["swing", "longterm"]
    thesis: str
    entry_zone: tuple[PriceQuote, PriceQuote]
    entry_trigger: str
    stop_reason: str
    targets_reason: str
    expected_holding_period: str
    catalysts: tuple[DatedCatalyst, ...]
    bull_case: str
    bear_case: str
    invalidation: str
    avoid_conditions: tuple[str, ...]
    uncertainty: tuple[str, ...]
    inputs: SizingInputs
    policy: RiskPolicy

    def __post_init__(self) -> None:
        validate_revision(self.record_id, self.revision, self.created_at)
        object.__setattr__(self, "created_at", normalize_utc(self.created_at))
        if not isinstance(self.identity, CompanyIdentity):
            raise DecisionInputError("Plan needs manual company identity.")
        if self.horizon not in ("swing", "longterm"):
            raise DecisionInputError("Swing and longterm plans must be separate.")
        for field in (
            "thesis", "entry_trigger", "stop_reason", "targets_reason",
            "expected_holding_period", "bull_case", "bear_case", "invalidation",
        ):
            text(getattr(self, field), field)
        immutable_items(self.entry_zone, PriceQuote, "entry_zone")
        if len(self.entry_zone) != 2 or not isinstance(self.inputs, SizingInputs):
            raise DecisionInputError("Plan requires two entry zone bounds and SizingInputs.")
        if not isinstance(self.policy, RiskPolicy):
            raise DecisionInputError("Plan requires an immutable policy snapshot.")
        low, high = self.entry_zone
        if low.unit != self.inputs.entry.unit or high.unit != low.unit:
            raise DecisionInputError("Entry zone must use entry quote unit.")
        if not self.inputs.stop.amount < low.amount <= self.inputs.entry.amount <= high.amount:
            raise DecisionInputError("Entry must lie inside zone above stop.")
        immutable_items(self.catalysts, DatedCatalyst, "catalysts")
        for field in ("avoid_conditions", "uncertainty"):
            immutable_items(getattr(self, field), str, field)
            if not getattr(self, field):
                raise DecisionInputError(f"Explicit {field} required.")
            for item in getattr(self, field):
                text(item, field)

    @property
    def sizing(self) -> PositionSizeResult:
        return calculate_position_size(self.policy, self.inputs)
