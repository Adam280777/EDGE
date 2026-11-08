"""Offline, deterministic acceptance decisions; no provider/account access."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from edge.data.market_data import (
    AcceptanceReport,
    CorporateAction,
    MarketDataConfig,
    MarketDataRequest,
    MarketDataSnapshot,
    ProviderPayload,
    ResearchUsePolicy,
    assess_acceptance,
    normalize_snapshot,
)


@pytest.fixture
def snapshot() -> MarketDataSnapshot:
    """A synthetic old snapshot, not an assertion about real market prices."""
    result = normalize_snapshot(
        MarketDataRequest("AAPL", date(2024, 1, 2), date(2024, 1, 5)),
        ProviderPayload(
            profile={
                "symbol": "AAPL", "companyName": "Fixture equity",
                "exchange": "NASDAQ", "country": "US", "currency": "USD",
                "isEtf": False, "isFund": False, "isAdr": False,
                "isActivelyTrading": True, "cik": "320193",
            },
            bars=tuple({
                "symbol": "AAPL", "date": f"2024-01-0{day}",
                "open": Decimal(100), "high": Decimal(105),
                "low": Decimal(95), "close": Decimal(102),
                "volume": 1000,
            } for day in (2, 3, 4, 5)),
            splits=(), dividends=(),
            endpoints=(
                "/stable/profile", "/stable/historical-price-eod/full",
                "/stable/splits", "/stable/dividends",
            ),
            api_version="stable",
            retrieved_at_start=datetime(2024, 1, 6, 12, tzinfo=UTC),
            retrieved_at_end=datetime(2024, 1, 6, 12, 1, tzinfo=UTC),
        ),
        config=MarketDataConfig(),
    )
    return replace(result, use_policy=ResearchUsePolicy(research_display_eligible=True))


def gate(report: AcceptanceReport, name: str):
    return next(item for item in report.gates if item.gate == name)


def test_specific_local_passes_and_external_unresolved(snapshot):
    report = assess_acceptance(snapshot)
    assert isinstance(report, AcceptanceReport)
    assert tuple(item.gate for item in report.gates if item.state == "pass") == (
        "identity", "currency_units", "finite_bars", "provenance", "bar_dates",
        "action_records", "display_boundary", "user_declarations",
    )
    assert report.blockers == (
        "window_coverage", "session_coverage", "freshness", "point_in_time",
        "price_basis", "action_coverage", "action_accounting", "legal_rights",
        "endpoint_entitlement", "calculation_rights",
    )
    assert gate(report, "window_coverage").reason_codes == (
        "boundary_dates_observed_not_session_completeness",
    )
    assert gate(report, "identity").evidence == (
        "retained_fields_only_not_historical_identifier_mapping",
    )
    assert report.eligible_intended_uses == ("private_ephemeral_display",)
    for item in report.intended_uses[1:]:
        assert not item.eligible
        assert item.blockers == report.blockers
    assert snapshot.calculation_eligible is False
    assert snapshot.storage_allowed is False


def test_reproducible_frozen_report_without_clock_or_io(snapshot, monkeypatch):
    import edge.data.market_data as module

    class NoClock(datetime):
        @classmethod
        def now(cls, *args, **kwargs):
            raise AssertionError("Acceptance must not read the wall clock.")

    monkeypatch.setattr(module, "datetime", NoClock)
    # Real datetime instances must still be recognized by the type check.
    # Freeze using a subclass fixture rather than patching away their type.
    p = snapshot.provenance
    snapshot = replace(snapshot, provenance=replace(
        p,
        retrieved_at_start=NoClock.fromisoformat(p.retrieved_at_start.isoformat()),
        retrieved_at_end=NoClock.fromisoformat(p.retrieved_at_end.isoformat()),
    ))
    report = assess_acceptance(snapshot)
    assert assess_acceptance(snapshot) == report
    assert report.retrieved_at_end == p.retrieved_at_end
    assert report.asof_scope == (
        "snapshot_retrieval_window_only_not_current_or_historical_availability"
    )
    with pytest.raises(FrozenInstanceError):
        report.blockers = ()
    with pytest.raises(FrozenInstanceError):
        report.gates[0].state = "failed"
    with pytest.raises(FrozenInstanceError):
        report.intended_uses[0].eligible = False


def test_old_snapshot_not_certified_current_or_arbitrarily_stale(snapshot):
    report = assess_acceptance(snapshot)
    check = gate(report, "freshness")
    assert check.state == "unresolved"
    assert "publication_timestamp_unavailable" in check.reason_codes
    assert "historical_snapshot_not_current" in check.evidence
    assert "no_wall_clock_or_invented_stale_threshold" in check.evidence
    assert report.publication_time is None
    assert report.last_observed_date == date(2024, 1, 5)
    assert report.eligible_intended_uses == ("private_ephemeral_display",)


def test_structured_observations_are_snapshot_scoped_and_immutable(snapshot):
    report = assess_acceptance(snapshot)
    assert dict(gate(report, "finite_bars").observations) == {
        "bar_count": 4, "missing_vwap_count": 4, "missing_adjusted_close_count": 4,
    }
    assert dict(gate(report, "window_coverage").observations) == {
        "requested_start": date(2024, 1, 2), "requested_end": date(2024, 1, 5),
        "first_observed_date": date(2024, 1, 2), "last_observed_date": date(2024, 1, 5),
        "leading_calendar_days": 0, "trailing_calendar_days": 0,
    }
    assert dict(gate(report, "freshness").observations) == {
        "reported_publication_time": None, "retrieval_lag_calendar_days": 1,
    }
    assert dict(gate(report, "price_basis").observations)["close_adjustment_basis"] == "split_adjusted"
    coverage = dict(gate(report, "action_coverage").observations)
    assert coverage["split_returned_count"] == 0
    assert coverage["split_completeness"] == "unverified"
    with pytest.raises(FrozenInstanceError):
        gate(report, "finite_bars").observations = ()


def test_reported_publication_does_not_prove_historical_availability(snapshot):
    reported = snapshot.provenance.retrieved_at_start - timedelta(hours=2)
    report = assess_acceptance(replace(snapshot, provenance=replace(
        snapshot.provenance, publication_time=reported,
    )))
    assert report.publication_time == reported
    assert gate(report, "provenance").state == "pass"
    assert gate(report, "freshness").reason_codes == (
        "freshness_not_verifiable_at_retrieval",
        "reported_publication_not_verified_availability",
    )
    assert gate(report, "point_in_time").state == "unresolved"


def test_explicit_stale_fails_only_freshness_and_blocks_display(snapshot):
    report = assess_acceptance(replace(snapshot, session_evidence=replace(
        snapshot.session_evidence, freshness="stale",
    )))
    assert gate(report, "freshness").state == "failed"
    assert gate(report, "freshness").reason_codes == ("explicitly_reported_stale",)
    assert gate(report, "finite_bars").state == "pass"
    assert report.eligible_intended_uses == ()
    assert report.intended_uses[0].blockers == ("freshness",)


def test_claimed_ready_metadata_cannot_accept_calculations(snapshot):
    report = assess_acceptance(replace(
        snapshot, calculation_eligible=True, storage_allowed=True,
        use_policy=replace(
            snapshot.use_policy, calculation_eligible=True, backtest_eligible=True,
            storage_allowed=True, nautilus_eligible=True, redistribution_allowed=True,
            calculation_blockers=(),
        ),
        bars=tuple(replace(
            bar, ohlc_adjustment_basis="verified", close_adjustment_basis="verified",
            volume_adjustment_basis="verified", vwap_adjustment_basis="verified",
        ) for bar in snapshot.bars),
        session_evidence=replace(
            snapshot.session_evidence, session_coverage="complete",
            freshness="fresh", point_in_time="verified",
        ),
        provenance=replace(
            snapshot.provenance, action_coverage="complete",
            price_basis_evidence="verified",
        ),
        action_evidence=tuple(replace(
            item, completeness="complete", window_bracketed_by_returned_dates=True,
        ) for item in snapshot.action_evidence),
    ))
    for name in ("price_basis", "session_coverage", "freshness", "point_in_time",
                 "action_coverage", "action_accounting", "calculation_rights"):
        assert gate(report, name).state == "unresolved"
    assert report.eligible_intended_uses == ("private_ephemeral_display",)


@pytest.mark.parametrize(("identity_change", "failed_gate", "reason"), [
    ({"symbol": "bad value"}, "identity", "listing_unsupported"),
    ({"company_name": ""}, "identity", "text_invalid"),
    ({"cik": None, "isin": None, "cusip": None}, "identity", "identifiers_missing"),
    ({"country": "GB"}, "identity", "listing_unsupported"),
    ({"currency": "GBP"}, "currency_units", "currency_or_units_invalid"),
    ({"price_unit": "cents"}, "currency_units", "currency_or_units_invalid"),
    ({"unit_scale": Decimal("NaN")}, "currency_units", "currency_or_units_invalid"),
])
def test_identity_units_specific_failure(snapshot, identity_change, failed_gate, reason):
    report = assess_acceptance(replace(
        snapshot, identity=replace(snapshot.identity, **identity_change),
    ))
    assert gate(report, failed_gate).reason_codes == (reason,)
    assert gate(report, failed_gate).state == "failed"
    assert gate(report, "finite_bars").state == "pass"
    assert not report.eligible_intended_uses


def test_supported_etf_identity_uses_existing_contract(snapshot):
    report = assess_acceptance(replace(
        snapshot, identity=replace(snapshot.identity, security_type="etf"),
    ))
    assert gate(report, "identity").state == "pass"


@pytest.mark.parametrize(("change", "reason"), [
    ({"close": Decimal("NaN")}, "number_range"),
    ({"open": Decimal("Infinity")}, "number_range"),
    ({"low": Decimal(0)}, "number_range"),
    ({"close": 102.0}, "number_invalid"),
    ({"close": None}, "number_invalid"),
    ({"volume": True}, "volume_invalid"),
    ({"volume": Decimal("1.5")}, "volume_invalid"),
    ({"volume": -1}, "volume_invalid"),
    ({"close": Decimal(110)}, "ohlc_inconsistent"),
    ({"vwap": Decimal(110)}, "vwap_range"),
    ({"adjusted_close": Decimal("NaN")}, "number_range"),
])
def test_bar_failure_is_specific_not_generic(snapshot, change, reason):
    bars = (replace(snapshot.bars[0], **change), *snapshot.bars[1:])
    report = assess_acceptance(replace(snapshot, bars=bars))
    assert gate(report, "finite_bars").reason_codes == (reason,)
    assert gate(report, "finite_bars").state == "failed"
    assert gate(report, "identity").state == "pass"
    assert gate(report, "bar_dates").state == "pass"
    assert report.intended_uses[0].blockers == ("finite_bars",)


def test_missing_optional_values_not_missing_ohlc(snapshot):
    assert all(bar.vwap is None and bar.adjusted_close is None for bar in snapshot.bars)
    report = assess_acceptance(snapshot)
    assert gate(report, "finite_bars").state == "pass"
    assert gate(report, "price_basis").state == "unresolved"


@pytest.mark.parametrize(("mode", "reason"), [
    ("duplicate", "duplicate_bar"),
    ("reversed", "bar_date_order"),
    ("outside", "date_outside_window"),
    ("datetime", "date_invalid"),
    ("empty", "history_empty"),
])
def test_bar_date_checks(snapshot, mode, reason):
    bars = snapshot.bars
    if mode == "duplicate":
        bars = (*bars, bars[-1])
    elif mode == "reversed":
        bars = tuple(reversed(bars))
    elif mode == "outside":
        bars = (replace(bars[0], session_date=date(2024, 1, 1)), *bars[1:])
    elif mode == "datetime":
        bars = (replace(bars[0], session_date=datetime(2024, 1, 2)), *bars[1:])
    else:
        bars = ()
    report = assess_acceptance(replace(snapshot, bars=bars))
    assert gate(report, "bar_dates").reason_codes == (reason,)
    assert gate(report, "window_coverage").state == "failed"
    assert not report.eligible_intended_uses


def test_actual_gaps_and_edges_not_optimistic_stored_evidence(snapshot):
    report = assess_acceptance(replace(snapshot, bars=snapshot.bars[1::2]))
    assert gate(report, "bar_dates").state == "pass"
    assert gate(report, "window_coverage").reason_codes == ("boundary_dates_without_bars",)
    assert "possible_weekday_gaps" in gate(report, "session_coverage").reason_codes
    assert gate(report, "session_coverage").state == "unresolved"
    assert report.first_observed_date == date(2024, 1, 3)


@pytest.mark.parametrize(("change", "reason"), [
    ({"provider": ""}, "source_metadata_missing"),
    ({"api_version": ""}, "source_metadata_missing"),
    ({"endpoints": ()}, "source_metadata_missing"),
    ({"retrieved_at_start": datetime(2024, 1, 6)}, "timestamp_invalid"),
    ({"retrieved_at_end": datetime(2024, 1, 5, tzinfo=UTC)}, "timestamp_order"),
    ({"publication_time": datetime(2024, 1, 6)}, "publication_timestamp_invalid"),
    ({"publication_time": datetime(2024, 1, 7, tzinfo=UTC)}, "publication_after_retrieval"),
])
def test_source_timestamp_checks(snapshot, change, reason):
    report = assess_acceptance(replace(
        snapshot, provenance=replace(snapshot.provenance, **change),
    ))
    assert gate(report, "provenance").state == "failed"
    assert gate(report, "provenance").reason_codes == (reason,)
    assert "provenance" in report.intended_uses[0].blockers


def test_timezone_aware_provenance_is_reported_in_utc(snapshot):
    tz = timezone(timedelta(hours=-5))
    p = snapshot.provenance
    report = assess_acceptance(replace(snapshot, provenance=replace(
        p, retrieved_at_start=p.retrieved_at_start.astimezone(tz),
        retrieved_at_end=p.retrieved_at_end.astimezone(tz),
    )))
    assert report.retrieved_at_start.tzinfo is UTC
    assert report.retrieved_at_end == p.retrieved_at_end
    assert gate(report, "provenance").state == "pass"


def dividend(**changes):
    return replace(CorporateAction(
        date(2024, 1, 4), "dividend", None, None, Decimal("0.25"), None, "USD",
        date(2024, 1, 5), date(2024, 1, 12), date(2023, 12, 1),
    ), **changes)


@pytest.mark.parametrize(("actions", "reason"), [
    ((dividend(), dividend()), "duplicate_action"),
    ((dividend(), dividend(amount=Decimal("0.5"))), "ambiguous_action"),
    ((dividend(amount=Decimal("NaN")),), "number_range"),
    ((dividend(currency="EUR"),), "currency_unsupported"),
    ((dividend(record_date=date(2024, 1, 15)),), "action_date_order"),
    ((dividend(kind="mystery"),), "invalid_record"),
    ((dividend(event_date=date(2024, 1, 8)),), "date_invalid"),
])
def test_action_record_failure_not_completeness_acceptance(snapshot, actions, reason):
    report = assess_acceptance(replace(snapshot, actions=actions))
    assert gate(report, "action_records").state == "failed"
    assert gate(report, "action_records").reason_codes == (reason,)
    assert gate(report, "action_coverage").state == "unresolved"
    assert gate(report, "finite_bars").state == "pass"


def test_special_dividend_ex_after_payment_not_invented_order_error(snapshot):
    report = assess_acceptance(replace(snapshot, actions=(
        dividend(event_date=date(2024, 1, 5), record_date=date(2024, 1, 2),
                 payment_date=date(2024, 1, 3)),
    )))
    assert gate(report, "action_records").state == "pass"
    assert gate(report, "action_accounting").state == "unresolved"


def test_empty_capped_and_unmatched_action_evidence_remains_unresolved(snapshot):
    report = assess_acceptance(replace(
        snapshot, actions=(dividend(),),
        bars=tuple(bar for bar in snapshot.bars if bar.session_date != date(2024, 1, 4)),
        action_evidence=tuple(replace(
            item, response_limit_reached=True, window_bracketed_by_returned_dates=True,
            completeness="complete",
        ) for item in snapshot.action_evidence),
    ))
    assert gate(report, "action_coverage").reason_codes == (
        "action_completeness_unverified", "action_response_limit_reached",
        "action_dates_without_bars",
    )


def test_direct_normalization_or_missing_declaration_cannot_display(snapshot):
    for changed in (
        replace(snapshot, use_policy=ResearchUsePolicy()),
        replace(snapshot, provenance=replace(
            snapshot.provenance, user_rights_declaration="HTTP 200",
        )),
    ):
        report = assess_acceptance(changed)
        assert gate(report, "user_declarations").state == "unresolved"
        assert not report.eligible_intended_uses


def test_legal_and_endpoint_acknowledgement_is_only_a_user_declaration(snapshot):
    report = assess_acceptance(snapshot)
    assert gate(report, "user_declarations").reason_codes == ("private_display_user_declared",)
    assert "user_declaration_only_not_verified_legal_account" in gate(
        report, "user_declarations",
    ).evidence
    assert gate(report, "legal_rights").state == "unresolved"
    assert gate(report, "endpoint_entitlement").evidence == (
        "successful_response_not_ongoing_entitlement",
    )


def test_foreign_provider_not_rejected_or_given_fmp_semantics(snapshot):
    report = assess_acceptance(replace(snapshot, provenance=replace(
        snapshot.provenance, provider="unknown_adapter",
        api_version="v9", endpoints=("/equity/bars",),
    )))
    assert report.provider == "unknown_adapter"
    assert report.endpoints == ("/equity/bars",)
    assert gate(report, "provenance").state == "pass"
    assert gate(report, "price_basis").state == "unresolved"
    assert gate(report, "price_basis").sources == ()
    assert gate(report, "action_coverage").sources == ()
    assert report.eligible_intended_uses == ("private_ephemeral_display",)


def test_fmp_references_are_reviewed_not_static_acceptance(snapshot):
    report = assess_acceptance(snapshot)
    assert gate(report, "price_basis").sources == snapshot.provenance.price_basis_sources
    assert gate(report, "price_basis").state == "unresolved"
    assert gate(report, "action_coverage").sources == snapshot.provenance.action_coverage_sources


@pytest.mark.parametrize("change", [
    {"display_scope": "public"},
    {"display_requirements": ()},
])
def test_private_display_labels_and_scope_required(snapshot, change):
    report = assess_acceptance(replace(
        snapshot, use_policy=replace(snapshot.use_policy, **change),
    ))
    assert report.eligible_intended_uses == ()
    assert gate(report, "display_boundary").state == "failed"
    assert report.intended_uses[0].blockers == ("display_boundary",)


def test_requires_snapshot_contract():
    with pytest.raises(TypeError, match="MarketDataSnapshot"):
        assess_acceptance(None)
