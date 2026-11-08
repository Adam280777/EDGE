"""Local manual alert evaluation; neither scheduling nor provider access."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from edge.decision_contracts import (
    DecisionInputError,
    ManualProvenance,
    PriceQuote,
    normalize_utc,
)


@dataclass(frozen=True)
class MonitoringRule:
    rule_id: str
    symbol: str
    market: Literal["US", "UK"]
    unit: Literal["USD", "GBP", "GBp"]
    stop: Decimal | None
    target: Decimal | None
    thesis_status: Literal["unchanged", "review", "invalidated"]
    max_age_seconds: int
    provenance: ManualProvenance

    def __post_init__(self):
        import re

        if (
            not re.fullmatch(r"[a-f0-9]{32}", self.rule_id)
            or not re.fullmatch(r"[A-Z0-9][A-Z0-9.^:/-]{0,31}", self.symbol)
            or (self.market, self.unit) not in (
                ("US", "USD"), ("UK", "GBP"), ("UK", "GBp")
            )
            or self.thesis_status not in ("unchanged", "review", "invalidated")
            or type(self.max_age_seconds) is not int
            or not 1 <= self.max_age_seconds <= 2592000
            or not isinstance(self.provenance, ManualProvenance)
        ):
            raise DecisionInputError("Invalid manual monitoring rule.")
        for value in (self.stop, self.target):
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            ):
                raise DecisionInputError("Thresholds must be positive exact decimals.")
        if self.stop is not None and self.target is not None and self.stop >= self.target:
            raise DecisionInputError("Stop must be below target for a long-only rule.")
        if self.stop is None and self.target is None and self.thesis_status == "unchanged":
            raise DecisionInputError("Supply a threshold or a thesis review status.")


@dataclass(frozen=True)
class MonitoringFinding:
    rule_id: str
    reason: str
    observation_at: datetime | None


def evaluate(
    rule: MonitoringRule, quote: PriceQuote | None, *, as_of: datetime
) -> tuple[MonitoringFinding, ...]:
    now = normalize_utc(as_of)
    reasons = []
    if rule.thesis_status != "unchanged":
        reasons.append(f"thesis_{rule.thesis_status}_user_declared")
    observed = quote.as_of if quote else None
    if rule.stop is not None or rule.target is not None:
        if quote is None:
            reasons.append("quote_missing_no_threshold_evaluation")
        elif quote.unit != rule.unit:
            reasons.append("quote_unit_mismatch_no_threshold_evaluation")
        elif quote.as_of > now or quote.provenance.declared_at > now:
            reasons.append("quote_future_no_threshold_evaluation")
        elif now - quote.as_of > timedelta(seconds=rule.max_age_seconds):
            reasons.append("quote_stale_no_threshold_evaluation")
        else:
            if rule.stop is not None and quote.amount <= rule.stop:
                reasons.append("manual_quote_at_or_below_stop")
            if rule.target is not None and quote.amount >= rule.target:
                reasons.append("manual_quote_at_or_above_target")
    return tuple(MonitoringFinding(rule.rule_id, reason, observed) for reason in reasons)
