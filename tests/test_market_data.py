"""Offline contract and FMP adapter tests; never call the real provider."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import urllib.request
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from edge.data.fmp_provider import (
    API_KEY_ENV_VAR,
    API_VERSION,
    ENDPOINTS,
    FmpProvider,
    TransportResponse,
    UrllibTransport,
    _NoRedirectHandler,
)
from edge.data.market_data import (
    CorporateAction,
    DataUseAcknowledgement,
    MarketDataAccessError,
    MarketDataConfig,
    MarketDataConfigurationError,
    MarketDataDataError,
    MarketDataInputError,
    MarketDataNotFoundError,
    MarketDataProviderError,
    MarketDataRateLimitError,
    MarketDataRequest,
    MarketDataSnapshot,
    ProviderPayload,
    fetch_daily_snapshot,
    normalize_snapshot,
)

API_KEY = "fixture-secret-do-not-print"
ACK = DataUseAcknowledgement(
    personal_research_permitted=True,
    requested_endpoints_entitled=True,
)
NOW = datetime(2024, 1, 5, 17, 0, tzinfo=UTC)
START = date(2024, 1, 2)
END = date(2024, 1, 4)


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("A market-data test attempted network access.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)


def test_contract_import_is_lazy_and_offline() -> None:
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop(API_KEY_ENV_VAR, None)
    environment["PYTHONPATH"] = str(root / "src")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    script = (
        "import socket, sys\n"
        "def blocked(*args, **kwargs): raise AssertionError('network attempted')\n"
        "socket.socket.connect = blocked\n"
        "socket.create_connection = blocked\n"
        "import edge.data.market_data\n"
        "assert 'edge.data.fmp_provider' not in sys.modules\n"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), allow_nan=False).encode("utf-8")


def profile(**overrides: object) -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "companyName": "Apple Inc.",
        "exchange": "NASDAQ",
        "exchangeFullName": "NASDAQ Global Select",
        "country": "US",
        "currency": "USD",
        "isEtf": False,
        "isFund": False,
        "isAdr": False,
        "isActivelyTrading": True,
        "cik": "0000320193",
        "isin": "US0378331005",
        "cusip": "037833100",
        **overrides,
    }


def bar(day: str, **overrides: object) -> dict[str, object]:
    return {
        "symbol": "AAPL",
        "date": day,
        "open": 100.25,
        "high": 105.5,
        "low": 99.0,
        "close": 104.0,
        "volume": 123456,
        "vwap": 102.75,
        "adjClose": 103.5,
        **overrides,
    }


SPLITS = [
    {"symbol": "AAPL", "date": "2023-08-01", "numerator": 4, "denominator": 1},
    {"symbol": "AAPL", "date": "2024-01-03", "numerator": 2, "denominator": 1},
]
DIVIDENDS = [
    {
        "symbol": "AAPL",
        "date": "2024-01-04",
        "dividend": 0.25,
        "adjDividend": 0.25,
        "recordDate": "2024-01-05",
        "paymentDate": "2024-01-12",
        "declarationDate": "2023-12-01",
    }
]


class FixtureTransport:
    def __init__(
        self,
        *,
        status: int = 200,
        malformed: bytes | None = None,
        response_override: dict[str, object] | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.status = status
        self.malformed = malformed
        self.response_override = response_override
        self.exception = exception
        self.calls: list[tuple[str, dict[str, str], float, int]] = []

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> TransportResponse:
        self.calls.append((url, dict(headers), timeout_seconds, max_response_bytes))
        if self.exception is not None:
            raise self.exception
        if self.malformed is not None:
            body = self.malformed
        elif self.response_override is not None:
            body = json_bytes(self.response_override[urlparse(url).path])
        else:
            payload: dict[str, object] = {
                ENDPOINTS[0]: [profile()],
                ENDPOINTS[1]: [bar("2024-01-04"), bar("2024-01-02")],
                ENDPOINTS[2]: SPLITS,
                ENDPOINTS[3]: DIVIDENDS,
            }
            body = json_bytes(payload[urlparse(url).path])
        if self.status != 200:
            body = f"provider reflected {API_KEY}".encode()
        return TransportResponse(self.status, body)


def provider(transport: FixtureTransport | None = None, **kwargs: Any) -> FmpProvider:
    return FmpProvider(
        transport=transport or FixtureTransport(),
        environ={API_KEY_ENV_VAR: API_KEY},
        clock=lambda: NOW,
        **kwargs,
    )


def fetch(
    selected_provider: FmpProvider | None = None,
    *,
    config: MarketDataConfig | None = None,
    acknowledgement: DataUseAcknowledgement | None = ACK,
    symbol: object = "aapl",
    start: object = START,
    end: object = END,
) -> MarketDataSnapshot:
    return fetch_daily_snapshot(
        symbol,
        start,
        end,
        acknowledgement=acknowledgement,
        config=config,
        provider=selected_provider,
        clock=lambda: NOW,
    )


def test_fmp_fetch_returns_research_only_snapshot_and_provenance(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    transport = FixtureTransport()
    result = fetch(provider(transport))

    assert result.identity.symbol == "AAPL"
    assert result.identity.company_name == "Apple Inc."
    assert result.identity.exchange == "NASDAQ"
    assert result.identity.currency == "USD"
    assert result.identity.price_unit == "USD per share"
    assert result.identity.cik == "0000320193"
    assert result.bars[0].volume_unit == "shares"
    assert [(item.session_date, item.close) for item in result.bars] == [
        (date(2024, 1, 2), Decimal("104.0")),
        (date(2024, 1, 4), Decimal("104.0")),
    ]
    assert result.bars[0].ohlc_adjustment_basis == "unknown"
    assert result.bars[0].adjusted_close == Decimal("103.5")
    assert result.bars[0].adjusted_close_basis == "unknown"
    assert result.bars[0].close_adjustment_basis == "unknown"
    assert result.actions == (
        CorporateAction(
            date(2024, 1, 3), "split", 2, 1, None, None, "USD", None, None, None
        ),
        CorporateAction(
            date(2024, 1, 4),
            "dividend",
            None,
            None,
            Decimal("0.25"),
            Decimal("0.25"),
            "USD",
            date(2024, 1, 5),
            date(2024, 1, 12),
            date(2023, 12, 1),
        ),
    )
    assert result.calculation_eligible is False
    assert result.storage_allowed is False
    assert result.provenance.provider == "fmp"
    assert result.provenance.api_version == API_VERSION
    assert result.provenance.endpoints == ENDPOINTS
    assert result.provenance.requested_start == START
    assert result.provenance.requested_end == END
    assert result.provenance.retrieved_at_start == NOW
    assert result.provenance.retrieved_at_end == NOW
    assert result.provenance.publication_time is None
    assert result.provenance.timeliness == "historical daily EOD; not real-time"
    assert result.provenance.cache_used is False
    assert result.provenance.action_coverage == "unverified"
    assert "not legal" in result.provenance.user_rights_declaration
    assert len(transport.calls) == 4
    assert not list(tmp_path.iterdir())
    assert API_KEY not in repr(result)


def test_api_key_only_in_documented_header_and_urls_are_bounded() -> None:
    transport = FixtureTransport()
    fetch(provider(transport))
    for index, (url, headers, timeout, response_limit) in enumerate(transport.calls):
        parsed = urlparse(url)
        assert parsed.scheme == "https"
        assert parsed.netloc == "financialmodelingprep.com"
        assert parsed.path == ENDPOINTS[index]
        assert API_KEY not in url
        assert headers["apikey"] == API_KEY
        assert headers["Accept"] == "application/json"
        assert timeout == 10.0
        assert response_limit == 4 * 1024 * 1024
    assert parse_qs(urlparse(transport.calls[1][0]).query) == {
        "symbol": ["AAPL"],
        "from": ["2024-01-02"],
        "to": ["2024-01-04"],
    }
    assert parse_qs(urlparse(transport.calls[2][0]).query) == {
        "symbol": ["AAPL"],
        "limit": ["1000"],
    }


@pytest.mark.parametrize(
    "acknowledgement",
    [
        None,
        DataUseAcknowledgement(),
        DataUseAcknowledgement(personal_research_permitted=True),
        DataUseAcknowledgement(requested_endpoints_entitled=True),
        DataUseAcknowledgement(
            personal_research_permitted=1, requested_endpoints_entitled=True
        ),  # type: ignore[arg-type]
    ],
)
def test_acknowledgement_denial_prevents_provider_access(acknowledgement) -> None:
    transport = FixtureTransport()
    selected = provider(transport)
    with pytest.raises(MarketDataConfigurationError):
        fetch(selected, acknowledgement=acknowledgement)
    assert transport.calls == []


def test_starter_label_does_not_grant_missing_account_entitlement() -> None:
    transport = FixtureTransport()
    selected = provider(transport)
    with pytest.raises(MarketDataConfigurationError):
        fetch(selected, acknowledgement=DataUseAcknowledgement())
    assert selected.describe().name == "fmp"
    assert transport.calls == []


def test_missing_or_malformed_key_fails_before_network() -> None:
    for environment in ({}, {API_KEY_ENV_VAR: "bad\r\napikey:other"}):
        transport = FixtureTransport()
        selected = FmpProvider(
            transport=transport, environ=environment, clock=lambda: NOW
        )
        with pytest.raises(MarketDataConfigurationError) as error:
            fetch(selected)
        assert API_KEY_ENV_VAR in str(error.value)
        assert transport.calls == []


def test_plan_label_must_not_embed_the_credential() -> None:
    transport = FixtureTransport()
    selected = provider(transport)
    with pytest.raises(MarketDataConfigurationError, match="plan label"):
        fetch(selected, config=MarketDataConfig(plan_label=f"plan {API_KEY}"))
    assert transport.calls == []


def test_short_api_key_is_rejected_if_plan_label_would_expose_it() -> None:
    short_key = "abc"
    transport = FixtureTransport()
    selected = FmpProvider(
        transport=transport,
        environ={API_KEY_ENV_VAR: short_key},
        clock=lambda: NOW,
    )
    with pytest.raises(MarketDataConfigurationError) as error:
        fetch(
            selected,
            config=MarketDataConfig(plan_label=short_key),
        )
    assert short_key not in str(error.value)
    assert short_key not in repr(error.value)
    assert transport.calls == []


def test_short_key_does_not_leak_into_success_provenance_or_warnings() -> None:
    short_key = "abc"
    result = fetch(
        FmpProvider(
            transport=FixtureTransport(),
            environ={API_KEY_ENV_VAR: short_key},
            clock=lambda: NOW,
        )
    )
    assert short_key not in repr(result.provenance)
    assert all(short_key not in warning for warning in result.warnings)


def test_provider_output_containing_credential_is_rejected_not_redacted() -> None:
    short_key = "abc"
    payload = {
        ENDPOINTS[0]: [profile(companyName=short_key)],
        ENDPOINTS[1]: [bar("2024-01-03")],
        ENDPOINTS[2]: [],
        ENDPOINTS[3]: [],
    }
    transport = FixtureTransport(response_override=payload)
    selected = FmpProvider(
        transport=transport,
        environ={API_KEY_ENV_VAR: short_key},
        clock=lambda: NOW,
    )
    with pytest.raises(MarketDataConfigurationError) as error:
        fetch(selected)
    assert short_key not in str(error.value)
    assert short_key not in repr(error.value)
    assert len(transport.calls) == 4


@pytest.mark.parametrize(
    "config",
    [
        MarketDataConfig(provider="yfinance"),
        MarketDataConfig(max_calls_per_snapshot=3),
        MarketDataConfig(max_calls_per_snapshot=5),
        MarketDataConfig(max_history_days=5 * 366 + 1),
        MarketDataConfig(timeout_seconds=31),
        MarketDataConfig(max_response_bytes=9 * 1024 * 1024),
    ],
)
def test_invalid_or_unimplemented_provider_configuration_fails_closed(config) -> None:
    transport = FixtureTransport()
    with pytest.raises(MarketDataConfigurationError):
        fetch(provider(transport), config=config)
    assert transport.calls == []


@pytest.mark.parametrize(
    ("symbol", "start", "end"),
    [
        ("bad ticker", START, END),
        ("AAPL", datetime(2024, 1, 2), END),
        ("AAPL", END, START),
        ("AAPL", START, date(2024, 1, 6)),
        ("AAPL", date(2018, 1, 1), END),
    ],
)
def test_invalid_request_fails_before_network(symbol, start, end) -> None:
    transport = FixtureTransport()
    with pytest.raises(MarketDataInputError):
        fetch(provider(transport), symbol=symbol, start=start, end=end)
    assert transport.calls == []


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, MarketDataAccessError),
        (403, MarketDataAccessError),
        (429, MarketDataRateLimitError),
        (404, MarketDataNotFoundError),
        (302, MarketDataProviderError),
        (500, MarketDataProviderError),
        (400, MarketDataProviderError),
    ],
)
def test_status_failures_are_sanitized_and_never_retried(status, error_type) -> None:
    transport = FixtureTransport(status=status)
    with pytest.raises(error_type) as error:
        fetch(provider(transport))
    assert len(transport.calls) == 1
    assert API_KEY not in str(error.value)
    assert f"provider reflected {API_KEY}" not in str(error.value)
    assert API_KEY not in repr(error.value)


def test_redirect_handler_refuses_redirects() -> None:
    assert (
        _NoRedirectHandler().redirect_request(
            object(),  # type: ignore[arg-type]
            None,
            302,
            "Found",
            {},
            "https://attacker.invalid/collect",
        )
        is None
    )


def test_urllib_transport_builds_header_only_authenticated_request(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, amount: int) -> bytes:
            captured["read_limit"] = amount
            return b"[]"

    class Opener:
        def __init__(self, handler: object) -> None:
            captured["handler"] = handler

        def open(self, request, *, timeout: float):
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

    monkeypatch.setattr(urllib.request, "build_opener", lambda handler: Opener(handler))
    result = UrllibTransport().get(
        "https://financialmodelingprep.com/stable/profile?symbol=AAPL",
        headers={"apikey": API_KEY, "Accept": "application/json"},
        timeout_seconds=3,
        max_response_bytes=128,
    )
    request = captured["request"]
    assert result == TransportResponse(200, b"[]")
    assert isinstance(captured["handler"], _NoRedirectHandler)
    assert isinstance(request, urllib.request.Request)
    assert request.method == "GET"
    assert request.get_header("Apikey") == API_KEY
    assert API_KEY not in request.full_url
    assert captured["timeout"] == 3
    assert captured["read_limit"] == 129


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"Error Message":"reflected token"}',
        b"[NaN]",
        b'[{"a":1,"a":2}]',
        b"[null]",
    ],
)
def test_malformed_provider_responses_are_rejected_without_echo(body) -> None:
    transport = FixtureTransport(malformed=body)
    with pytest.raises(MarketDataDataError):
        fetch(provider(transport))
    assert len(transport.calls) == 1
    # The fixture captures the authorized request header, but public errors never echo it.


def test_oversized_body_is_rejected_without_decoding() -> None:
    transport = FixtureTransport(malformed=b" " * (4 * 1024 * 1024 + 1))
    with pytest.raises(MarketDataProviderError, match="size limit"):
        fetch(provider(transport))


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError(f"failure: {API_KEY}"),
        MarketDataProviderError(f"failure: {API_KEY}"),
        MarketDataConfigurationError(f"failure: {API_KEY}"),
    ],
)
def test_transport_exception_is_redacted(exception: Exception) -> None:
    transport = FixtureTransport(exception=exception)
    with pytest.raises(MarketDataProviderError) as error:
        fetch(provider(transport))
    assert API_KEY not in str(error.value)
    assert API_KEY not in repr(error.value)


@pytest.mark.parametrize(
    ("profile_override", "message"),
    [
        ({"symbol": "MSFT"}, "identity"),
        ({"country": "GB"}, "US exchange"),
        ({"exchange": "LSE"}, "US exchange"),
        ({"currency": "GBp"}, "USD"),
        ({"currency": "GBP"}, "USD"),
        ({"currency": None}, "currency"),
        ({"isEtf": None}, "security type"),
        ({"isFund": True}, "common equity"),
        ({"isActivelyTrading": False}, "actively trading"),
        ({"cik": None, "isin": None, "cusip": None}, "stable security identifier"),
    ],
)
def test_profile_identity_and_currency_validation(profile_override, message) -> None:
    payload = make_payload(profile_row=profile(**profile_override))
    with pytest.raises(MarketDataDataError, match=message):
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())


def test_etf_is_explicitly_typed() -> None:
    payload = make_payload(profile_row=profile(isEtf=True))
    snapshot = normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert snapshot.identity.security_type == "etf"


def test_bar_duplicate_outside_bounds_nonfinite_and_bad_ohlc_rejected() -> None:
    for rows in (
        (bar("2024-01-02"), bar("2024-01-02")),
        (bar("2024-01-01"),),
        (bar("2024-01-02", high=98.0),),
        (bar("2024-01-02", volume=True),),
        (bar("2024-01-02", close=Decimal("NaN")),),
        (bar("2024-01-02", symbol="MSFT"),),
    ):
        payload = make_payload(bar_rows=rows)
        with pytest.raises(MarketDataDataError):
            normalize_snapshot(payload_request(), payload, config=MarketDataConfig())


def test_empty_history_is_not_treated_as_success() -> None:
    payload = make_payload(bar_rows=())
    with pytest.raises(MarketDataNotFoundError):
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())


def test_action_shape_duplicates_and_zero_values_are_rejected() -> None:
    invalid_sets = (
        (
            [
                {
                    "symbol": "AAPL",
                    "date": "2024-01-03",
                    "numerator": 0,
                    "denominator": 1,
                }
            ],
            [],
        ),
        (
            [
                {
                    "symbol": "AAPL",
                    "date": "2024-01-03",
                    "numerator": 2.0,
                    "denominator": 1,
                }
            ],
            [],
        ),
        ([*SPLITS[1:], SPLITS[1]], []),
        ([], [{"symbol": "AAPL", "date": "2024-01-03", "dividend": 0}]),
        ([], [{"symbol": "MSFT", "date": "2024-01-03", "dividend": 0.2}]),
        ([], [{"symbol": "AAPL", "date": "not-a-date", "dividend": 0.2}]),
    )
    for splits, dividends in invalid_sets:
        payload = make_payload(split_rows=splits, dividend_rows=dividends)
        with pytest.raises(MarketDataDataError):
            normalize_snapshot(payload_request(), payload, config=MarketDataConfig())


def test_retrieval_times_and_endpoint_provenance_are_validated() -> None:
    payload = make_payload()
    with pytest.raises(MarketDataDataError, match="out of order"):
        normalize_snapshot(
            payload_request(),
            ProviderPayload(
                payload.profile,
                payload.bars,
                payload.splits,
                payload.dividends,
                payload.endpoints,
                payload.api_version,
                NOW,
                datetime(2024, 1, 1, tzinfo=UTC),
            ),
            config=MarketDataConfig(),
        )
    bad_endpoints = ProviderPayload(
        payload.profile,
        payload.bars,
        payload.splits,
        payload.dividends,
        ("https://financialmodelingprep.com/stable/profile?apikey=secret",),
        payload.api_version,
        NOW,
        NOW,
    )
    with pytest.raises(MarketDataDataError, match="endpoint provenance"):
        normalize_snapshot(payload_request(), bad_endpoints, config=MarketDataConfig())


def payload_request():
    return MarketDataRequest("AAPL", START, END)


def test_stable_profile_exchange_schema_offline_reproduction() -> None:
    """Stable profile uses exchange, not legacy exchangeShortName."""
    row = profile()
    row.pop("exchange", None)
    row.update(exchange="NASDAQ", exchangeFullName="NASDAQ Global Select", isAdr=False)
    result = normalize_snapshot(
        payload_request(), make_payload(profile_row=row), config=MarketDataConfig()
    )
    assert result.identity.exchange == "NASDAQ"


def test_exact_integral_json_decimal_volume_offline_reproduction() -> None:
    """JSON 123456.0 is a whole share count, not fractional volume."""
    payload = make_payload(bar_rows=(bar("2024-01-03", volume=Decimal("123456.0")),))
    result = normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert result.bars[0].volume == 123456
    assert type(result.bars[0].volume) is int


@pytest.mark.parametrize(
    "volume",
    [Decimal("1.5"), Decimal("NaN"), Decimal("Infinity"), Decimal(-1),
     Decimal(9223372036854775808), True, None, "123"],
)
def test_volume_does_not_round_default_or_coerce_text(volume) -> None:
    payload = make_payload(bar_rows=(bar("2024-01-03", volume=volume),))
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert caught.value.validation_stage == "history"
    assert caught.value.validation_reason == "volume_invalid"
    assert caught.value.validation_field == "volume"
    assert caught.value.identity_validated is True


def test_stable_wire_schema_optional_fields_and_action_currency() -> None:
    """Synthetic values; field names/shapes follow the public stable examples."""
    row = profile(cik=None, cusip="", isin="US0378331005")
    history = bar("2024-01-03", volume=123456.0, vwap=None)
    history.pop("adjClose")
    transport = FixtureTransport(response_override={
        ENDPOINTS[0]: [row],
        ENDPOINTS[1]: [history],
        ENDPOINTS[2]: [
            {"symbol": "AAPL", "date": "2024-01-03", "numerator": 4.0,
             "denominator": 1.0, "splitType": "stock-split"},
        ],
        ENDPOINTS[3]: [
            {"symbol": "AAPL", "date": "2024-01-04", "dividend": 0.27,
             "adjDividend": None, "recordDate": None, "paymentDate": "",
             "declarationDate": "", "yield": 0.42, "frequency": "Quarterly"},
        ],
    })
    result = fetch(provider(transport))
    assert result.identity.cik is None
    assert result.bars[0].volume == 123456
    assert result.bars[0].adjusted_close is None
    assert result.bars[0].adjusted_close_basis == "unavailable"
    assert result.bars[0].vwap is None
    split, dividend = result.actions
    assert (split.numerator, split.denominator) == (4, 1)
    assert dividend.amount == Decimal("0.27")
    assert dividend.adjusted_amount is None
    assert dividend.currency == result.identity.currency == "USD"
    assert dividend.record_date is dividend.payment_date is dividend.declaration_date is None
    assert not result.storage_allowed and not result.calculation_eligible
    assert len(transport.calls) == 4
    for url, *_ in transport.calls[2:]:
        # These endpoints document limit, not from/to; filtering is local.
        assert parse_qs(urlparse(url).query) == {"symbol": ["AAPL"], "limit": ["1000"]}


def test_stable_exchange_no_legacy_or_descriptive_fallback() -> None:
    row = profile(exchange=None, exchangeShortName="NASDAQ")
    row["exchangeFullName"] = "NASDAQ Global Select"
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), make_payload(profile_row=row),
                           config=MarketDataConfig())
    assert caught.value.validation_reason == "text_invalid"
    assert caught.value.validation_field == "exchange"


@pytest.mark.parametrize("required", ["dividend", "date", "symbol"])
def test_dividends_never_invent_amount_date_or_identity(required) -> None:
    action = dict(DIVIDENDS[0], amount=0.25, currency="USD")
    action.pop(required)
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), make_payload(dividend_rows=[action]),
                           config=MarketDataConfig())
    assert caught.value.validation_stage == "dividends"
    assert caught.value.validation_field == required


def test_action_window_inclusive_and_unverified_coverage_at_limit() -> None:
    rows = [
        {"symbol": "AAPL", "date": "2024-01-01", "dividend": Decimal("0.1")},
        {"symbol": "AAPL", "date": START.isoformat(), "dividend": Decimal("0.2")},
        {"symbol": "AAPL", "date": END.isoformat(), "dividend": Decimal("0.3")},
        {"symbol": "AAPL", "date": "2024-01-05", "dividend": Decimal("0.4")},
    ]
    # Full-list coverage evidence requires valid, distinct out-of-window events.
    rows += [
        {"symbol": "AAPL", "date": (START - timedelta(days=i + 2)).isoformat(),
         "dividend": Decimal("0.1")}
        for i in range(996)
    ]
    payload = make_payload(dividend_rows=rows, split_rows=[])
    result = normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert [a.event_date for a in result.actions] == [START, END]
    assert result.provenance.action_coverage == "unverified"
    assert any("reached the limit" in warning for warning in result.warnings)
    evidence = result.action_evidence[1]
    assert evidence.returned_count == 1000
    assert evidence.in_window_count == 2
    assert evidence.window_bracketed_by_returned_dates
    assert evidence.outstanding_reason == "response_limit_reached_no_documented_paging"
    assert not result.calculation_eligible and not result.storage_allowed


@pytest.mark.parametrize(
    "attribute,value,stage,reason,field,identity",
    [
        ("profile", None, "profile", "record_shape", "none", False),
        ("bars", [], "history", "collection_shape", "none", True),
        ("bars", (None,), "history", "record_shape", "none", True),
        ("splits", [], "splits", "collection_shape", "none", True),
        ("dividends", (None,), "dividends", "record_shape", "none", True),
        ("retrieved_at_start", datetime(2024, 1, 5), "provenance",
         "timestamp_invalid", "retrieved_at_start", True),
        ("retrieved_at_end", datetime(2024, 1, 5), "provenance",
         "timestamp_invalid", "retrieved_at_end", True),
        ("retrieved_at_end", datetime(2024, 1, 1, tzinfo=UTC), "provenance",
         "timestamp_order", "none", True),
        ("api_version", "", "provenance", "api_version_missing", "api_version", True),
        ("endpoints", ("unsafe",), "provenance", "endpoint_mismatch", "endpoints", True),
    ],
)
def test_payload_and_provenance_failure_codes(
    attribute, value, stage, reason, field, identity
) -> None:
    payload = replace(make_payload(), **{attribute: value})
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    error = caught.value
    assert (error.validation_stage, error.validation_reason, error.validation_field) == (
        stage, reason, field,
    )
    assert error.identity_validated is identity


def make_payload(
    *,
    profile_row: dict[str, object] | None = None,
    bar_rows: tuple[dict[str, object], ...] | None = None,
    split_rows: list[dict[str, object]] | None = None,
    dividend_rows: list[dict[str, object]] | None = None,
) -> ProviderPayload:
    selected_bars = (bar("2024-01-03"),) if bar_rows is None else bar_rows
    precise_bars = tuple(
        {
            key: Decimal(str(value)) if isinstance(value, float) else value
            for key, value in item.items()
        }
        for item in selected_bars
    )
    precise_dividends = tuple(
        {
            key: Decimal(str(value)) if isinstance(value, float) else value
            for key, value in item.items()
        }
        for item in (DIVIDENDS if dividend_rows is None else dividend_rows)
    )
    return ProviderPayload(
        profile=profile() if profile_row is None else profile_row,
        bars=precise_bars,
        splits=tuple(SPLITS[1:] if split_rows is None else split_rows),
        dividends=precise_dividends,
        endpoints=ENDPOINTS,
        api_version=API_VERSION,
        retrieved_at_start=NOW,
        retrieved_at_end=NOW,
    )


def test_possible_gaps_and_large_close_moves_are_flagged_not_rewritten() -> None:
    rows = (
        bar("2024-01-02", open=100.0, high=105.0, low=99.0, close=104.0),
        bar(
            "2024-01-08",
            open=140.0,
            high=145.0,
            low=139.0,
            close=140.0,
            vwap=142.0,
            adjClose=140.0,
        ),
    )
    request = MarketDataRequest("AAPL", START, date(2024, 1, 10))
    payload = replace(
        make_payload(bar_rows=rows),
        retrieved_at_start=datetime(2024, 1, 11, tzinfo=UTC),
        retrieved_at_end=datetime(2024, 1, 11, tzinfo=UTC),
    )
    result = normalize_snapshot(
        request, payload, config=MarketDataConfig()
    )
    assert result.bars[1].close == Decimal("140.0")
    assert any(
        "Possible missing trading sessions" in warning for warning in result.warnings
    )
    assert any("Unusual close-to-close move" in warning for warning in result.warnings)


def test_tuesday_to_friday_gap_is_flagged_without_assuming_exchange_holidays() -> None:
    rows = (
        bar("2024-01-02", open=100.0, high=105.0, low=99.0, close=104.0),
        bar(
            "2024-01-05",
            open=104.0,
            high=106.0,
            low=103.0,
            close=105.0,
            vwap=104.5,
            adjClose=105.0,
        ),
    )
    result = normalize_snapshot(
        MarketDataRequest("AAPL", START, date(2024, 1, 5)),
        replace(make_payload(bar_rows=rows),
                retrieved_at_end=datetime(2024, 1, 6, tzinfo=UTC)),
        config=MarketDataConfig(),
    )
    assert any(
        "Possible missing trading sessions" in warning for warning in result.warnings
    )
    assert any("no exchange holiday calendar" in warning for warning in result.warnings)


def test_friday_to_monday_gap_is_not_flagged_as_a_missing_weekday() -> None:
    rows = (
        bar(
            "2024-01-05",
            open=100.0,
            high=105.0,
            low=99.0,
            close=104.0,
            vwap=102.0,
            adjClose=104.0,
        ),
        bar(
            "2024-01-08",
            open=104.0,
            high=106.0,
            low=103.0,
            close=105.0,
            vwap=104.5,
            adjClose=105.0,
        ),
    )
    request = MarketDataRequest("AAPL", date(2024, 1, 5), date(2024, 1, 8))
    result = normalize_snapshot(
        request, replace(make_payload(bar_rows=rows),
                         retrieved_at_end=datetime(2024, 1, 9, tzinfo=UTC)),
        config=MarketDataConfig()
    )
    assert not any(
        "Possible missing trading sessions" in warning for warning in result.warnings
    )


def test_indirect_documentation_does_not_establish_equity_full_field_basis():
    """Synthetic /full shape, not a recorded or reconstructed market response."""
    result = normalize_snapshot(payload_request(), make_payload(), config=MarketDataConfig())
    observed = result.bars[0]
    assert observed.close_adjustment_basis == "unknown"
    assert observed.ohlc_adjustment_basis == "unknown"
    assert observed.volume_adjustment_basis == "unknown"
    assert observed.vwap_adjustment_basis == "unknown"
    # FMP's shared FAQ is not endpoint-specific evidence for an extra adjClose.
    assert observed.adjusted_close == Decimal("103.5")
    assert observed.adjusted_close_basis == "unknown"
    assert result.provenance.price_basis_evidence == (
        "insufficient_endpoint_specific_adjustment_evidence"
    )
    assert result.provenance.price_basis_sources
    assert result.provenance.action_coverage_sources
    assert result.provenance.publication_time is None


def test_adj_named_unadjusted_endpoint_shape_is_not_an_alias_for_full_ohlc():
    # FMP /non-split-adjusted documents adjOpen/adjHigh/adjLow/adjClose.
    # Those names must never silently satisfy this /full contract.
    row = {
        "symbol": "AAPL", "date": "2024-01-03", "adjOpen": Decimal(100),
        "adjHigh": Decimal(105), "adjLow": Decimal(99),
        "adjClose": Decimal(104), "volume": 1000,
    }
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), make_payload(bar_rows=(row,)),
                           config=MarketDataConfig())
    assert caught.value.validation_field == "open"


def test_empty_action_responses_do_not_prove_no_actions_or_no_adjustment():
    result = normalize_snapshot(
        payload_request(), make_payload(split_rows=[], dividend_rows=[]),
        config=MarketDataConfig(),
    )
    assert result.actions == ()
    for evidence in result.action_evidence:
        assert evidence.returned_count == evidence.in_window_count == 0
        assert evidence.earliest_returned_date is evidence.latest_returned_date is None
        assert not evidence.window_bracketed_by_returned_dates
        assert not evidence.response_limit_reached
        assert evidence.completeness == "unverified"
        assert evidence.outstanding_reason == "empty_response_no_completeness_metadata"
    assert not result.use_policy.calculation_eligible


def test_unsorted_bracketing_action_list_is_observed_not_exhaustive():
    events = [
        {"symbol": "AAPL", "date": "2024-01-08", "dividend": Decimal("0.3")},
        {"symbol": "AAPL", "date": "2024-01-01", "dividend": Decimal("0.2")},
        {"symbol": "AAPL", "date": "2024-01-04", "dividend": Decimal("0.25")},
    ]
    result = normalize_snapshot(
        payload_request(), make_payload(split_rows=[], dividend_rows=events),
        config=MarketDataConfig(),
    )
    evidence = result.action_evidence[1]
    assert evidence.returned_count == 3 and evidence.in_window_count == 1
    assert evidence.earliest_returned_date == date(2024, 1, 1)
    assert evidence.latest_returned_date == date(2024, 1, 8)
    assert evidence.window_bracketed_by_returned_dates
    assert evidence.events_without_bar == (END,)
    assert evidence.completeness == "unverified"
    assert evidence.outstanding_reason == "no_documented_completeness_or_exhaustion_metadata"
    assert len(result.actions) == 1  # Future events are not historical events.
    assert result.actions[0].event_date == END
    assert any("no matching bar date" in warning for warning in result.warnings)


@pytest.mark.parametrize("kind", ["split", "dividend"])
def test_malformed_and_duplicate_out_of_window_actions_cannot_supply_coverage(kind):
    row = (
        {"symbol": "AAPL", "date": "2023-12-01", "numerator": 2, "denominator": 1}
        if kind == "split" else
        {"symbol": "AAPL", "date": "2023-12-01", "dividend": Decimal("0.2")}
    )
    invalid = dict(row, **({"numerator": 0} if kind == "split" else {"dividend": None}))
    for events in ([invalid], [row, row]):
        payload = make_payload(
            split_rows=events if kind == "split" else [],
            dividend_rows=events if kind == "dividend" else [],
        )
        with pytest.raises(MarketDataDataError) as caught:
            normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
        assert caught.value.validation_stage == ("splits" if kind == "split" else "dividends")
        assert caught.value.identity_validated


def test_matching_split_and_dividend_do_not_rewrite_prices_or_establish_completeness():
    payload = make_payload(
        split_rows=[{
            "symbol": "AAPL", "date": "2024-01-03", "numerator": 4,
            "denominator": 1, "splitType": "stock-split",
        }],
        dividend_rows=[{
            "symbol": "AAPL", "date": "2024-01-03", "dividend": Decimal("0.4"),
            "adjDividend": Decimal("0.1"), "declarationDate": "2023-12-01",
        }],
    )
    result = normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert all(item.events_without_bar == () for item in result.action_evidence)
    assert all(item.completeness == "unverified" for item in result.action_evidence)
    dividend, split = result.actions
    assert (split.numerator, split.denominator, split.provider_split_type) == (4, 1, "stock-split")
    assert dividend.amount == Decimal("0.4")
    assert dividend.adjusted_amount == Decimal("0.1")
    assert dividend.adjusted_amount_basis == "unknown"
    assert dividend.declaration_date == date(2023, 12, 1)  # Not an availability timestamp.
    assert result.bars[0].close == Decimal("104.0")  # No split/dividend double adjustment.
    assert not result.calculation_eligible
    assert result.provenance.publication_time is None


def test_split_response_at_limit_preserves_only_observed_bounds():
    events = [
        {"symbol": "AAPL", "date": (START - timedelta(days=i)).isoformat(),
         "numerator": 2, "denominator": 1}
        for i in range(1000)
    ]
    result = normalize_snapshot(
        payload_request(), make_payload(split_rows=events, dividend_rows=[]),
        config=MarketDataConfig(),
    )
    evidence = result.action_evidence[0]
    assert evidence.response_limit_reached
    assert evidence.returned_count == 1000 and evidence.in_window_count == 1
    assert evidence.latest_returned_date == START
    assert not evidence.window_bracketed_by_returned_dates
    assert evidence.outstanding_reason == "response_limit_reached_no_documented_paging"


def test_boundary_and_stale_observations_do_not_infer_holidays_or_current_data():
    request = MarketDataRequest("AAPL", date(2024, 1, 1), date(2024, 1, 7))
    payload = replace(make_payload(), retrieved_at_end=datetime(2024, 2, 1, tzinfo=UTC))
    result = normalize_snapshot(request, payload, config=MarketDataConfig())
    evidence = result.session_evidence
    assert evidence.leading_calendar_days == 2
    assert evidence.trailing_calendar_days == 4
    assert evidence.retrieval_lag_calendar_days == 29
    assert evidence.session_coverage == "unverified_no_exchange_calendar"
    assert evidence.freshness == "unknown_no_publication_timestamp_or_session_calendar"
    assert evidence.point_in_time == "unverified_no_publication_or_revision_history"
    assert result.provenance.publication_time is None
    assert any("Partial boundary coverage" in warning for warning in result.warnings)
    assert not result.use_policy.backtest_eligible


def test_exact_boundaries_do_not_prove_expected_session_coverage_or_freshness():
    result = normalize_snapshot(
        payload_request(), make_payload(bar_rows=(bar(START.isoformat()), bar(END.isoformat()))),
        config=MarketDataConfig(),
    )
    evidence = result.session_evidence
    assert evidence.leading_calendar_days == evidence.trailing_calendar_days == 0
    assert evidence.possible_weekday_gaps == ((START, END),)
    assert evidence.session_coverage == "unverified_no_exchange_calendar"
    assert evidence.freshness.startswith("unknown_")
    assert not result.use_policy.calculation_eligible


def test_history_cannot_be_retrieved_before_its_completed_session_date():
    payload = replace(
        make_payload(),
        retrieved_at_start=datetime(2024, 1, 3, 12, tzinfo=UTC),
        retrieved_at_end=datetime(2024, 1, 3, 12, tzinfo=UTC),
    )
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert caught.value.validation_reason == "timestamp_order"
    assert caught.value.validation_field == "retrieved_at_end"


def test_direct_normalization_is_not_a_user_declaration_or_display_acceptance():
    result = normalize_snapshot(payload_request(), make_payload(), config=MarketDataConfig())
    assert not result.use_policy.research_display_eligible
    assert result.use_policy.display_scope == "private_local_ephemeral_research_only"


def test_acknowledged_validated_fetch_accepts_only_labelled_private_ephemeral_display():
    transport = FixtureTransport()
    result = fetch(provider(transport))
    policy = result.use_policy
    assert policy.research_display_eligible
    assert not any((policy.calculation_eligible, policy.backtest_eligible,
                    policy.storage_allowed, policy.nautilus_eligible,
                    policy.redistribution_allowed))
    assert not result.calculation_eligible and not result.storage_allowed
    assert "show_source_retrieval_window_and_all_limitations" in policy.display_requirements
    assert "do_not_label_as_current_or_point_in_time_verified" in policy.display_requirements
    assert "action_completeness_unverified" in policy.calculation_blockers
    assert "publication_and_revision_history_unavailable" in policy.calculation_blockers
    assert len(transport.calls) == 4


def test_final_display_scope_is_also_subject_to_credential_disclosure_guard():
    # Scope strings exist during the adapter's disclosure check, not added later.
    adapter = FmpProvider(
        transport=FixtureTransport(), environ={API_KEY_ENV_VAR: "ephemeral"}, clock=lambda: NOW
    )
    with pytest.raises(MarketDataConfigurationError):
        fetch(adapter)


@pytest.mark.parametrize("outside_window", [False, True])
@pytest.mark.parametrize(
    "kind,first_attributes,second_attributes,reason",
    [
        ("split", {"numerator": 2, "denominator": 1},
         {"numerator": 3, "denominator": 1}, "ambiguous_action"),
        ("split", {"numerator": 2, "denominator": 1},
         {"numerator": 2, "denominator": 1}, "duplicate_action"),
        ("dividend", {"dividend": Decimal("0.27")},
         {"dividend": Decimal("0.27"), "adjDividend": Decimal("0.27")}, "ambiguous_action"),
        ("dividend", {"dividend": Decimal("0.27")},
         {"dividend": Decimal("0.54")}, "ambiguous_action"),
        ("dividend", {"dividend": Decimal("0.27")},
         {"dividend": Decimal("0.27"), "paymentDate": "2024-01-12"}, "ambiguous_action"),
        ("dividend", {"dividend": Decimal("0.27")},
         {"dividend": Decimal("0.27")}, "duplicate_action"),
    ],
)
def test_same_symbol_kind_date_is_not_disambiguated_by_amount_ratio_or_metadata(
    kind, first_attributes, second_attributes, reason, outside_window
):
    day = "2023-12-01" if outside_window else "2024-01-03"
    events = [
        {"symbol": "AAPL", "date": day, **attributes}
        for attributes in (first_attributes, second_attributes)
    ]
    for ordered in (events, list(reversed(events))):
        payload = make_payload(
            split_rows=ordered if kind == "split" else [],
            dividend_rows=ordered if kind == "dividend" else [],
        )
        with pytest.raises(MarketDataDataError) as caught:
            normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
        assert caught.value.validation_reason == reason
        assert caught.value.validation_field == "date"
        assert caught.value.validation_stage == ("splits" if kind == "split" else "dividends")


@pytest.mark.parametrize(
    "attributes,field",
    [
        ({"declarationDate": "2024-01-04", "paymentDate": "2024-01-03"}, "payment_date"),
        ({"declarationDate": "2024-01-04", "recordDate": "2024-01-03"}, "record_date"),
        ({"recordDate": "2024-01-04", "paymentDate": "2024-01-03"}, "payment_date"),
    ],
)
@pytest.mark.parametrize("outside_window", [False, True])
def test_dividend_optional_date_chronology_rejects_reversals(attributes, field, outside_window):
    payload = make_payload(dividend_rows=[{
        "symbol": "AAPL", "date": "2023-12-01" if outside_window else "2024-01-03",
        "dividend": Decimal("0.27"), **attributes,
    }])
    with pytest.raises(MarketDataDataError) as caught:
        normalize_snapshot(payload_request(), payload, config=MarketDataConfig())
    assert caught.value.validation_reason == "action_date_order"
    assert caught.value.validation_field == field
    assert caught.value.validation_stage == "dividends"


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"declarationDate": None, "recordDate": "", "paymentDate": None},
        {"declarationDate": "2024-01-03"},
        {"recordDate": "2024-01-03"},
        {"paymentDate": "2024-01-03"},
        {"declarationDate": "2024-01-03", "recordDate": "2024-01-03",
         "paymentDate": "2024-01-03"},
        # Do not impose ex-date <= payment-date; special dividends may differ.
        {"declarationDate": "2023-12-01", "recordDate": "2023-12-20",
         "paymentDate": "2024-01-02"},
    ],
)
def test_dividend_chronology_preserves_missing_equal_and_special_event_dates(attributes):
    result = normalize_snapshot(
        payload_request(), make_payload(split_rows=[], dividend_rows=[{
            "symbol": "AAPL", "date": "2024-01-03",
            "dividend": Decimal("0.27"), **attributes,
        }]), config=MarketDataConfig(),
    )
    action = result.actions[0]
    for wire_field, attr in (
        ("declarationDate", "declaration_date"), ("recordDate", "record_date"),
        ("paymentDate", "payment_date"),
    ):
        supplied = attributes.get(wire_field)
        assert getattr(action, attr) == (date.fromisoformat(supplied) if supplied else None)
    assert not result.calculation_eligible
