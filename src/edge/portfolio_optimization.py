"""Explicit isolated skfolio research; no provider calls, orders or ledger writes.

Only synthetic panels can currently pass: real-data manifests have no accepted
independent verifier. A user's declaration never upgrades market-data rights.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, ROUND_HALF_EVEN, Context, Decimal, localcontext
from itertools import pairwise
from pathlib import Path
from typing import Literal

from edge.decision_contracts import (
    DecisionInputError,
    FinanceMoney,
    FxRate,
    PriceQuote,
    normalize_utc,
    validate_whole_shares,
)
from edge.research_workflows import immutable_items, number, text

_CONTEXT = Context(prec=80, rounding=ROUND_HALF_EVEN)
_TOLERANCE = Decimal("0.0000001")


@dataclass(frozen=True)
class AllocationAsset:
    asset_id: str
    symbol: str
    exchange: str
    currency: Literal["GBP", "USD"]
    identity_reference: str

    def __post_init__(self) -> None:
        for field in ("asset_id", "symbol", "exchange", "identity_reference"):
            text(getattr(self, field), field)
            if len(getattr(self, field)) > 200:
                raise DecisionInputError("Asset identity must be at most 200 characters.")
        if self.currency not in ("GBP", "USD"):
            raise DecisionInputError("Asset cash currency must be GBP or USD.")


@dataclass(frozen=True)
class ReturnPanel:
    """Aligned simple GBP TOTAL returns; no mixed local-currency price returns.

    Timestamps are period ends. Explicit train_count freezes a chronological
    train/holdout split before fit. No imputation or calendar guessing.
    """

    assets: tuple[AllocationAsset, ...]
    timestamps: tuple[datetime, ...]
    returns: tuple[tuple[Decimal, ...], ...]
    train_count: int
    synthetic: bool
    source: str
    calendar: str
    rights_reference: str
    corporate_actions: str
    manifest_reference: str
    reporting_currency: Literal["GBP"] = "GBP"

    def __post_init__(self) -> None:
        immutable_items(self.assets, AllocationAsset, "assets")
        if not 2 <= len(self.assets) <= 30:
            raise DecisionInputError("Return panel must have 2 to 30 assets.")
        if len({asset.asset_id for asset in self.assets}) != len(self.assets) or len({
            (asset.exchange, asset.symbol) for asset in self.assets
        }) != len(self.assets):
            raise DecisionInputError("Duplicate asset identity in panel.")
        if not isinstance(self.timestamps, tuple) or not 12 <= len(self.timestamps) <= 2000:
            raise DecisionInputError("Panel must have 12 to 2000 aligned periods.")
        dates = tuple(normalize_utc(date) for date in self.timestamps)
        if any(a >= b for a, b in pairwise(dates)):
            raise DecisionInputError("Period timestamps must be unique and strictly increasing.")
        object.__setattr__(self, "timestamps", dates)
        if not isinstance(self.returns, tuple) or len(self.returns) != len(dates):
            raise DecisionInputError("Returns must exactly align with all period timestamps.")
        for row in self.returns:
            if not isinstance(row, tuple) or len(row) != len(self.assets):
                raise DecisionInputError("No missing/extra returns or unequal row dimensions.")
            for value in row:
                number(value, "simple return", minimum=Decimal("-0.999999"))
                if value > 10:
                    raise DecisionInputError("Return exceeds explicit panel safeguards.")
        if type(self.train_count) is not int or not max(8, len(self.assets) + 2) <= self.train_count <= len(dates) - 4:
            raise DecisionInputError("Explicit split needs adequate training rows and >=4 holdout rows.")
        if type(self.synthetic) is not bool or self.reporting_currency != "GBP":
            raise DecisionInputError("Explicit synthetic flag and GBP total returns required.")
        for field in ("source", "calendar", "rights_reference", "corporate_actions", "manifest_reference"):
            text(getattr(self, field), field)
            if len(getattr(self, field)) > 200:
                raise DecisionInputError("Panel metadata limited to 200 characters.")
        if self.synthetic and self.corporate_actions != "synthetic-not-applicable":
            raise DecisionInputError("Synthetic corporate actions must be explicitly not applicable.")


@dataclass(frozen=True)
class AllocationConstraints:
    """All bounds/rates explicit. Costs are one-off fractions per weight traded.

    max_turnover is sum(abs(new-old)) over asset weights, NOT divided by two.
    invested_budget leaves 1-budget as zero-yield cash. min_net_return and
    max_variance are per input period; costs divided by holding_periods for fit.
    """

    min_weights: tuple[Decimal, ...]
    max_weights: tuple[Decimal, ...]
    previous_weights: tuple[Decimal, ...]
    invested_budget: Decimal
    max_turnover: Decimal
    max_variance: Decimal
    min_net_return: Decimal
    one_off_costs: tuple[Decimal, ...]
    holding_periods: int
    max_estimated_cost_fraction: Decimal

    def __post_init__(self) -> None:
        count = len(self.min_weights) if isinstance(self.min_weights, tuple) else 0
        if not 2 <= count <= 30:
            raise DecisionInputError("Explicit per-asset constraints need 2 to 30 assets.")
        for field in ("min_weights", "max_weights", "previous_weights", "one_off_costs"):
            immutable_items(getattr(self, field), Decimal, field)
            if len(getattr(self, field)) != count:
                raise DecisionInputError("Constraint vectors must have identical dimensions.")
            for value in getattr(self, field):
                number(value, field, minimum=Decimal(0))
                if value > 1:
                    raise DecisionInputError("Long-only fractions/costs cannot exceed one.")
        for field in ("invested_budget", "max_turnover", "max_variance", "max_estimated_cost_fraction"):
            number(getattr(self, field), field, minimum=Decimal(0))
        number(self.min_net_return, "min_net_return", minimum=Decimal("-0.999999"))
        if not Decimal("0.000001") <= self.invested_budget <= 1:
            raise DecisionInputError("Invested budget fraction must be >=0.000001 and <=1.")
        if self.max_turnover > 2 or not Decimal("1e-12") <= self.max_variance <= 100:
            raise DecisionInputError("Turnover or per-period variance limit out of bounds.")
        if self.min_net_return > 10 or self.max_estimated_cost_fraction > 1:
            raise DecisionInputError("Return/cost constraint out of bounds.")
        if type(self.holding_periods) is not int or not 1 <= self.holding_periods <= 2000:
            raise DecisionInputError("Explicit holding duration must be 1 to 2000 periods.")
        if any(a > b for a, b in zip(self.min_weights, self.max_weights, strict=True)):
            raise DecisionInputError("Minimum weight exceeds maximum.")
        with localcontext(_CONTEXT):
            if sum(self.min_weights) > self.invested_budget or sum(self.max_weights) < self.invested_budget:
                raise DecisionInputError("Weight constraints cannot satisfy invested budget.")
            if sum(self.previous_weights) > 1:
                raise DecisionInputError("Previous weights imply leverage.")


@dataclass(frozen=True)
class AllocationResult:
    status: Literal["ok", "blocked", "error"]
    reasons: tuple[str, ...]
    assets: tuple[AllocationAsset, ...] = ()
    target_weights: tuple[Decimal, ...] = ()
    estimated_period_variance: Decimal | None = None
    estimated_net_period_return: Decimal | None = None
    estimated_turnover: Decimal | None = None
    estimated_one_off_cost_fraction: Decimal | None = None
    solver_status: str | None = None
    panel_digest: str | None = None
    train_end: datetime | None = None
    holdout_start: datetime | None = None
    holdout_metrics: tuple[tuple[str, Decimal | int | None], ...] = ()
    naive_holdout_metrics: tuple[tuple[str, Decimal | int | None], ...] = ()
    limitations: tuple[str, ...] = ()
    framework_executed: bool = False
    covariance: tuple[tuple[Decimal, ...], ...] = ()
    expected_asset_returns: tuple[Decimal, ...] = ()
    constraints: AllocationConstraints | None = None


def _request(panel: ReturnPanel, constraints: AllocationConstraints) -> dict:
    if len(panel.assets) != len(constraints.min_weights):
        raise DecisionInputError("Panel/constraints asset dimensions differ.")
    return {
        "schema_version": 1, "framework": "skfolio", "operation": "target-allocation",
        "panel": {
            "synthetic": panel.synthetic,
            "assets": [
                {"asset_id": a.asset_id, "symbol": a.symbol, "exchange": a.exchange,
                 "currency": a.currency, "identity_reference": a.identity_reference}
                for a in panel.assets
            ],
            "timestamps": [date.isoformat() for date in panel.timestamps],
            "returns": [[str(value) for value in row] for row in panel.returns],
            "train_count": panel.train_count, "source": panel.source,
            "calendar": panel.calendar, "rights_reference": panel.rights_reference,
            "corporate_actions": panel.corporate_actions,
            "manifest_reference": panel.manifest_reference,
            "reporting_currency": panel.reporting_currency,
        },
        "constraints": {
            field: [str(value) for value in getattr(constraints, field)] for field in (
                "min_weights", "max_weights", "previous_weights", "one_off_costs",
            )
        } | {
            field: str(getattr(constraints, field)) for field in (
                "invested_budget", "max_turnover", "max_variance", "min_net_return",
                "max_estimated_cost_fraction",
            )
        } | {"holding_periods": constraints.holding_periods},
    }


def _run(project_root: Path, request: dict, timeout_seconds: int):
    # Shared owner handles subprocess environment/path/timeout/output safeguards.
    from edge.research_workers import run_research_worker

    return run_research_worker(
        project_root=project_root, framework="skfolio", request=request,
        timeout_seconds=timeout_seconds, max_response_bytes=200_000,
    )


def _response_decimal(value: object) -> Decimal:
    if not isinstance(value, str) or len(value) > 64:
        raise DecisionInputError("Solver output requires bounded decimal strings.")
    try:
        result = Decimal(value)
    except ArithmeticError:
        raise DecisionInputError("Invalid solver decimal.") from None
    if not result.is_finite() or result.copy_abs() > Decimal("1e18"):
        raise DecisionInputError("Nonfinite or excessive solver output.")
    return result


def _metrics(value: object, expected_periods: int) -> tuple[tuple[str, Decimal | int | None], ...]:
    expected = {
        "total_return", "max_drawdown", "mean_period_return", "period_volatility",
        "mean_over_volatility", "positive_period_fraction", "average_positive_period",
        "average_negative_period", "period_profit_factor", "initial_cost_fraction",
        "periods", "trade_count",
    }
    if not isinstance(value, dict) or set(value) != expected or type(value["periods"]) is not int:
        raise DecisionInputError("Invalid holdout metrics.")
    if value["periods"] != expected_periods or value["trade_count"] is not None:
        raise DecisionInputError("Holdout is statistical, not trade simulation.")
    output = []
    for key in sorted(value):
        item = value[key]
        output.append((key, item if key == "periods" or item is None else _response_decimal(item)))
    return tuple(output)


def _parse_response(response: object, request: dict, panel: ReturnPanel,
                    constraints: AllocationConstraints) -> AllocationResult:
    if not isinstance(response, dict) or type(response.get("schema_version")) is not int or response.get("schema_version") != 1 or response.get("framework") != "skfolio":
        raise DecisionInputError("Worker response envelope mismatch.")
    if "request_sha256" in response:
        expected_hash = hashlib.sha256(
            json.dumps(request, allow_nan=False).encode("utf-8")
        ).hexdigest()
        if response["request_sha256"] != expected_hash:
            raise DecisionInputError("Worker transport request digest mismatch.")
        response = {key: value for key, value in response.items() if key != "request_sha256"}
    status = response.get("status")
    if status in ("blocked", "error"):
        if set(response) != {"schema_version", "framework", "status", "reason"}:
            raise DecisionInputError("Malformed failure envelope.")
        text(response["reason"], "worker reason")
        return AllocationResult(status, (response["reason"],))
    if status != "ok" or set(response) != {"schema_version", "framework", "status", "result"}:
        raise DecisionInputError("Malformed success envelope.")
    result = response["result"]
    expected = {
        "package_version", "solver", "solver_status", "panel_digest", "weights",
        "estimated_period_variance", "estimated_net_period_return", "estimated_turnover",
        "estimated_one_off_cost_fraction", "train_count", "holdout_count",
        "train_end", "holdout_start", "holdout", "naive_holdout", "naive_weights", "limitations",
        "covariance", "expected_asset_returns",
    }
    if not isinstance(result, dict) or set(result) != expected:
        raise DecisionInputError("Malformed solver payload.")
    if result["package_version"] != "1.12.5" or result["solver"] != "CLARABEL" or result["solver_status"] != "optimal":
        raise DecisionInputError("Pinned skfolio/optimal CLARABEL result required.")
    digest = hashlib.sha256(json.dumps(request["panel"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if result["panel_digest"] != digest:
        raise DecisionInputError("Returned panel digest does not match request.")
    if type(result["train_count"]) is not int or type(result["holdout_count"]) is not int or (
        result["train_count"], result["holdout_count"], result["train_end"], result["holdout_start"]
    ) != (
        panel.train_count, len(panel.timestamps) - panel.train_count,
        panel.timestamps[panel.train_count - 1].isoformat(), panel.timestamps[panel.train_count].isoformat(),
    ):
        raise DecisionInputError("Returned chronological split does not match request.")
    if not isinstance(result["weights"], list) or len(result["weights"]) != len(panel.assets):
        raise DecisionInputError("Returned weight dimension mismatch.")
    weights = tuple(_response_decimal(item) for item in result["weights"])
    variance = _response_decimal(result["estimated_period_variance"])
    net_return = _response_decimal(result["estimated_net_period_return"])
    reported_turnover = _response_decimal(result["estimated_turnover"])
    reported_cost = _response_decimal(result["estimated_one_off_cost_fraction"])
    count = len(weights)
    if not isinstance(result["covariance"], list) or len(result["covariance"]) != count or any(
        not isinstance(row, list) or len(row) != count for row in result["covariance"]
    ):
        raise DecisionInputError("Invalid estimated covariance dimensions.")
    covariance = tuple(tuple(_response_decimal(item) for item in row) for row in result["covariance"])
    if not isinstance(result["expected_asset_returns"], list) or len(result["expected_asset_returns"]) != count:
        raise DecisionInputError("Invalid estimated asset return dimensions.")
    expected_returns = tuple(_response_decimal(item) for item in result["expected_asset_returns"])
    if not isinstance(result["naive_weights"], list) or len(result["naive_weights"]) != count:
        raise DecisionInputError("Invalid naive benchmark dimensions.")
    naive = tuple(_response_decimal(item) for item in result["naive_weights"])
    with localcontext(_CONTEXT):
        if any(abs(value - constraints.invested_budget / count) > _TOLERANCE for value in naive):
            raise DecisionInputError("Naive benchmark must be equal-weight with the same cash budget.")
        if any(covariance[i][i] < 0 or abs(covariance[i][j] - covariance[j][i]) > Decimal("1e-12")
               for i in range(count) for j in range(count)):
            raise DecisionInputError("Invalid symmetric training covariance.")
        recomputed_variance = sum(weights[i] * covariance[i][j] * weights[j]
                                  for i in range(count) for j in range(count))
        recomputed_return = sum(w * mean for w, mean in zip(weights, expected_returns, strict=True)) - reported_cost / constraints.holding_periods
        if abs(recomputed_variance - variance) > _TOLERANCE or abs(recomputed_return - net_return) > _TOLERANCE:
            raise DecisionInputError("Estimated risk/return trace inconsistent with training distribution.")
        for w, low, high in zip(weights, constraints.min_weights, constraints.max_weights, strict=True):
            if not low - _TOLERANCE <= w <= high + _TOLERANCE:
                raise DecisionInputError("Returned weights violate user constraints.")
        if abs(sum(weights) - constraints.invested_budget) > _TOLERANCE:
            raise DecisionInputError("Returned budget exceeds solver tolerance.")
        turnover = sum(abs(w - previous) for w, previous in zip(weights, constraints.previous_weights, strict=True))
        cost = sum(rate * abs(w - previous) for rate, w, previous in zip(
            constraints.one_off_costs, weights, constraints.previous_weights, strict=True
        ))
        if turnover > constraints.max_turnover + _TOLERANCE or abs(turnover - reported_turnover) > _TOLERANCE:
            raise DecisionInputError("Returned turnover violates user limit or trace.")
        if cost > constraints.max_estimated_cost_fraction + _TOLERANCE or abs(cost - reported_cost) > _TOLERANCE:
            raise DecisionInputError("Returned costs violate user limit or trace.")
        if variance < 0 or variance > constraints.max_variance + _TOLERANCE or net_return < constraints.min_net_return - _TOLERANCE:
            raise DecisionInputError("Returned estimated risk/net return violates explicit limit.")
    if not isinstance(result["limitations"], list) or not 1 <= len(result["limitations"]) <= 20:
        raise DecisionInputError("Explicit uncertainty/holdout limitations required.")
    for item in result["limitations"]:
        text(item, "limitation")
    return AllocationResult(
        "ok", (), panel.assets, weights, variance, net_return, reported_turnover, reported_cost,
        result["solver_status"], digest, panel.timestamps[panel.train_count - 1],
        panel.timestamps[panel.train_count],
        _metrics(result["holdout"], len(panel.timestamps) - panel.train_count),
        _metrics(result["naive_holdout"], len(panel.timestamps) - panel.train_count),
        tuple(result["limitations"]), True, covariance, expected_returns, constraints,
    )


def optimize_allocation(panel: ReturnPanel, constraints: AllocationConstraints, *,
                        project_root: Path, timeout_seconds: int) -> AllocationResult:
    """Explicit execution only. Importing/rendering readiness never starts a worker."""
    if not isinstance(panel, ReturnPanel) or not isinstance(constraints, AllocationConstraints):
        raise DecisionInputError("Typed ReturnPanel/AllocationConstraints required.")
    if not isinstance(project_root, Path) or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise DecisionInputError("Explicit project Path and 1..300-second timeout required.")
    request = _request(panel, constraints)
    if not panel.synthetic:
        return AllocationResult("blocked", (
            "Real histories blocked: independently accepted panel manifest verifier absent.",
            "Manual provenance/rights declarations do not confer market-data acceptance.",
        ))
    if len(json.dumps(request, allow_nan=False).encode("utf-8")) > 1_048_576:
        return AllocationResult("blocked", ("Panel exceeds shared worker 1 MiB request limit.",))
    try:
        execution = _run(project_root, request, timeout_seconds)
    except (ImportError, OSError):
        return AllocationResult("blocked", ("Isolated skfolio worker/runtime unavailable.",))
    if execution.response is None:
        return AllocationResult(
            execution.status if execution.status in ("blocked", "error") else "error",
            (execution.error or "Worker failed without a response; no allocation.",),
        )
    try:
        return _parse_response(execution.response, request, panel, constraints)
    except (DecisionInputError, ValueError, TypeError, ArithmeticError, KeyError):
        return AllocationResult("error", ("Worker response failed validation; no allocation.",))


def skfolio_synthetic_inputs(
    fixture_id: Literal["minimum-variance-three-assets"],
) -> tuple[ReturnPanel, AllocationConstraints]:
    """Fixed educational fixture, never defaults for user portfolios/risk policy."""
    if fixture_id != "minimum-variance-three-assets":
        raise DecisionInputError("Unknown allowlisted skfolio synthetic fixture.")
    assets = tuple(
        AllocationAsset(name, name, "SYNTHETIC", "GBP", "Generated fixture identity")
        for name in ("FIX_A", "FIX_B", "FIX_C")
    )
    start = datetime(2025, 1, 1, tzinfo=UTC)
    with localcontext(_CONTEXT):
        rows = tuple(
            tuple(Decimal((i * multiplier) % modulus - modulus // 2) / Decimal(divisor)
                  + Decimal(".0005")
                  for multiplier, modulus, divisor in ((7, 19, 1000), (3, 13, 2000), (11, 23, 2000)))
            for i in range(80)
        )
    panel = ReturnPanel(
        assets, tuple(start + timedelta(days=i) for i in range(80)), rows, 50,
        True, "Deterministic generated fixture", "Synthetic aligned daily periods",
        "Synthetic generated; no vendor license", "synthetic-not-applicable",
        "minimum-variance-three-assets-v1",
    )
    constraints = AllocationConstraints(
        (Decimal(0),) * 3, (Decimal(".7"),) * 3, (Decimal(0),) * 3,
        Decimal(".8"), Decimal(1), Decimal(".01"), Decimal("-.1"),
        (Decimal(".001"),) * 3, 20, Decimal(".01"),
    )
    return panel, constraints


def skfolio_synthetic_request(
    fixture_id: Literal["minimum-variance-three-assets"],
) -> dict:
    """Allowlisted payload for shared runner; no random data/network/user defaults."""
    return _request(*skfolio_synthetic_inputs(fixture_id))


def allocation_readiness(panel: ReturnPanel, *, project_root: Path) -> tuple[str, ...]:
    """Read-only filesystem hints, not framework or data acceptance; no execution."""
    if not isinstance(panel, ReturnPanel) or not isinstance(project_root, Path):
        raise DecisionInputError("Typed ReturnPanel and explicit project Path required.")
    reasons = []
    if not panel.synthetic:
        reasons.append("Real histories blocked: independently accepted panel manifest verifier absent.")
    try:
        from edge.research_workers import research_runtime_readiness
        reasons.extend(research_runtime_readiness("skfolio", project_root=project_root))
    except ImportError:
        reasons.append("Shared research worker readiness unavailable.")
    # Filesystem presence is never a package/solver smoke or acceptance certificate.
    reasons.append("Explicit worker execution is required to verify pinned package and solver availability.")
    return tuple(reasons)


@dataclass(frozen=True)
class TranslationAsset:
    asset_id: str
    current_shares: int
    quote: PriceQuote
    fx_to_gbp: FxRate | None
    buy_fee: FinanceMoney
    sell_fee: FinanceMoney
    spread_per_share: Decimal
    slippage_per_side: Decimal

    def __post_init__(self) -> None:
        text(self.asset_id, "asset_id")
        validate_whole_shares(self.current_shares, allow_zero=True)
        if not isinstance(self.quote, PriceQuote):
            raise DecisionInputError("Explicit manual price required.")
        currency = "USD" if self.quote.unit == "USD" else "GBP"
        number(self.quote.amount, "quote", minimum=Decimal(0))
        for fee in (self.buy_fee, self.sell_fee):
            if not isinstance(fee, FinanceMoney) or fee.currency != currency:
                raise DecisionInputError("Fees must use the quote cash currency.")
            number(fee.amount, "fee", minimum=Decimal(0))
        for field in ("spread_per_share", "slippage_per_side"):
            number(getattr(self, field), field, minimum=Decimal(0))
        if currency == "USD":
            if not isinstance(self.fx_to_gbp, FxRate) or (
                self.fx_to_gbp.from_currency, self.fx_to_gbp.to_currency
            ) != ("USD", "GBP"):
                raise DecisionInputError("Explicit USD->GBP rate required; no inversion.")
        elif self.fx_to_gbp is not None:
            raise DecisionInputError("No FX required for GBP/GBp quotes.")


@dataclass(frozen=True)
class TargetRecommendation:
    asset_id: str
    target_weight: Decimal
    target_shares: int
    delta_shares: int
    target_exposure_gbp: FinanceMoney
    estimated_cost_gbp: FinanceMoney


@dataclass(frozen=True)
class TranslationResult:
    status: Literal["ok", "blocked"]
    reasons: tuple[str, ...]
    recommendations: tuple[TargetRecommendation, ...] = ()
    rounding_cash_gbp: FinanceMoney | None = None
    estimated_cost_gbp: FinanceMoney | None = None
    limitations: tuple[str, ...] = (
        "Recommendations only: no holdings mutation, orders or automatic cash conversion.",
        "Whole-share flooring changes weights; bounds/turnover/estimated risk rechecked, not guaranteed loss.",
        "Synthetic allocation is educational; manual quotes/FX unverified.",
        "Gross purchases need existing currency cash; hypothetical sale proceeds are not funding.",
        "rounding_cash_gbp is a theoretical unallocated GBP valuation, not converted/spendable cash.",
        "No stop-loss risk sizing or execution simulation; separate confirmed trade plan required.",
    )


def translate_targets(allocation: AllocationResult, assets: tuple[TranslationAsset, ...], *,
                      constraints: AllocationConstraints,
                      portfolio_value_gbp: FinanceMoney, available_cash: tuple[FinanceMoney, ...],
                      max_cost_gbp: FinanceMoney, not_before: datetime,
                      as_of: datetime) -> TranslationResult:
    """Conservative target shares with explicit dated quotes/cost/currency funding.

    Buys floor target budget / adverse buy price incl fee. Sales expose intended
    reduction only; proceeds are not assumed available or converted to GBP.
    """
    if not isinstance(allocation, AllocationResult) or allocation.status != "ok" or not allocation.framework_executed:
        return TranslationResult("blocked", ("A validated executed allocation is required.",))
    immutable_items(assets, TranslationAsset, "translation assets")
    immutable_items(available_cash, FinanceMoney, "available_cash")
    if not isinstance(constraints, AllocationConstraints) or len(constraints.min_weights) != len(assets):
        raise DecisionInputError("Matching explicit allocation constraints required.")
    if constraints != allocation.constraints:
        return TranslationResult("blocked", ("Changed optimization constraints require a new executed solve.",))
    if any(weight < 0 for weight in allocation.target_weights):
        return TranslationResult("blocked", ("Numerical negative weights cannot become long-only share recommendations.",))
    if len(allocation.covariance) != len(assets) or len(allocation.expected_asset_returns) != len(assets):
        return TranslationResult("blocked", ("Training covariance/return trace unavailable.",))
    if tuple(asset.asset_id for asset in assets) != tuple(asset.asset_id for asset in allocation.assets):
        raise DecisionInputError("Translation identities/order must match allocation exactly.")
    for value, label in ((portfolio_value_gbp, "portfolio_value_gbp"), (max_cost_gbp, "max_cost_gbp")):
        if not isinstance(value, FinanceMoney) or value.currency != "GBP":
            raise DecisionInputError(f"{label} must be GBP money.")
        number(value.amount, label, minimum=Decimal(0))
    if portfolio_value_gbp.amount <= 0:
        raise DecisionInputError("Portfolio value must be positive.")
    cash = {}
    for item in available_cash:
        number(item.amount, "available cash", minimum=Decimal(0))
        if item.currency in cash:
            raise DecisionInputError("Duplicate currency cash balances.")
        cash[item.currency] = item.amount
    not_before, as_of = normalize_utc(not_before), normalize_utc(as_of)
    if not_before > as_of:
        raise DecisionInputError("Freshness interval cannot be reversed.")
    with localcontext(_CONTEXT):
        recommendations = []
        gross_buys = {"GBP": Decimal(0), "USD": Decimal(0)}
        total_cost = Decimal(0)
        total_exposure = Decimal(0)
        for asset, identity, weight in zip(assets, allocation.assets, allocation.target_weights, strict=True):
            times = [asset.quote.as_of, asset.quote.provenance.declared_at]
            if asset.fx_to_gbp:
                times.extend((asset.fx_to_gbp.as_of, asset.fx_to_gbp.provenance.declared_at))
            if any(not not_before <= date <= as_of for date in times):
                return TranslationResult("blocked", ("Quote/FX source dates outside explicit freshness interval.",))
            money = asset.quote.per_share_money()
            if identity.currency != money.currency:
                raise DecisionInputError("Quote currency does not match allocation asset identity.")
            fx = asset.fx_to_gbp.rate if asset.fx_to_gbp else Decimal(1)
            scale = Decimal(".01") if asset.quote.unit == "GBp" else Decimal(1)
            friction = (asset.spread_per_share / 2 + asset.slippage_per_side) * scale
            buy_fill = money.amount + friction
            if money.amount - friction <= 0:
                return TranslationResult("blocked", ("Adverse sell fill would be nonpositive.",))
            target_budget = portfolio_value_gbp.amount * weight
            # Gross target budget reserves one buy fee, even for currently held shares.
            budget_cash = max(Decimal(0), target_budget / fx - asset.buy_fee.amount)
            quantity = int((budget_cash / buy_fill).to_integral_value(rounding=ROUND_FLOOR))
            delta = quantity - asset.current_shares
            fee = asset.buy_fee.amount if delta > 0 else asset.sell_fee.amount if delta < 0 else Decimal(0)
            costs = (abs(delta) * friction + fee) * fx
            if delta > 0:
                gross_buys[money.currency] += delta * buy_fill + fee
            else:
                # Conservatively require existing cash for sell fees as well.
                gross_buys[money.currency] += fee
            exposure = quantity * money.amount * fx
            total_exposure += exposure
            total_cost += costs
            recommendations.append(TargetRecommendation(
                asset.asset_id, weight, quantity, delta, FinanceMoney(exposure, "GBP"),
                FinanceMoney(costs, "GBP"),
            ))
        if any(required > cash.get(currency, Decimal(0)) for currency, required in gross_buys.items()):
            return TranslationResult("blocked", ("Insufficient separate-currency cash for gross purchases/fees.",))
        if total_cost > max_cost_gbp.amount:
            return TranslationResult("blocked", ("Estimated costs exceed explicit user cash-cost limit.",))
        if total_exposure + total_cost > portfolio_value_gbp.amount:
            return TranslationResult("blocked", ("Rounded targets plus costs exceed portfolio value.",))
        rounded_weights = tuple(item.target_exposure_gbp.amount / portfolio_value_gbp.amount
                                for item in recommendations)
        if any(not low <= weight <= high for low, weight, high in zip(
            constraints.min_weights, rounded_weights, constraints.max_weights, strict=True
        )) or sum(rounded_weights) > constraints.invested_budget:
            return TranslationResult("blocked", ("Whole-share rounding violates user weight/budget bounds.",))
        actual_previous = tuple(
            asset.current_shares * asset.quote.per_share_money().amount *
            (asset.fx_to_gbp.rate if asset.fx_to_gbp else Decimal(1)) / portfolio_value_gbp.amount
            for asset in assets
        )
        # User weights must describe the quoted holdings; no silent reinterpretation.
        if any(abs(a - b) > _TOLERANCE for a, b in zip(
            actual_previous, constraints.previous_weights, strict=True
        )):
            return TranslationResult("blocked", ("User previous weights do not match explicit quoted holdings.",))
        turnover = sum(abs(a - b) for a, b in zip(rounded_weights, actual_previous, strict=True))
        count = len(assets)
        variance = sum(rounded_weights[i] * allocation.covariance[i][j] * rounded_weights[j]
                       for i in range(count) for j in range(count))
        net_return = sum(w * mean for w, mean in zip(
            rounded_weights, allocation.expected_asset_returns, strict=True
        )) - total_cost / portfolio_value_gbp.amount / constraints.holding_periods
        if turnover > constraints.max_turnover or variance > constraints.max_variance or net_return < constraints.min_net_return:
            return TranslationResult("blocked", ("Rounded allocation violates turnover/estimated risk/net return limit.",))
        if total_cost / portfolio_value_gbp.amount > constraints.max_estimated_cost_fraction:
            return TranslationResult("blocked", ("Rounded allocation violates estimated cost fraction limit.",))
        return TranslationResult(
            "ok", (), tuple(recommendations),
            FinanceMoney(portfolio_value_gbp.amount - total_exposure - total_cost, "GBP"),
            FinanceMoney(total_cost, "GBP"),
        )
