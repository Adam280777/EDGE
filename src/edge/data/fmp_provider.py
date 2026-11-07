"""Narrow FMP stable REST adapter for daily U.S. equity research snapshots.

The adapter uses only documented stable endpoints, stdlib HTTP/JSON, no cache,
and no automatic retry. FMP credentials are read lazily from
``EDGE_FMP_API_KEY`` and sent only in the documented ``apikey`` header.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from edge.data.market_data import (
    MAX_ACTION_RECORDS,
    DataUseAcknowledgement,
    MarketDataAccessError,
    MarketDataConfig,
    MarketDataConfigurationError,
    MarketDataDataError,
    MarketDataNotFoundError,
    MarketDataProviderError,
    MarketDataRateLimitError,
    MarketDataRequest,
    ProviderInfo,
    ProviderPayload,
    normalize_snapshot,
    require_acknowledgement,
    validate_config,
    validate_request,
    validation_stage,
)

API_KEY_ENV_VAR = "EDGE_FMP_API_KEY"
BASE_URL = "https://financialmodelingprep.com"
ENDPOINTS = (
    "/stable/profile",
    "/stable/historical-price-eod/full",
    "/stable/splits",
    "/stable/dividends",
)
API_VERSION = "FMP stable REST API"
_API_KEY_RE = re.compile(r"^[A-Za-z0-9._~-]{1,512}$")
_MISSING_TEXT = frozenset({"", "none", "null", "nan", "nat"})


class FmpMissingApiKeyError(MarketDataConfigurationError):
    """The runtime credential is absent or empty; no request was attempted."""


class FmpInvalidApiKeyError(MarketDataConfigurationError):
    """The runtime credential fails local header-safe format validation."""


class FmpCredentialInPlanLabelError(MarketDataConfigurationError):
    """The descriptive plan label would disclose the runtime credential."""


@dataclass(frozen=True)
class TransportResponse:
    status: int
    body: bytes


class HttpTransport(Protocol):
    """Injectable no-cache transport. Implementations must not follow redirects."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> TransportResponse: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every redirect so credentials cannot be forwarded to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibTransport:
    """Bounded HTTPS GET transport with redirects disabled and no persistence."""

    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                status = int(response.status)
                body = response.read(max_response_bytes + 1)
        except urllib.error.HTTPError as error:
            # Do not expose provider body/error text; it may contain reflected inputs.
            if 300 <= error.code < 400:
                return TransportResponse(error.code, b"")
            return TransportResponse(error.code, b"")
        except (urllib.error.URLError, TimeoutError, OSError):
            raise MarketDataProviderError(
                "FMP request failed; no response details were retained."
            ) from None
        if len(body) > max_response_bytes:
            raise MarketDataProviderError(
                "FMP response exceeded the configured size limit."
            )
        return TransportResponse(status, body)


