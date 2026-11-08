"""Transparent screens over manual observations, never authoritative signals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from edge.decision_contracts import DecisionInputError, ManualProvenance, normalize_utc
from edge.research_workflows import CompanyIdentity, immutable_items, number, text


@dataclass(frozen=True)
class FundamentalObservation:
    metric: str
    value: Decimal | None
    unit: str
    reporting_period: str
    as_of: datetime
    provenance: ManualProvenance
    classification: Literal["claimed-reported", "user-estimate", "user-derived"]

    def __post_init__(self) -> None:
        for field in ("metric", "unit", "reporting_period"):
            text(getattr(self, field), field)
        if self.value is not None:
            number(self.value, "observation value")
        object.__setattr__(self, "as_of", normalize_utc(self.as_of))
        if not isinstance(self.provenance, ManualProvenance):
            raise DecisionInputError("Observations need explicit manual source and date.")
        if self.classification not in ("claimed-reported", "user-estimate", "user-derived"):
            raise DecisionInputError("Classification must distinguish reported claims and estimates.")


@dataclass(frozen=True)
class CompanyObservations:
    identity: CompanyIdentity
    observations: tuple[FundamentalObservation, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.identity, CompanyIdentity):
            raise DecisionInputError("Manual identity is required.")
        immutable_items(self.observations, FundamentalObservation, "observations")
        if len({o.metric for o in self.observations}) != len(self.observations):
            raise DecisionInputError("Duplicate metrics require explicit user period selection.")


@dataclass(frozen=True)
class ScreenFilter:
    metric: str
    unit: str
    minimum: Decimal | None
    maximum: Decimal | None
    reporting_period: str
    not_before: datetime
    allowed_classifications: tuple[str, ...] = ("claimed-reported",)

    def __post_init__(self) -> None:
        for field in ("metric", "unit", "reporting_period"):
            text(getattr(self, field), field)
        if self.minimum is None and self.maximum is None:
            raise DecisionInputError("Filter must have at least one explicit threshold.")
        if self.minimum is not None:
            number(self.minimum, "minimum")
        if self.maximum is not None:
            number(self.maximum, "maximum")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise DecisionInputError("Minimum exceeds maximum.")
        object.__setattr__(self, "not_before", normalize_utc(self.not_before))
        immutable_items(self.allowed_classifications, str, "allowed_classifications")
        if not self.allowed_classifications or any(
            c not in ("claimed-reported", "user-estimate", "user-derived")
            for c in self.allowed_classifications
        ):
            raise DecisionInputError("Filter must explicitly select valid classifications.")


@dataclass(frozen=True)
class RankingRule:
    """Lexicographic priority order, no opaque weighted or meaningless score."""

    metric: str
    unit: str
    direction: Literal["higher", "lower"]
    reporting_period: str
    not_before: datetime
    allowed_classifications: tuple[str, ...] = ("claimed-reported",)

    def __post_init__(self) -> None:
        # Share dimension/source validation; no synthetic observations or scores.
        ScreenFilter(self.metric, self.unit, Decimal(0), None, self.reporting_period,
                     self.not_before, self.allowed_classifications)
        if self.direction not in ("higher", "lower"):
            raise DecisionInputError("Ranking direction must be higher or lower.")
        object.__setattr__(self, "not_before", normalize_utc(self.not_before))


@dataclass(frozen=True)
class DiscoveryResult:
    identity: CompanyIdentity
    included: bool
    rank: int | None
    filter_trace: tuple[str, ...]
    ranking_trace: tuple[str, ...]
    exclusions: tuple[str, ...]
    limitations: tuple[str, ...]


def screen_companies(
    companies: tuple[CompanyObservations, ...],
    filters: tuple[ScreenFilter, ...],
    ranking: tuple[RankingRule, ...],
    *,
    as_of: datetime,
) -> tuple[DiscoveryResult, ...]:
    """Exclude missing/incomparable/stale/future ranking or filter observations.

    Timestamps/periods/classifications are user declarations. Units and period
    must exactly match; no cross-currency/percent conversion, fill or inference.
    Included companies first, sorted lexicographically by explicit ranking
    priorities; ties and excluded companies sort by exchange/symbol/name.
    """
    immutable_items(companies, CompanyObservations, "companies")
    immutable_items(filters, ScreenFilter, "filters")
    immutable_items(ranking, RankingRule, "ranking")
    as_of = normalize_utc(as_of)
    identities = [(c.identity.exchange, c.identity.symbol) for c in companies]
    if len(set(identities)) != len(identities):
        raise DecisionInputError("Duplicate company identities cannot be ranked.")
    if not filters:
        raise DecisionInputError("Discovery needs at least one user-authored filter.")
    if len({r.metric for r in ranking}) != len(ranking):
        raise DecisionInputError("Ranking metrics must be distinct.")
    if any(rule.not_before > as_of for rule in (*filters, *ranking)):
        raise DecisionInputError("Freshness lower bound cannot be after screen date.")
    rows = []
    limitations = (
        "User-authored universe/identity/observations unverified; selection bias possible.",
        "Rank is ordinal lexicographic priority, not expected return or recommendation.",
        "No provider promotion, missing-value imputation or automatic unit conversion.",
    )
    for company in companies:
        observations = {o.metric: o for o in company.observations}
        exclusions: list[str] = []
        trace: list[str] = []
        ranking_trace: list[str] = []
        keys: list[Decimal] = []

        def usable(
            rule: ScreenFilter | RankingRule,
            observations: dict[str, FundamentalObservation] = observations,
            exclusions: list[str] = exclusions,
        ) -> FundamentalObservation | None:
            observation = observations.get(rule.metric)
            reason = None
            if observation is None or observation.value is None:
                reason = "missing-data"
            elif observation.unit != rule.unit:
                reason = "unit-mismatch"
            elif observation.reporting_period != rule.reporting_period:
                reason = "period-mismatch"
            elif not rule.not_before <= observation.as_of <= as_of:
                reason = "stale-or-future-date"
            elif observation.provenance.declared_at > as_of:
                reason = "future-declaration"
            elif observation.classification not in rule.allowed_classifications:
                reason = "classification-excluded"
            if reason:
                exclusions.append(f"{rule.metric}: {reason}")
                return None
            return observation

        for rule in filters:
            observation = usable(rule)
            if observation is None:
                continue
            value = observation.value
            passed = (rule.minimum is None or value >= rule.minimum) and (
                rule.maximum is None or value <= rule.maximum
            )
            trace.append(
                f"{rule.metric}={value} {rule.unit}; period={observation.reporting_period}; "
                f"as_of={observation.as_of.isoformat()}; source={observation.provenance.reference}; "
                f"class={observation.classification}; bounds=[{rule.minimum},{rule.maximum}]; "
                f"{'pass' if passed else 'fail'}"
            )
            if not passed:
                exclusions.append(f"{rule.metric}: outside-user-bounds")
        for priority, rule in enumerate(ranking, 1):
            observation = usable(rule)
            if observation is None:
                continue
            value = observation.value
            # copy_negate is exact regardless of the process Decimal context.
            keys.append(value.copy_negate() if rule.direction == "higher" else value)
            ranking_trace.append(
                f"priority {priority}: {rule.metric}={value} {rule.unit}, {rule.direction}; "
                f"period={observation.reporting_period}; as_of={observation.as_of.isoformat()}; "
                f"source={observation.provenance.reference}; class={observation.classification}"
            )
        identity_key = (company.identity.exchange, company.identity.symbol, company.identity.company_name)
        rows.append((bool(exclusions), tuple(keys) if not exclusions else (), identity_key,
                     company.identity, tuple(trace), tuple(ranking_trace), tuple(dict.fromkeys(exclusions))))
    rows.sort(key=lambda row: row[:3])
    results = []
    for index, row in enumerate(rows, 1):
        excluded, _, _, identity, trace, ranking_trace, exclusions = row
        results.append(DiscoveryResult(identity, not excluded,
                       index if ranking and not excluded else None,
                       trace, ranking_trace, exclusions, limitations))
    return tuple(results)
