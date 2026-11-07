"""Provider-independent, in-memory daily market-data contract.

Real-provider data returned here is for explicitly acknowledged personal
research only. It is not persisted, not approved for calculations, and must not
be passed to Nautilus until source rights, adjustment basis, action coverage,
and accounting have been independently accepted and tested.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Literal, Protocol

from edge.errors import EdgeError

type RawRecord = Mapping[str, object]
_SYMBOL_RE = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")
_ALLOWED_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "NYSE AMERICAN"})
MAX_STARTER_RANGE_DAYS = 5 * 366
MAX_PROVIDER_CALLS = 4
MAX_PROVIDER_RECORDS = 5_000
MAX_ACTION_RECORDS = 1_000
# Reviewed documentation, not proof of equity /full field adjustment semantics
# or provider observation/publication timestamps. Shared FAQs are indirect.
FMP_BASIS_SOURCES = (
    "https://site.financialmodelingprep.com/developer/docs/stable/historical-price-eod-full",
    "https://site.financialmodelingprep.com/developer/docs/stable/index-historical-price-eod-light",
    "https://site.financialmodelingprep.com/how-to/fmp-historical-price-apis-from-light-charts-to-dividendadjusted-analysis",
)
_MISSING_TEXT = frozenset({"", "none", "null", "nan", "nat"})
# Diagnostics are a closed vocabulary, never provider text or field values.
VALIDATION_STAGES = frozenset(
    {"snapshot", "profile", "history", "splits", "dividends", "provenance"}
)
VALIDATION_REASONS = frozenset(
    {
        "invalid_record", "payload_type", "collection_shape", "json_invalid",
        "response_shape", "record_shape", "profile_count", "record_limit",
        "text_invalid", "symbol_mismatch", "name_length", "listing_unsupported",
        "currency_unsupported", "inactive_security", "type_flags_invalid",
        "security_type_unsupported", "identifier_invalid", "identifiers_missing",
        "date_invalid", "date_outside_window", "duplicate_bar", "number_invalid",
        "number_range", "volume_invalid", "ohlc_inconsistent", "vwap_range",
        "split_ratio_invalid", "duplicate_action", "ambiguous_action",
        "action_date_order", "timestamp_invalid",
        "timestamp_order", "api_version_missing", "endpoint_mismatch", "history_empty",
    }
)
VALIDATION_FIELDS = frozenset(
    {
        "none", "symbol", "company_name", "exchange", "country", "currency",
        "is_actively_trading", "type_flags", "cik", "isin", "cusip",
        "date", "open", "high", "low", "close", "volume", "vwap", "adj_close",
        "numerator", "denominator", "dividend", "adj_dividend", "record_date",
        "payment_date", "declaration_date", "retrieved_at_start", "retrieved_at_end",
        "api_version", "endpoints",
        "split_type",
    }
)
_FIELD_CODES = {
    "companyName": "company_name", "isActivelyTrading": "is_actively_trading",
    "adjClose": "adj_close", "adjDividend": "adj_dividend",
    "bar date": "date", "split date": "date", "dividend date": "date",
    "split numerator": "numerator", "split denominator": "denominator",
    "recordDate": "record_date", "paymentDate": "payment_date",
    "declarationDate": "declaration_date",
    "splitType": "split_type",
}


class MarketDataError(EdgeError):
    """Base error for real market-data request and validation failures."""

    def __init__(
        self, message: str, *, reason: str = "invalid_record", field: str = "none"
    ) -> None:
        super().__init__(message)
        self.validation_stage = "snapshot"
        self.validation_reason = reason
        self.validation_field = _FIELD_CODES.get(field, field)
        self.identity_validated = False


class MarketDataConfigurationError(MarketDataError):
    """Configuration, acknowledgement, or provider selection is invalid."""


class MarketDataInputError(MarketDataError):
    """The requested ticker or date window is invalid."""


class MarketDataProviderError(MarketDataError):
    """A provider request failed; provider response bodies are never exposed."""


class MarketDataAccessError(MarketDataProviderError):
    """Credentials or provider/account endpoint access were rejected."""


class MarketDataRateLimitError(MarketDataProviderError):
    """The provider rejected the request due to a rate or quota limit."""


class MarketDataNotFoundError(MarketDataProviderError):
    """The provider did not return the requested security or history."""


class MarketDataDataError(MarketDataError):
    """Provider records are incomplete, malformed, duplicated, or inconsistent."""


@contextmanager
def validation_stage(
    stage: str, *, identity_validated: bool = False
) -> Iterator[None]:
    """Attach only the completed validation progress, not HTTP success."""
    try:
        yield
    except (MarketDataDataError, MarketDataNotFoundError) as error:
        error.validation_stage = stage
        error.identity_validated = identity_validated
        raise


@dataclass(frozen=True)
class MarketDataConfig:
    """Non-secret provider selection and conservative request ceilings.

    ``plan_label`` is descriptive only. It is never used to infer endpoint
    entitlement. FMP's account response remains authoritative.
    """

    provider: str = "fmp"
    plan_label: str = "starter"
    max_history_days: int = MAX_STARTER_RANGE_DAYS
    max_calls_per_snapshot: int = MAX_PROVIDER_CALLS
    timeout_seconds: float = 10.0
    max_response_bytes: int = 4 * 1024 * 1024


@dataclass(frozen=True)
class DataUseAcknowledgement:
    """User declarations required before a network request; not legal clearance."""

    personal_research_permitted: bool = False
    requested_endpoints_entitled: bool = False


@dataclass(frozen=True)
class MarketDataRequest:
    symbol: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    api_version: str
    endpoints: tuple[str, ...]


@dataclass(frozen=True)
class ProviderPayload:
    """Untrusted JSON-derived records and request provenance from an adapter."""

    profile: RawRecord
    bars: tuple[RawRecord, ...]
    splits: tuple[RawRecord, ...]
    dividends: tuple[RawRecord, ...]
    endpoints: tuple[str, ...]
    api_version: str
    retrieved_at_start: datetime
    retrieved_at_end: datetime


class MarketDataProvider(Protocol):
    """Replaceable adapter contract; implementations must not persist/cache."""

    name: str

    def describe(self) -> ProviderInfo: ...

    def fetch(
        self,
        request: MarketDataRequest,
        config: MarketDataConfig,
        acknowledgement: DataUseAcknowledgement,
    ) -> ProviderPayload: ...


@dataclass(frozen=True)
class SecurityIdentity:
    symbol: str
    company_name: str
    exchange: str
    country: str
    security_type: str
    currency: str
    price_unit: str
    unit_scale: Decimal
    cik: str | None
    isin: str | None
    cusip: str | None


@dataclass(frozen=True)
class DailyBar:
    session_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    volume_unit: str
    vwap: Decimal | None
    adjusted_close: Decimal | None
    adjusted_close_basis: str
    ohlc_adjustment_basis: str
    close_adjustment_basis: str = "unknown"
    volume_adjustment_basis: str = "unknown"
    vwap_adjustment_basis: str = "unknown"


@dataclass(frozen=True)
class CorporateAction:
    event_date: date
    kind: str
    numerator: int | None
    denominator: int | None
    amount: Decimal | None
    adjusted_amount: Decimal | None
    currency: str
    record_date: date | None
    payment_date: date | None
    declaration_date: date | None
    provider_split_type: str | None = None
    adjusted_amount_basis: str = "unknown"


@dataclass(frozen=True)
class ActionCoverageEvidence:
    """Observed response bounds only; neither bracketing nor a short list is proof."""

    kind: str
    returned_count: int
    in_window_count: int
    earliest_returned_date: date | None
    latest_returned_date: date | None
    response_limit_reached: bool
    window_bracketed_by_returned_dates: bool
    events_without_bar: tuple[date, ...]
    completeness: str
    outstanding_reason: str


@dataclass(frozen=True)
class SessionCoverageEvidence:
    """Calendar-day distances are observations, never inferred trading sessions."""

    first_observed_date: date
    last_observed_date: date
    leading_calendar_days: int
    trailing_calendar_days: int
    retrieval_lag_calendar_days: int
    possible_weekday_gaps: tuple[tuple[date, date], ...]
    session_coverage: str = "unverified_no_exchange_calendar"
    freshness: str = "unknown_no_publication_timestamp_or_session_calendar"
    point_in_time: str = "unverified_no_publication_or_revision_history"


@dataclass(frozen=True)
class ResearchUsePolicy:
    """Local permission boundary; not an entitlement or legal determination."""

    research_display_eligible: bool = False
    calculation_eligible: bool = False
    backtest_eligible: bool = False
    storage_allowed: bool = False
    nautilus_eligible: bool = False
    redistribution_allowed: bool = False
    display_scope: str = "private_local_ephemeral_research_only"
    display_requirements: tuple[str, ...] = (
        "label_historical_eod_not_realtime",
        "show_source_retrieval_window_and_all_limitations",
        "do_not_label_as_current_or_point_in_time_verified",
    )
    calculation_blockers: tuple[str, ...] = (
        "whole_ohlc_volume_vwap_basis_unverified",
        "action_completeness_unverified",
        "action_adjustment_and_cash_accounting_not_accepted",
        "expected_sessions_and_freshness_unverified",
        "publication_and_revision_history_unavailable",
        "calculation_rights_not_independently_accepted",
    )


@dataclass(frozen=True)
class MarketDataProvenance:
    provider: str
    api_version: str
    plan_label: str
    endpoints: tuple[str, ...]
    requested_start: date
    requested_end: date
    retrieved_at_start: datetime
    retrieved_at_end: datetime
    publication_time: datetime | None
    timeliness: str
    cache_used: bool
    user_rights_declaration: str
    action_coverage: str
    price_basis_sources: tuple[str, ...] = FMP_BASIS_SOURCES
    price_basis_evidence: str = "insufficient_endpoint_specific_adjustment_evidence"
    action_coverage_sources: tuple[str, ...] = (
        "https://site.financialmodelingprep.com/developer/docs/stable/splits-company",
        "https://site.financialmodelingprep.com/developer/docs/stable/dividends-company",
    )


@dataclass(frozen=True)
class MarketDataSnapshot:
    identity: SecurityIdentity
    bars: tuple[DailyBar, ...]
    actions: tuple[CorporateAction, ...]
    provenance: MarketDataProvenance
    warnings: tuple[str, ...]
    calculation_eligible: bool
    storage_allowed: bool
    action_evidence: tuple[ActionCoverageEvidence, ...]
    session_evidence: SessionCoverageEvidence
    use_policy: ResearchUsePolicy


@dataclass(frozen=True)
class AcceptanceGate:
    """One scoped check, not a provider/account-wide certification.

    Reason codes are static diagnostics. Evidence describes observations, not
    inferred sessions, legal clearance, or field-name adjustment semantics.
    """

    gate: str
    state: Literal["pass", "unresolved", "failed"]
    reason_codes: tuple[str, ...]
    evidence: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    observations: tuple[tuple[str, str | int | bool | date | datetime | None], ...] = ()


@dataclass(frozen=True)
class AcceptanceUseDecision:
    intended_use: str
    eligible: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceReport:
    """Immutable decision about this snapshot, as retrieved, never 'now'.

    ``publication_time`` is a reported timestamp, NOT verified historical
    availability. No clock, I/O, account lookup, or mutation is used. Blockers
    are gate IDs; consult their states/reason codes for the precise limitation.
    """

    provider: str
    api_version: str
    endpoints: tuple[str, ...]
    requested_start: date
    requested_end: date
    retrieved_at_start: datetime | None
    retrieved_at_end: datetime | None
    publication_time: datetime | None
    first_observed_date: date | None
    last_observed_date: date | None
    asof_scope: str
    gates: tuple[AcceptanceGate, ...]
    blockers: tuple[str, ...]
    intended_uses: tuple[AcceptanceUseDecision, ...]
    eligible_intended_uses: tuple[str, ...]


def assess_acceptance(snapshot: MarketDataSnapshot) -> AcceptanceReport:
    """Recheck local invariants and expose externally unresolved evidence.

    This is not another normalization path or a mechanism for promoting flags.
    Even manually promoted policy/basis/coverage labels cannot accept research
    calculations: the contract has no independently verified rights, calendar,
    adjustment/accounting, or publication/revision evidence.
    """
    if not isinstance(snapshot, MarketDataSnapshot):
        raise TypeError("A MarketDataSnapshot is required.")
    p = snapshot.provenance
    identity = snapshot.identity
    gates: list[AcceptanceGate] = []

    def add(
        gate: str,
        state: Literal["pass", "unresolved", "failed"],
        *reasons: str,
        evidence: tuple[str, ...] = (),
        sources: tuple[str, ...] = (),
        observations: tuple[
            tuple[str, str | int | bool | date | datetime | None], ...
        ] = (),
    ) -> None:
        gates.append(AcceptanceGate(gate, state, reasons, evidence, sources, observations))

    # Reuse normalization checks, but only claim consistency of retained fields:
    # requested symbol and historical identity mapping are not in provenance.
    try:
        if (
            not isinstance(identity.symbol, str)
            or not _SYMBOL_RE.fullmatch(identity.symbol)
            or identity.security_type not in {"equity", "etf"}
        ):
            raise MarketDataDataError("Invalid identity.", reason="listing_unsupported")
        _normalize_identity({
            "symbol": identity.symbol, "companyName": identity.company_name,
            "exchange": identity.exchange, "country": identity.country,
            "currency": "USD", "isEtf": identity.security_type == "etf",
            "isFund": False, "isAdr": False,
            "isActivelyTrading": True, "cik": identity.cik,
            "isin": identity.isin, "cusip": identity.cusip,
        }, identity.symbol)
    except MarketDataDataError as error:
        add("identity", "failed", error.validation_reason)
    else:
        add("identity", "pass", "retained_identity_valid",
            evidence=("retained_fields_only_not_historical_identifier_mapping",),
            observations=(("symbol", identity.symbol), ("exchange", identity.exchange),
                          ("security_type", identity.security_type)))
    if (
        identity.currency == "USD" and identity.price_unit == "USD per share"
        and isinstance(identity.unit_scale, Decimal)
        and identity.unit_scale.is_finite() and identity.unit_scale == Decimal(1)
        and all(bar.volume_unit == "shares" for bar in snapshot.bars)
    ):
        add("currency_units", "pass", "usd_per_share_whole_share_units",
            observations=(("currency", identity.currency), ("price_unit", identity.price_unit),
                          ("unit_scale", str(identity.unit_scale)), ("volume_unit", "shares")))
    else:
        add("currency_units", "failed", "currency_or_units_invalid")

    try:
        if not snapshot.bars:
            raise MarketDataDataError("Empty bars.", reason="history_empty")
        if len(snapshot.bars) > MAX_PROVIDER_RECORDS:
            raise MarketDataDataError("Too many bars.", reason="record_limit")
        for bar in snapshot.bars:
            for field, value in (
                ("open", bar.open), ("high", bar.high), ("low", bar.low),
                ("close", bar.close), ("vwap", bar.vwap),
                ("adj_close", bar.adjusted_close),
            ):
                if value is None and field in {"vwap", "adj_close"}:
                    continue
                if not isinstance(value, Decimal):
                    raise MarketDataDataError("Invalid price.", reason="number_invalid")
                _positive_decimal(value, field)
            if (
                type(bar.volume) is not int
                or not 0 <= bar.volume <= 9_223_372_036_854_775_807
            ):
                raise MarketDataDataError("Invalid volume.", reason="volume_invalid")
            if (
                not bar.low <= bar.open <= bar.high
                or not bar.low <= bar.close <= bar.high
            ):
                raise MarketDataDataError("Invalid OHLC.", reason="ohlc_inconsistent")
            if bar.vwap is not None and not bar.low <= bar.vwap <= bar.high:
                raise MarketDataDataError("Invalid VWAP.", reason="vwap_range")
    except MarketDataDataError as error:
        add("finite_bars", "failed", error.validation_reason)
    else:
        add("finite_bars", "pass", "finite_bounded_consistent_bars",
            evidence=("optional_missing_vwap_or_adjusted_close_not_fabricated",),
            observations=(("bar_count", len(snapshot.bars)),
                          ("missing_vwap_count", sum(b.vwap is None for b in snapshot.bars)),
                          ("missing_adjusted_close_count",
                           sum(b.adjusted_close is None for b in snapshot.bars))))

    def utc_or_none(value: object) -> datetime | None:
        try:
            return _utc_datetime(value, "none")
        except MarketDataDataError:
            return None

    started = utc_or_none(p.retrieved_at_start)
    finished = utc_or_none(p.retrieved_at_end)
    publication = utc_or_none(p.publication_time)
    source_valid = (
        isinstance(p.provider, str) and bool(p.provider.strip())
        and isinstance(p.api_version, str) and bool(p.api_version.strip())
        and isinstance(p.endpoints, tuple) and bool(p.endpoints)
        and all(isinstance(item, str) and bool(item.strip()) for item in p.endpoints)
    )
    if not source_valid:
        add("provenance", "failed", "source_metadata_missing")
    elif started is None or finished is None:
        add("provenance", "failed", "timestamp_invalid")
    elif started > finished:
        add("provenance", "failed", "timestamp_order")
    elif p.publication_time is not None and publication is None:
        add("provenance", "failed", "publication_timestamp_invalid")
    elif publication is not None and publication > finished:
        add("provenance", "failed", "publication_after_retrieval")
    else:
        add("provenance", "pass", "source_and_utc_retrieval_recorded",
            evidence=("retrieval_is_not_publication_or_entitlement",),
            observations=(("provider", p.provider), ("api_version", p.api_version),
                          ("retrieved_at_start", started), ("retrieved_at_end", finished),
                          ("reported_publication_time", publication)))

    window_valid = (
        type(p.requested_start) is date and type(p.requested_end) is date
        and p.requested_start <= p.requested_end
        and (finished is None or p.requested_end <= finished.date())
    )
    dates_valid = all(type(bar.session_date) is date for bar in snapshot.bars)
    dates = tuple(bar.session_date for bar in snapshot.bars) if dates_valid else ()
    if not window_valid or not dates_valid:
        add("bar_dates", "failed", "date_invalid")
    elif not dates:
        add("bar_dates", "failed", "history_empty")
    elif len(set(dates)) != len(dates):
        add("bar_dates", "failed", "duplicate_bar")
    elif dates != tuple(sorted(dates)):
        add("bar_dates", "failed", "bar_date_order")
    elif any(day < p.requested_start or day > p.requested_end for day in dates):
        add("bar_dates", "failed", "date_outside_window")
    else:
        add("bar_dates", "pass", "unique_ordered_in_window_dates")
    first = min(dates) if dates else None
    last = max(dates) if dates else None

    # Revalidate retained actions (including duplicates/ambiguity and lifecycle
    # ordering). Returned-but-discarded records cannot be rechecked here.
    try:
        splits: list[RawRecord] = []
        dividends: list[RawRecord] = []
        for action in snapshot.actions:
            if (
                type(action.event_date) is not date or not window_valid
                or not p.requested_start <= action.event_date <= p.requested_end
            ):
                raise MarketDataDataError("Invalid action date.", reason="date_invalid")
            if action.currency != identity.currency:
                raise MarketDataDataError("Invalid currency.", reason="currency_unsupported")
            if action.kind == "split":
                if any(value is not None for value in (
                    action.amount, action.adjusted_amount, action.record_date,
                    action.payment_date, action.declaration_date,
                )):
                    raise MarketDataDataError("Invalid split.", reason="invalid_record")
                splits.append({
                    "symbol": identity.symbol, "date": action.event_date.isoformat(),
                    "numerator": action.numerator, "denominator": action.denominator,
                })
            elif action.kind == "dividend":
                if action.numerator is not None or action.denominator is not None:
                    raise MarketDataDataError("Invalid dividend.", reason="invalid_record")
                if (
                    not isinstance(action.amount, Decimal)
                    or (action.adjusted_amount is not None
                        and not isinstance(action.adjusted_amount, Decimal))
                ):
                    raise MarketDataDataError("Invalid amount.", reason="number_invalid")
                record: dict[str, object] = {
                    "symbol": identity.symbol, "date": action.event_date.isoformat(),
                    "dividend": action.amount, "adjDividend": action.adjusted_amount,
                }
                for field, value in (
                    ("recordDate", action.record_date),
                    ("paymentDate", action.payment_date),
                    ("declarationDate", action.declaration_date),
                ):
                    if value is not None and type(value) is not date:
                        raise MarketDataDataError("Invalid date.", reason="date_invalid")
                    record[field] = value.isoformat() if value is not None else None
                dividends.append(record)
            else:
                raise MarketDataDataError("Unknown action.", reason="invalid_record")
        _normalize_actions(tuple(splits), tuple(dividends), identity)
    except MarketDataDataError as error:
        add("action_records", "failed", error.validation_reason)
    else:
        add("action_records", "pass", "retained_actions_valid",
            evidence=("empty_or_valid_records_not_completeness_proof",),
            observations=(("retained_action_count", len(snapshot.actions)),))

    if (
        first is None or last is None or not window_valid
        or next(item.state for item in gates if item.gate == "bar_dates") == "failed"
    ):
        add("window_coverage", "failed", "window_observations_invalid")
    else:
        add("window_coverage", "unresolved", (
            "boundary_dates_without_bars"
            if first != p.requested_start or last != p.requested_end
            else "boundary_dates_observed_not_session_completeness"
        ), observations=(
            ("requested_start", p.requested_start), ("requested_end", p.requested_end),
            ("first_observed_date", first), ("last_observed_date", last),
            ("leading_calendar_days", (first - p.requested_start).days),
            ("trailing_calendar_days", (p.requested_end - last).days),
        ))
    gaps = (
        tuple((a, b) for a, b in pairwise(sorted(set(dates)))
              if _has_intervening_weekday(a, b))
        if dates and window_valid else ()
    )
    add("session_coverage", "unresolved", "exchange_calendar_unavailable",
        *(("possible_weekday_gaps",) if gaps else ()),
        evidence=("weekdays_are_not_expected_exchange_sessions",),
        observations=(
            ("possible_weekday_gap_count", len(gaps)),
            *(item for a, b in gaps for item in (("gap_start", a), ("gap_end", b))),
        ))
    if snapshot.session_evidence.freshness == "stale":
        add("freshness", "failed", "explicitly_reported_stale",
            evidence=("snapshot_metadata_declaration_not_calendar_verification",))
    else:
        add("freshness", "unresolved", "freshness_not_verifiable_at_retrieval",
            *(("publication_timestamp_unavailable",) if publication is None else
              ("reported_publication_not_verified_availability",)),
            evidence=("historical_snapshot_not_current",
                      "no_wall_clock_or_invented_stale_threshold"),
            observations=(("reported_publication_time", publication),
                          ("retrieval_lag_calendar_days",
                           (finished.date() - last).days if finished and last else None)))
    add("point_in_time", "unresolved", "publication_revision_history_unavailable",
        evidence=("retrieval_or_reported_publication_not_historical_availability",))

    # Only attach reviewed references when actually scoped to this provider.
    # Neither the URLs nor claimed basis/completeness strings prove semantics.
    basis_sources = p.price_basis_sources if p.provider == "fmp" else ()
    action_sources = p.action_coverage_sources if p.provider == "fmp" else ()
    add("price_basis", "unresolved", "whole_ohlc_volume_vwap_basis_unverified",
        evidence=("endpoint_specific_semantics_not_proven_by_adj_field_names",
                  "close_and_optional_adjusted_close_not_independently_accepted"),
        sources=basis_sources,
        observations=tuple(
            (field, ",".join(sorted({getattr(bar, field) for bar in snapshot.bars})))
            for field in (
                "ohlc_adjustment_basis", "close_adjustment_basis",
                "adjusted_close_basis", "volume_adjustment_basis", "vwap_adjustment_basis",
            )
        ))
    coverage_reasons = ["action_completeness_unverified"]
    if any(item.response_limit_reached for item in snapshot.action_evidence):
        coverage_reasons.append("action_response_limit_reached")
    if (
        any(item.events_without_bar for item in snapshot.action_evidence)
        or any(action.event_date not in dates for action in snapshot.actions)
    ):
        coverage_reasons.append("action_dates_without_bars")
    add("action_coverage", "unresolved", *coverage_reasons,
        evidence=("empty_short_or_bracketed_response_not_exhaustion_proof",
                  "snapshot_response_metadata_not_independent_coverage_verification"),
        sources=action_sources,
        observations=tuple(
            (f"{kind}_{field}", getattr(item, field))
            for kind in ("split", "dividend")
            for item in snapshot.action_evidence if item.kind == kind
            for field in (
                "returned_count", "in_window_count", "earliest_returned_date",
                "latest_returned_date", "response_limit_reached",
                "window_bracketed_by_returned_dates", "completeness",
            )
        ))
    add("action_accounting", "unresolved", "action_adjustment_cash_accounting_unaccepted")
    display_reasons = []
    if snapshot.use_policy.display_scope != "private_local_ephemeral_research_only":
        display_reasons.append("private_display_scope_required")
    if snapshot.use_policy.display_requirements != ResearchUsePolicy().display_requirements:
        display_reasons.append("historical_display_labels_required")
    add("display_boundary", "failed" if display_reasons else "pass",
        *(display_reasons or ["private_ephemeral_historical_display_boundary"]),
        evidence=("policy_boundary_only_not_verification_of_rendered_labels",))
    declared = (
        snapshot.use_policy.research_display_eligible is True
        and p.user_rights_declaration
        == "user-declared only; not legal or entitlement verification"
    )
    add("user_declarations", "pass" if declared else "unresolved",
        "private_display_user_declared" if declared else "user_declaration_not_recorded",
        evidence=("user_declaration_only_not_verified_legal_account",))
    add("legal_rights", "unresolved", "legal_account_rights_not_verified")
    add("endpoint_entitlement", "unresolved", "ongoing_endpoint_entitlement_not_verified",
        evidence=("successful_response_not_ongoing_entitlement",))
    add("calculation_rights", "unresolved", "calculation_rights_not_independently_accepted")

    states = {item.gate: item.state for item in gates}
    local = ("identity", "currency_units", "finite_bars", "provenance",
             "bar_dates", "action_records", "display_boundary")
    display_blockers = tuple(
        gate for gate in (*local, "user_declarations") if states[gate] != "pass"
    )
    if snapshot.session_evidence.freshness == "stale":
        display_blockers += ("freshness",)
    blockers = tuple(item.gate for item in gates if item.state != "pass")
    decisions = (
        AcceptanceUseDecision("private_ephemeral_display", not display_blockers,
                              display_blockers),
        *(AcceptanceUseDecision(use, False, blockers) for use in (
            "calculations", "backtest", "storage", "nautilus", "redistribution",
        )),
    )
    return AcceptanceReport(
        p.provider, p.api_version, p.endpoints, p.requested_start, p.requested_end,
        started, finished, publication, first, last,
        "snapshot_retrieval_window_only_not_current_or_historical_availability",
        tuple(gates), blockers, decisions,
        tuple(item.intended_use for item in decisions if item.eligible),
    )


def validate_config(config: MarketDataConfig) -> None:
    if not isinstance(config, MarketDataConfig):
        raise MarketDataConfigurationError("A MarketDataConfig is required.")
    if config.provider != "fmp":
        raise MarketDataConfigurationError(
            "Unsupported market-data provider; only 'fmp' is implemented."
        )
    if (
        not isinstance(config.plan_label, str)
        or not 1 <= len(config.plan_label) <= 32
        or any(not (char.isalnum() or char in " _.-") for char in config.plan_label)
    ):
        raise MarketDataConfigurationError(
            "plan_label must be a short non-secret descriptive label."
        )
    if (
        isinstance(config.max_history_days, bool)
        or not isinstance(config.max_history_days, int)
        or not 1 <= config.max_history_days <= MAX_STARTER_RANGE_DAYS
    ):
        raise MarketDataConfigurationError(
            "max_history_days must be between 1 and the documented five-year ceiling."
        )
    if (
        isinstance(config.max_calls_per_snapshot, bool)
        or not isinstance(config.max_calls_per_snapshot, int)
        or not 1 <= config.max_calls_per_snapshot <= MAX_PROVIDER_CALLS
    ):
        raise MarketDataConfigurationError(
            "max_calls_per_snapshot cannot exceed the four-request FMP snapshot."
        )
    if config.max_calls_per_snapshot < MAX_PROVIDER_CALLS:
        raise MarketDataConfigurationError(
            "A complete FMP snapshot requires a four-call budget."
        )
    if (
        isinstance(config.timeout_seconds, bool)
        or not isinstance(config.timeout_seconds, (int, float))
        or not 0 < config.timeout_seconds <= 30
    ):
        raise MarketDataConfigurationError("timeout_seconds must be in (0, 30].")
    if (
        isinstance(config.max_response_bytes, bool)
        or not isinstance(config.max_response_bytes, int)
        or not 1_024 <= config.max_response_bytes <= 8 * 1024 * 1024
    ):
        raise MarketDataConfigurationError(
            "max_response_bytes must be between 1 KiB and 8 MiB."
        )


def validate_request(
    symbol: object,
    start_date: object,
    end_date: object,
    *,
    config: MarketDataConfig,
    today: date,
) -> MarketDataRequest:
    if not isinstance(symbol, str):
        raise MarketDataInputError("Ticker must be a string.")
    normalized_symbol = symbol.strip().upper()
    if not _SYMBOL_RE.fullmatch(normalized_symbol):
        raise MarketDataInputError("Ticker must be a US-listed stock symbol.")
    if type(start_date) is not date or type(end_date) is not date:
        raise MarketDataInputError("start_date and end_date must be date values.")
    if start_date > end_date:
        raise MarketDataInputError("start_date must not be after end_date.")
    if end_date > today:
        raise MarketDataInputError(
            "Daily historical requests cannot end in the future."
        )
    if (end_date - start_date).days > config.max_history_days:
        raise MarketDataInputError(
            "Requested range exceeds the configured history limit."
        )
    return MarketDataRequest(normalized_symbol, start_date, end_date)


def require_acknowledgement(value: object) -> DataUseAcknowledgement:
    if not isinstance(value, DataUseAcknowledgement):
        raise MarketDataConfigurationError(
            "Explicit data-use acknowledgement is required before provider access."
        )
    if (
        value.personal_research_permitted is not True
        or value.requested_endpoints_entitled is not True
    ):
        raise MarketDataConfigurationError(
            "Confirm private personal-research rights and endpoint entitlement before requesting data."
        )
    return value


def normalize_snapshot(
    request: MarketDataRequest,
    payload: ProviderPayload,
    *,
    config: MarketDataConfig,
) -> MarketDataSnapshot:
    """Validate untrusted provider output into the immutable research contract."""
    if not isinstance(request, MarketDataRequest) or not isinstance(
        payload, ProviderPayload
    ):
        raise MarketDataDataError(
            "Provider returned an invalid snapshot payload.", reason="payload_type"
        )
    with validation_stage("profile"):
        if not isinstance(payload.profile, Mapping):
            raise MarketDataDataError(
                "Provider returned a malformed profile.", reason="record_shape"
            )
        identity = _normalize_identity(payload.profile, request.symbol)
    with validation_stage("history", identity_validated=True):
        _validate_records(payload.bars)
        bars = _normalize_bars(payload.bars, identity.symbol, request)
        if not bars:
            raise MarketDataNotFoundError(
                "FMP returned no daily bars for the requested period.",
                reason="history_empty",
            )
    # Validate the entire bounded action response before filtering. Bounds from
    # malformed/out-of-window events must never be treated as coverage evidence.
    all_actions = _normalize_actions(payload.splits, payload.dividends, identity)
    actions = tuple(
        action for action in all_actions
        if request.start_date <= action.event_date <= request.end_date
    )
    with validation_stage("provenance", identity_validated=True):
        started = _utc_datetime(payload.retrieved_at_start, "retrieved_at_start")
        finished = _utc_datetime(payload.retrieved_at_end, "retrieved_at_end")
        if finished < started:
            raise MarketDataDataError(
                "Provider retrieval timestamps are out of order.", reason="timestamp_order"
            )
        if finished.date() <= bars[-1].session_date:
            raise MarketDataDataError(
                "Completed daily history must precede the retrieval UTC date.",
                reason="timestamp_order", field="retrieved_at_end",
            )
        if not payload.api_version or not isinstance(payload.api_version, str):
            raise MarketDataDataError(
                "Provider API version is missing.", reason="api_version_missing",
                field="api_version",
            )
        if payload.endpoints != (
            "/stable/profile",
            "/stable/historical-price-eod/full",
            "/stable/splits",
            "/stable/dividends",
        ):
            raise MarketDataDataError(
                "Provider endpoint provenance does not match the FMP daily snapshot.",
                reason="endpoint_mismatch", field="endpoints",
            )

    action_evidence = _action_evidence(all_actions, actions, bars, request)
    session_evidence = SessionCoverageEvidence(
        first_observed_date=bars[0].session_date,
        last_observed_date=bars[-1].session_date,
        leading_calendar_days=(bars[0].session_date - request.start_date).days,
        trailing_calendar_days=(request.end_date - bars[-1].session_date).days,
        retrieval_lag_calendar_days=(finished.date() - bars[-1].session_date).days,
        possible_weekday_gaps=tuple(
            (previous.session_date, current.session_date)
            for previous, current in pairwise(bars)
            if _has_intervening_weekday(previous.session_date, current.session_date)
        ),
    )
    warnings = [
        "Research-only: equity /full close, whole OHLC, volume and VWAP adjustment bases remain unknown; endpoint-specific evidence is insufficient.",
        "Corporate-action response coverage is unverified; returned events are not proof of completeness.",
        "No exchange calendar or expected-session reconciliation was applied to the returned history.",
        "Daily EOD history is not real-time; provider publication/as-of timestamp was unavailable.",
        "Point-in-time availability and revision history are unverified; retrieval time is not historical availability.",
        "User rights and endpoint access are self-declared and were not independently verified.",
    ]
    if any(item.response_limit_reached for item in action_evidence):
        warnings.append(
            "Corporate-action response reached the limit; older events may be omitted."
        )
    if any(item.events_without_bar for item in action_evidence):
        warnings.append(
            "Returned in-window corporate actions have no matching bar date; "
            "session status is unresolved, not evidence of a holiday or adjustment."
        )
    if session_evidence.leading_calendar_days or session_evidence.trailing_calendar_days:
        warnings.append(
            "Partial boundary coverage: calendar dates at a requested edge have no bars; "
            "holidays, missing sessions and staleness cannot be distinguished."
        )
    for previous, current in pairwise(bars):
        if _has_intervening_weekday(previous.session_date, current.session_date):
            warnings.append(
                f"Possible missing trading sessions between {previous.session_date.isoformat()} "
                f"and {current.session_date.isoformat()}; weekday check only, no exchange holiday calendar."
            )
        if abs(current.close / previous.close - Decimal(1)) >= Decimal("0.30"):
            warnings.append(
                f"Unusual close-to-close move of at least 30% ending {current.session_date.isoformat()}; "
                "verify corporate actions and source data."
            )
    provenance = MarketDataProvenance(
        provider="fmp",
        api_version=payload.api_version,
        plan_label=config.plan_label,
        endpoints=payload.endpoints,
        requested_start=request.start_date,
        requested_end=request.end_date,
        retrieved_at_start=started,
        retrieved_at_end=finished,
        publication_time=None,
        timeliness="historical daily EOD; not real-time",
        cache_used=False,
        user_rights_declaration="user-declared only; not legal or entitlement verification",
        action_coverage="unverified",
    )
    return MarketDataSnapshot(
        identity=identity,
        bars=bars,
        actions=actions,
        provenance=provenance,
        warnings=tuple(warnings),
        calculation_eligible=False,
        storage_allowed=False,
        action_evidence=action_evidence,
        session_evidence=session_evidence,
        use_policy=ResearchUsePolicy(),
    )


def _action_evidence(
    all_actions: tuple[CorporateAction, ...],
    in_window: tuple[CorporateAction, ...],
    bars: tuple[DailyBar, ...],
    request: MarketDataRequest,
) -> tuple[ActionCoverageEvidence, ...]:
    bar_dates = {bar.session_date for bar in bars}
    evidence = []
    for kind in ("split", "dividend"):
        dates = [action.event_date for action in all_actions if action.kind == kind]
        retained = [action.event_date for action in in_window if action.kind == kind]
        earliest = min(dates) if dates else None
        latest = max(dates) if dates else None
        capped = len(dates) == MAX_ACTION_RECORDS
        evidence.append(ActionCoverageEvidence(
            kind=kind,
            returned_count=len(dates),
            in_window_count=len(retained),
            earliest_returned_date=earliest,
            latest_returned_date=latest,
            response_limit_reached=capped,
            window_bracketed_by_returned_dates=(
                earliest is not None and latest is not None
                and earliest <= request.start_date and latest >= request.end_date
            ),
            events_without_bar=tuple(sorted(set(retained) - bar_dates)),
            completeness="unverified",
            outstanding_reason=(
                "response_limit_reached_no_documented_paging"
                if capped else
                "empty_response_no_completeness_metadata"
                if not dates else
                "no_documented_completeness_or_exhaustion_metadata"
            ),
        ))
    return tuple(evidence)


def _has_intervening_weekday(previous: date, current: date) -> bool:
    """Flag weekday gaps conservatively; holidays may be false positives."""
    cursor = previous.toordinal() + 1
    end = current.toordinal()
    while cursor < end:
        if date.fromordinal(cursor).weekday() < 5:
            return True
        cursor += 1
    return False


def _validate_records(records: object) -> None:
    if not isinstance(records, tuple):
        raise MarketDataDataError(
            "Provider returned a malformed record collection.", reason="collection_shape"
        )
    if any(not isinstance(record, Mapping) for record in records):
        raise MarketDataDataError(
            "Provider returned a malformed record.", reason="record_shape"
        )


def _normalize_identity(profile: RawRecord, requested_symbol: str) -> SecurityIdentity:
    symbol = _required_text(profile, "symbol").upper()
    if symbol != requested_symbol:
        raise MarketDataDataError(
            "Provider security identity does not match the requested ticker.",
            reason="symbol_mismatch", field="symbol",
        )
    name = _required_text(profile, "companyName")
    if len(name) > 200:
        raise MarketDataDataError(
            "Provider company name exceeds the accepted length.",
            reason="name_length", field="company_name",
        )
    # Stable /profile reports the exchange code in `exchange`; the legacy
    # `exchangeShortName` and the descriptive `exchangeFullName` are not aliases.
    exchange = _required_text(profile, "exchange").upper()
    country = _required_text(profile, "country").upper()
    currency = _required_text(profile, "currency").upper()
    if country != "US" or exchange not in _ALLOWED_EXCHANGES:
        raise MarketDataDataError(
            "Only validated US exchange listings are accepted in this initial slice.",
            reason="listing_unsupported", field="country" if country != "US" else "exchange",
        )
    if currency != "USD":
        raise MarketDataDataError(
            "Only explicitly reported USD prices are accepted; no unit conversion is inferred.",
            reason="currency_unsupported", field="currency",
        )
    if profile.get("isActivelyTrading") is not True:
        raise MarketDataDataError(
            "Provider did not positively identify an actively trading security.",
            reason="inactive_security", field="isActivelyTrading",
        )
    is_etf = profile.get("isEtf")
    is_fund = profile.get("isFund")
    is_adr = profile.get("isAdr")
    if any(type(flag) is not bool for flag in (is_etf, is_fund, is_adr)):
        raise MarketDataDataError(
            "Provider security type metadata is missing or malformed.",
            reason="type_flags_invalid", field="type_flags",
        )
    if (is_fund and not is_etf) or is_adr:
        raise MarketDataDataError(
            "Only common equity and explicitly identified ETFs are supported.",
            reason="security_type_unsupported", field="type_flags",
        )
    security_type = "etf" if is_etf else "equity"
    cik = _optional_identifier(profile.get("cik"), field="cik", digits_only=True)
    if cik is not None:
        cik = cik.zfill(10)
    isin = _optional_identifier(profile.get("isin"), field="isin")
    cusip = _optional_identifier(profile.get("cusip"), field="cusip")
    if not any((cik, isin, cusip)):
        raise MarketDataDataError(
            "Provider profile has no stable security identifier beyond its ticker.",
            reason="identifiers_missing",
        )
    return SecurityIdentity(
        symbol=symbol,
        company_name=name,
        exchange=exchange,
        country=country,
        security_type=security_type,
        currency=currency,
        price_unit="USD per share",
        unit_scale=Decimal(1),
        cik=cik,
        isin=isin,
        cusip=cusip,
    )


def _normalize_bars(
    raw_bars: tuple[RawRecord, ...],
    symbol: str,
    request: MarketDataRequest,
) -> tuple[DailyBar, ...]:
    if len(raw_bars) > MAX_PROVIDER_RECORDS:
        raise MarketDataDataError(
            "Provider returned more rows than the documented per-request limit.",
            reason="record_limit",
        )
    bars: list[DailyBar] = []
    seen: set[date] = set()
    for item in raw_bars:
        _check_record_symbol(item, symbol)
        session_date = _parse_date(item.get("date"), required=True, field="bar date")
        assert session_date is not None
        if session_date < request.start_date or session_date > request.end_date:
            raise MarketDataDataError(
                "Provider returned a daily bar outside the requested date bounds.",
                reason="date_outside_window", field="date",
            )
        if session_date in seen:
            raise MarketDataDataError(
                "Provider returned duplicate daily bars.",
                reason="duplicate_bar", field="date",
            )
        seen.add(session_date)
        open_price = _positive_decimal(item.get("open"), "open")
        high = _positive_decimal(item.get("high"), "high")
        low = _positive_decimal(item.get("low"), "low")
        close = _positive_decimal(item.get("close"), "close")
        volume = item.get("volume")
        if (
            isinstance(volume, bool)
            or not isinstance(volume, (int, Decimal))
            or (isinstance(volume, Decimal) and (
                not volume.is_finite() or volume != volume.to_integral_value()
            ))
            or not 0 <= volume <= 9_223_372_036_854_775_807
        ):
            raise MarketDataDataError(
                "Provider volume must be a nonnegative integer.",
                reason="volume_invalid", field="volume",
            )
        volume = int(volume)  # Exact whole-share conversion only; never round.
        if low > high or not low <= open_price <= high or not low <= close <= high:
            raise MarketDataDataError(
                "Provider OHLC values are internally inconsistent.",
                reason="ohlc_inconsistent",
            )
        vwap = _optional_positive_decimal(item.get("vwap"), "vwap")
        if vwap is not None and not low <= vwap <= high:
            raise MarketDataDataError(
                "Provider VWAP lies outside the daily low/high range.",
                reason="vwap_range", field="vwap",
            )
        adjusted_close = _optional_positive_decimal(item.get("adjClose"), "adjClose")
        bars.append(
            DailyBar(
                session_date=session_date,
                open=open_price,
                high=high,
                low=low,
                close=close,
                volume=volume,
                volume_unit="shares",
                vwap=vwap,
                adjusted_close=adjusted_close,
                adjusted_close_basis=(
                    # Stable /full does not document this optional field. The
                    # same adjClose name is used by /non-split-adjusted: do not
                    # import legacy semantics from the field name or FAQ alone.
                    "unknown"
                    if adjusted_close is not None
                    else "unavailable"
                ),
                ohlc_adjustment_basis="unknown",
                close_adjustment_basis="unknown",
            )
        )
    bars.sort(key=lambda bar: bar.session_date)
    return tuple(bars)


def _normalize_actions(
    splits: tuple[RawRecord, ...],
    dividends: tuple[RawRecord, ...],
    identity: SecurityIdentity,
) -> tuple[CorporateAction, ...]:
    normalized: list[CorporateAction] = []
    seen: dict[tuple[str, str, date], CorporateAction] = {}
    for stage, records in (("splits", splits), ("dividends", dividends)):
        with validation_stage(stage, identity_validated=True):
            _validate_records(records)
            if len(records) > MAX_ACTION_RECORDS:
                raise MarketDataDataError(
                    "Provider returned more corporate actions than the request limit.",
                    reason="record_limit",
                )
    for item in splits:
        with validation_stage("splits", identity_validated=True):
            _check_record_symbol(item, identity.symbol)
            event_date = _parse_date(item.get("date"), required=True, field="split date")
            assert event_date is not None
            numerator = _positive_integer(item.get("numerator"), "split numerator")
            denominator = _positive_integer(item.get("denominator"), "split denominator")
            split_type = (
                _required_text(item, "splitType") if item.get("splitType") is not None
                else None
            )
            action = CorporateAction(
                event_date, "split", numerator, denominator, None, None,
                identity.currency, None, None, None,
                provider_split_type=split_type,
            )
            _check_action_identity(action, identity.symbol, seen)
            normalized.append(action)
    for item in dividends:
        with validation_stage("dividends", identity_validated=True):
            _check_record_symbol(item, identity.symbol)
            event_date = _parse_date(item.get("date"), required=True, field="dividend date")
            assert event_date is not None
            amount = _positive_decimal(item.get("dividend"), "dividend")
            adjusted_amount = _optional_positive_decimal(
                item.get("adjDividend"), "adjDividend"
            )
            record_date = _parse_date(
                item.get("recordDate"), required=False, field="recordDate"
            )
            payment_date = _parse_date(
                item.get("paymentDate"), required=False, field="paymentDate"
            )
            declaration_date = _parse_date(
                item.get("declarationDate"), required=False, field="declarationDate"
            )
            # Compare only reported dates with an unambiguous lifecycle order.
            # Do not order event/ex dates against record/payment: special
            # dividends can have ex dates after payment. Missing dates stay None.
            for earlier, later, field in (
                (declaration_date, record_date, "record_date"),
                (declaration_date, payment_date, "payment_date"),
                (record_date, payment_date, "payment_date"),
            ):
                if earlier is not None and later is not None and earlier > later:
                    raise MarketDataDataError(
                        "Provider dividend lifecycle dates are out of order.",
                        reason="action_date_order", field=field,
                    )
            # FMP stable dividends documents "Currency is as Trading". Inherit
            # only the validated profile currency, not an invented USD default.
            if "currency" in item and item["currency"] != identity.currency:
                raise MarketDataDataError(
                    "Provider action currency conflicts with the trading currency.",
                    reason="currency_unsupported", field="currency",
                )
            action = CorporateAction(
                event_date, "dividend", None, None, amount, adjusted_amount,
                identity.currency, record_date, payment_date, declaration_date,
            )
            _check_action_identity(action, identity.symbol, seen)
            normalized.append(action)
    return tuple(
        sorted(normalized, key=lambda action: (action.event_date, action.kind))
    )


def _check_action_identity(
    action: CorporateAction,
    symbol: str,
    seen: dict[tuple[str, str, date], CorporateAction],
) -> None:
    """Amounts/ratios/nullable metadata are attributes, not event identifiers.

    The supported schema supplies no unique event ID. Multiple same-kind events
    on one symbol/date cannot be safely distinguished, even if they might be
    genuine separate distributions. Reject rather than select or combine them.
    """
    key = (symbol, action.kind, action.event_date)
    if key in seen:
        raise MarketDataDataError(
            "Provider returned duplicate or unresolved same-date corporate actions.",
            reason="duplicate_action" if seen[key] == action else "ambiguous_action",
            field="date",
        )
    seen[key] = action


def _check_record_symbol(record: RawRecord, requested_symbol: str) -> None:
    symbol = _required_text(record, "symbol").upper()
    if symbol != requested_symbol:
        raise MarketDataDataError(
            "Provider returned a record for a different ticker.",
            reason="symbol_mismatch", field="symbol",
        )


def _required_text(record: RawRecord, field: str) -> str:
    value = record.get(field)
    if (
        not isinstance(value, str)
        or not value.strip()
        or value.strip().lower() in _MISSING_TEXT
    ):
        raise MarketDataDataError(
            f"Provider {field} is missing or malformed.", reason="text_invalid", field=field
        )
    return value.strip()


def _optional_identifier(
    value: object, *, field: str, digits_only: bool = False
) -> str | None:
    if value is None or (
        isinstance(value, str) and value.strip().lower() in _MISSING_TEXT
    ):
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise MarketDataDataError(
            "Provider security identifier is malformed.",
            reason="identifier_invalid", field=field,
        )
    text = str(value).strip()
    if (
        not text
        or len(text) > 32
        or (digits_only and (not text.isdigit() or len(text) > 10 or int(text) == 0))
    ):
        raise MarketDataDataError(
            "Provider security identifier is malformed.",
            reason="identifier_invalid", field=field,
        )
    return text


def _parse_date(value: object, *, required: bool, field: str) -> date | None:
    if value is None or (
        isinstance(value, str) and value.strip().lower() in _MISSING_TEXT
    ):
        if required:
            raise MarketDataDataError(
                f"Provider {field} is missing.", reason="date_invalid", field=field
            )
        return None
    if not isinstance(value, str):
        raise MarketDataDataError(
            f"Provider {field} is malformed.", reason="date_invalid", field=field
        )
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise MarketDataDataError(
            f"Provider {field} is malformed.", reason="date_invalid", field=field
        ) from None
    if parsed.isoformat() != value:
        raise MarketDataDataError(
            f"Provider {field} must use YYYY-MM-DD format.",
            reason="date_invalid", field=field,
        )
    return parsed


def _positive_decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise MarketDataDataError(
            f"Provider {field} is missing or malformed.",
            reason="number_invalid", field=field,
        )
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MarketDataDataError(
            f"Provider {field} is missing or malformed.",
            reason="number_invalid", field=field,
        ) from None
    if not number.is_finite() or not Decimal("0.000001") <= number <= Decimal(
        1_000_000_000
    ):
        raise MarketDataDataError(
            f"Provider {field} is non-finite or outside the accepted sanity range.",
            reason="number_range", field=field,
        )
    return number


def _optional_positive_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _positive_decimal(value, field)


def _positive_integer(value: object, field: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, Decimal))
        or (isinstance(value, Decimal) and (
            not value.is_finite() or value != value.to_integral_value()
        ))
        or not 0 < value <= 9_223_372_036_854_775_807
    ):
        raise MarketDataDataError(
            f"Provider {field} must be a positive integer.",
            reason="split_ratio_invalid", field=field,
        )
    return int(value)


def _utc_datetime(value: object, field: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise MarketDataDataError(
            f"Provider {field} must be timezone-aware.",
            reason="timestamp_invalid", field=field,
        )
    return value.astimezone(UTC)


def fetch_daily_snapshot(
    symbol: object,
    start_date: object,
    end_date: object,
    *,
    acknowledgement: DataUseAcknowledgement | None = None,
    config: MarketDataConfig | None = None,
    provider: MarketDataProvider | None = None,
    clock: Callable[[], datetime] | None = None,
) -> MarketDataSnapshot:
    """Fetch one in-memory, research-only snapshot after explicit user gates."""
    selected_config = MarketDataConfig() if config is None else config
    validate_config(selected_config)
    require_acknowledgement(acknowledgement)
    now = (clock or (lambda: datetime.now(UTC)))()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise MarketDataConfigurationError(
            "Clock must return a timezone-aware datetime."
        )
    request = validate_request(
        symbol,
        start_date,
        end_date,
        config=selected_config,
        today=now.astimezone(UTC).date(),
    )
    selected_provider = provider
    if selected_provider is None:
        # Keep the optional provider implementation lazy and side-effect free.
        from edge.data.fmp_provider import FmpProvider

        selected_provider = FmpProvider(clock=clock)
    if getattr(selected_provider, "name", None) != selected_config.provider:
        raise MarketDataConfigurationError(
            "Configured provider does not match the selected adapter."
        )
    description = selected_provider.describe()
    if (
        not isinstance(description, ProviderInfo)
        or description.name != selected_config.provider
        or description.endpoints
        != (
            "/stable/profile",
            "/stable/historical-price-eod/full",
            "/stable/splits",
            "/stable/dividends",
        )
    ):
        raise MarketDataConfigurationError(
            "Provider description does not match the supported FMP daily interface."
        )
    payload = selected_provider.fetch(request, selected_config, acknowledgement)
    if (
        not isinstance(payload, ProviderPayload)
        or payload.api_version != description.api_version
    ):
        raise MarketDataConfigurationError(
            "Provider response version does not match its declared interface."
        )
    snapshot = normalize_snapshot(request, payload, config=selected_config)
    # Only this acknowledged, strictly normalized fetch path allows labelled
    # private display. Direct normalization alone never records user permission.
    return replace(snapshot, use_policy=ResearchUsePolicy(
        research_display_eligible=True,
    ))