class FmpProvider:
    """FMP provider implementation for a complete four-endpoint daily snapshot."""

    name = "fmp"

    def __init__(
        self,
        *,
        transport: HttpTransport | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._transport = transport if transport is not None else UrllibTransport()
        self._environ = environ
        self._clock = clock if clock is not None else lambda: datetime.now(UTC)

    def describe(self) -> ProviderInfo:
        return ProviderInfo(self.name, API_VERSION, ENDPOINTS)

    def fetch(
        self,
        request: MarketDataRequest,
        config: MarketDataConfig,
        acknowledgement: DataUseAcknowledgement,
    ) -> ProviderPayload:
        """Fetch profile, EOD bars, splits, and dividends; never cache or retry."""
        validate_config(config)
        require_acknowledgement(acknowledgement)
        today = self._now().date()
        checked = validate_request(
            request.symbol,
            request.start_date,
            request.end_date,
            config=config,
            today=today,
        )
        if checked != request:
            raise MarketDataConfigurationError(
                "Provider request was not normalized by the market-data contract."
            )
        api_key = self._api_key(config)
        retrieved_at_start = self._now()

        profile_rows = self._get_json_rows(
            ENDPOINTS[0], {"symbol": request.symbol}, api_key, config
        )
        with validation_stage("profile"):
            if len(profile_rows) != 1:
                raise MarketDataNotFoundError(
                    "FMP did not return exactly one security profile.",
                    reason="profile_count",
                )
        bars = self._get_json_rows(
            ENDPOINTS[1],
            {
                "symbol": request.symbol,
                "from": request.start_date.isoformat(),
                "to": request.end_date.isoformat(),
            },
            api_key,
            config,
        )
        with validation_stage("history"):
            if len(bars) > 5_000:
                raise MarketDataDataError(
                    "FMP exceeded the documented daily-history response limit.",
                    reason="record_limit",
                )
        splits = self._get_json_rows(
            ENDPOINTS[2],
            {"symbol": request.symbol, "limit": str(MAX_ACTION_RECORDS)},
            api_key,
            config,
        )
        dividends = self._get_json_rows(
            ENDPOINTS[3],
            {"symbol": request.symbol, "limit": str(MAX_ACTION_RECORDS)},
            api_key,
            config,
        )
        for stage, records in (("splits", splits), ("dividends", dividends)):
            with validation_stage(stage):
                if len(records) > MAX_ACTION_RECORDS:
                    raise MarketDataDataError(
                        "FMP exceeded the documented corporate-action response limit.",
                        reason="record_limit",
                    )
        retrieved_at_end = self._now()
        payload = ProviderPayload(
            profile=profile_rows[0],
            bars=tuple(bars),
            splits=tuple(splits),
            dividends=tuple(dividends),
            endpoints=ENDPOINTS,
            api_version=API_VERSION,
            retrieved_at_start=retrieved_at_start,
            retrieved_at_end=retrieved_at_end,
        )
        snapshot = normalize_snapshot(request, payload, config=config)
        if api_key in repr(snapshot):
            raise MarketDataConfigurationError(
                "FMP output was rejected to prevent credential disclosure."
            )
        return payload

    def _api_key(self, config: MarketDataConfig) -> str:
        environment = os.environ if self._environ is None else self._environ
        value = environment.get(API_KEY_ENV_VAR)
        if value is None or value == "":
            raise FmpMissingApiKeyError(
                f"Set {API_KEY_ENV_VAR} before requesting FMP data."
            )
        if not isinstance(value, str) or not _API_KEY_RE.fullmatch(value):
            raise FmpInvalidApiKeyError(
                f"Set a valid {API_KEY_ENV_VAR} before requesting FMP data."
            )
        if value in config.plan_label:
            raise FmpCredentialInPlanLabelError(
                "The descriptive plan label must not contain the API credential."
            )
        return value

    def _now(self) -> datetime:
        value = self._clock()
        if (
            not isinstance(value, datetime)
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise MarketDataConfigurationError(
                "Clock must return a timezone-aware datetime."
            )
        return value.astimezone(UTC)

    def _get_json_rows(
        self,
        endpoint: str,
        params: Mapping[str, str],
        api_key: str,
        config: MarketDataConfig,
    ) -> list[Mapping[str, object]]:
        # The API key is intentionally absent from the URL/query string.
        query = urllib.parse.urlencode(params)
        url = f"{BASE_URL}{endpoint}?{query}"
        try:
            response = self._transport.get(
                url,
                headers={"Accept": "application/json", "apikey": api_key},
                timeout_seconds=float(config.timeout_seconds),
                max_response_bytes=config.max_response_bytes,
            )
        except Exception:  # noqa: BLE001 - custom transports may embed credentials in exception text
            # Transport exceptions can include request objects or headers.
            raise MarketDataProviderError(
                "FMP transport failed; details were redacted."
            ) from None
        if not isinstance(response, TransportResponse):
            raise MarketDataProviderError("FMP transport returned an invalid response.")
        if response.status in (401, 403):
            raise MarketDataAccessError(
                "FMP rejected credentials or endpoint entitlement; verify the account and plan."
            )
        if response.status == 429:
            raise MarketDataRateLimitError(
                "FMP rate or quota limit reached; no retry was attempted."
            )
        if 300 <= response.status < 400:
            raise MarketDataProviderError(
                "FMP redirected the request; redirects are refused."
            )
        if response.status == 404:
            raise MarketDataNotFoundError(
                "FMP endpoint or requested security was not found."
            )
        if response.status >= 500:
            raise MarketDataProviderError("FMP service is unavailable.")
        if response.status != 200:
            raise MarketDataProviderError(
                "FMP returned an unsuccessful status; response details were redacted."
            )
        if (
            not isinstance(response.body, bytes)
            or len(response.body) > config.max_response_bytes
        ):
            raise MarketDataProviderError(
                "FMP response exceeded the configured size limit."
            )
        stage = dict(zip(ENDPOINTS, ("profile", "history", "splits", "dividends")))[endpoint]
        with validation_stage(stage):
            return _decode_rows(response.body)


def _decode_rows(body: bytes) -> list[Mapping[str, object]]:
    try:
        text = body.decode("utf-8")
        value = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise MarketDataDataError(
            "FMP returned malformed JSON; response contents were discarded.",
            reason="json_invalid",
        ) from None
    if not isinstance(value, list):
        # FMP error envelopes are never echoed; avoid interpreting them as data.
        raise MarketDataDataError(
            "FMP returned an unexpected response shape.", reason="response_shape"
        )
    if any(not isinstance(item, dict) for item in value):
        raise MarketDataDataError(
            "FMP returned a non-object record.", reason="record_shape"
        )
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant {value} is not accepted.")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key.")
        result[key] = value
    return result
