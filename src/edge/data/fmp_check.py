"""Explicit, bounded FMP connectivity check; never emits or persists records."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from edge.data.fmp_provider import (
    BASE_URL,
    ENDPOINTS,
    FmpCredentialInPlanLabelError,
    FmpInvalidApiKeyError,
    FmpMissingApiKeyError,
    FmpProvider,
    HttpTransport,
    TransportResponse,
    UrllibTransport,
)
from edge.data.market_data import (
    VALIDATION_FIELDS,
    VALIDATION_REASONS,
    VALIDATION_STAGES,
    DataUseAcknowledgement,
    MarketDataAccessError,
    MarketDataConfig,
    MarketDataConfigurationError,
    MarketDataDataError,
    MarketDataInputError,
    MarketDataNotFoundError,
    MarketDataProviderError,
    MarketDataRateLimitError,
    fetch_daily_snapshot,
)


class _InvalidArgumentsError(MarketDataConfigurationError):
    """Arguments failed parsing; never retain argparse's reflected input."""


class _MissingConfirmationsError(MarketDataConfigurationError):
    """Both explicit user declarations are required before provider access."""


class _SafeParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # argparse's default error can echo arbitrary command-line secrets.
        raise _InvalidArgumentsError("Invalid check arguments.")


class _BoundedTransport:
    """Enforce four ordered attempts, regardless of plan or provider behavior."""

    def __init__(self, transport: HttpTransport) -> None:
        self._transport = transport
        self.attempts = 0
        self.succeeded: list[str] = []

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        target = urlsplit(url)
        if (
            self.attempts >= 4
            or target.scheme != "https"
            or target.netloc != urlsplit(BASE_URL).netloc
            or target.path != ENDPOINTS[self.attempts]
            or target.fragment
        ):
            raise MarketDataProviderError("Check request boundary rejected.")
        endpoint = ENDPOINTS[self.attempts]
        self.attempts += 1
        response = self._transport.get(
            url,
            headers=headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        if isinstance(response, TransportResponse) and response.status == 200:
            self.succeeded.append(endpoint)
        return response


_FAILURES = (
    (MarketDataConfigurationError, "configuration_error", 2),
    (MarketDataInputError, "input_error", 2),
    (MarketDataAccessError, "access_rejected", 3),
    (MarketDataRateLimitError, "rate_limit", 4),
    (MarketDataNotFoundError, "not_found", 5),
    (MarketDataDataError, "data_validation_failed", 6),
    (MarketDataProviderError, "provider_error", 7),
)

# Only literal codes selected by exception type may leave the configuration gate.
# Never classify by exception text or print attributes supplied by a caller.
_CONFIGURATION_REASONS = (
    (_InvalidArgumentsError, "invalid_arguments", "use_documented_flags"),
    (
        _MissingConfirmationsError,
        "missing_confirmations",
        "confirm_rights_and_access_if_permitted",
    ),
    (FmpMissingApiKeyError, "missing_key", "enter_key_at_hidden_prompt"),
    (FmpInvalidApiKeyError, "invalid_key_format", "reenter_key_only_at_hidden_prompt"),
    (
        FmpCredentialInPlanLabelError,
        "credential_in_plan_label",
        "use_nonsecret_plan_label",
    ),
)

_VALIDATION_RECOVERY = {
    "snapshot": "review_adapter_contract_offline_no_retry",
    "profile": "review_profile_schema_and_identity_offline_no_retry",
    "history": "review_history_schema_and_window_offline_no_retry",
    "splits": "review_split_schema_and_coverage_offline_no_retry",
    "dividends": "review_dividend_schema_and_coverage_offline_no_retry",
    "provenance": "review_adapter_provenance_offline_no_retry",
}


def _validation_diagnostic(error: Exception) -> tuple[str, str, str, str]:
    """Select closed literal codes only; never stringify arbitrary attributes."""
    stage = getattr(error, "validation_stage", None)
    reason = getattr(error, "validation_reason", None)
    field = getattr(error, "validation_field", None)
    if not (
        type(stage) is str and stage in VALIDATION_STAGES
        and type(reason) is str and reason in VALIDATION_REASONS
        and type(field) is str and field in VALIDATION_FIELDS
    ):
        return "snapshot", "invalid_record", "none", "not_established"
    identity = (
        "passed"
        if getattr(error, "identity_validated", None) is True
        and stage in {"history", "splits", "dividends", "provenance"}
        else "not_established"
    )
    return stage, reason, field, identity


def main(argv: Sequence[str] | None = None) -> int:
    """Run once, with sanitized typed failures and no exception/record output."""
    transport = _BoundedTransport(UrllibTransport())
    display_eligible = False
    try:
        parser = _SafeParser(
            description=(
                "One MSFT private-research check, last 14 completed UTC calendar "
                "days; at most four requests, no retries or saved records. "
                "Both confirmations are declarations, not verified entitlement."
            ),
            allow_abbrev=False,
        )
        parser.add_argument("--symbol", choices=("MSFT",), default="MSFT")
        parser.add_argument("--confirm-private-research", action="store_true")
        parser.add_argument("--confirm-endpoint-access", action="store_true")
        args = parser.parse_args(argv)
        if not (args.confirm_private_research and args.confirm_endpoint_access):
            raise _MissingConfirmationsError("Both declarations are required.")

        now = datetime.now(UTC)
        today = now.date()
        start = today - timedelta(days=14)
        end = today - timedelta(days=1)
        config = MarketDataConfig(
            plan_label="private-check",
            max_history_days=14,
            max_calls_per_snapshot=4,
            timeout_seconds=10,
            max_response_bytes=4 * 1024 * 1024,
        )
        snapshot = fetch_daily_snapshot(
            args.symbol,
            start,
            end,
            config=config,
            acknowledgement=DataUseAcknowledgement(
                personal_research_permitted=True,
                requested_endpoints_entitled=True,
            ),
            provider=FmpProvider(transport=transport),
            clock=lambda: now,
        )
        print("status=validated_research_snapshot; identity_validation=passed")
        print(f"requested_window_utc={start.isoformat()}..{end.isoformat()}")
        provenance = snapshot.provenance
        print(
            f"retrieved_utc={provenance.retrieved_at_start.isoformat()}.."
            f"{provenance.retrieved_at_end.isoformat()}"
        )
        print(
            "source=fmp; api=stable; timeliness=historical_daily_eod_not_realtime; "
            "publication_time=unavailable; cache_used=false"
        )
        print(
            f"rows={len(snapshot.bars)}; actions={len(snapshot.actions)}; "
            f"splits={sum(a.kind == 'split' for a in snapshot.actions)}; "
            f"dividends={sum(a.kind == 'dividend' for a in snapshot.actions)}"
        )
        first = snapshot.bars[0].session_date
        last = snapshot.bars[-1].session_date
        print(f"observed_dates={first.isoformat()}..{last.isoformat()}")
        print(
            "close_basis=unknown; whole_ohlc_basis=unknown; "
            "volume_and_vwap_basis=unknown; "
            "basis_evidence=insufficient_endpoint_specific_adjustment_evidence"
        )
        print(
            "warnings=whole_bar_basis_unverified,action_coverage_unverified,"
            "no_exchange_calendar,publication_time_unknown,"
            "point_in_time_unverified,rights_and_access_self_declared"
        )
        print(
            "session_coverage=unverified; freshness=unknown; "
            "publication_and_revision_history=unavailable; "
            "retrieval_time_is_not_historical_availability"
        )
        # Fixed report vocabulary only; never emit arbitrary warning/record text.
        for kind in ("split", "dividend"):
            evidence = next(item for item in snapshot.action_evidence if item.kind == kind)
            print(
                f"action_kind={kind}; returned_rows={evidence.returned_count}; "
                f"in_window_rows={evidence.in_window_count}; "
                f"events_without_bar={len(evidence.events_without_bar)}; "
                "completeness=unverified"
            )
        # Only known categories escape; never echo provider-derived warning text.
        if any(
            w.startswith("Possible missing trading sessions") for w in snapshot.warnings
        ):
            print("warning=possible_session_gaps_weekday_check_only")
        if any(w.startswith("Unusual close-to-close move") for w in snapshot.warnings):
            print("warning=unusual_price_move_verify_source_and_actions")
        if any(w.startswith("Corporate-action response reached") for w in snapshot.warnings):
            print("warning=action_response_limit_coverage_unverified")
        if first > start or last < end:
            print(
                "warning=partial_boundary_coverage_holidays_or_staleness_not_resolved"
            )
        display_eligible = snapshot.use_policy.research_display_eligible is True
        print(
            "display_scope=private_local_ephemeral_research_only_user_declared; "
            "display_requires=source_retrieval_window_and_all_limitations; "
            "not_current_or_point_in_time_verified"
        )
        status = 0
    except KeyboardInterrupt:
        print("status=interrupted; identity_validation=not_established")
        status = 130
    except Exception as error:  # noqa: BLE001 - never echo even unexpected failures
        label, status = "internal_error", 8
        for error_type, safe_label, code in _FAILURES:
            if isinstance(error, error_type):
                label, status = safe_label, code
                break
        diagnostic = None
        identity = "not_established"
        if isinstance(error, (MarketDataDataError, MarketDataNotFoundError)):
            diagnostic = _validation_diagnostic(error)
            identity = diagnostic[3]
        print(f"status={label}; identity_validation={identity}")
        if diagnostic is not None:
            stage, reason, field, _ = diagnostic
            print(
                f"validation_stage={stage}; validation_reason={reason}; "
                f"validation_field={field}; recovery={_VALIDATION_RECOVERY[stage]}"
            )
        if isinstance(error, MarketDataConfigurationError):
            reason, recovery = "invalid_configuration", "review_local_configuration"
            for error_type, safe_reason, safe_recovery in _CONFIGURATION_REASONS:
                if isinstance(error, error_type):
                    reason, recovery = safe_reason, safe_recovery
                    break
            print(f"configuration_reason={reason}; recovery={recovery}")
        print("warning=check_incomplete_no_acceptance")
    print(f"requests_attempted={transport.attempts}; request_limit=4; retries=0")
    print(
        "endpoints_http_200="
        + (",".join(transport.succeeded) if transport.succeeded else "none")
        + "; http_success_is_not_data_or_entitlement_acceptance"
    )
    print(
        "storage_allowed=false; calculation_eligible=false; "
        "backtest_eligible=false; nautilus_eligible=false; "
        "redistribution_allowed=false; entitlement_verified=false"
    )
    print("research_display_eligible=" + ("true" if display_eligible else "false"))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
