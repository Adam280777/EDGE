"""Counts-only check tests: mocked HTTP, synthetic records, no live credentials."""

from __future__ import annotations

import builtins
import io
import json
import os
import socket
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from edge.data import fmp_check
from edge.data.fmp_provider import (
    API_KEY_ENV_VAR,
    ENDPOINTS,
    FmpCredentialInPlanLabelError,
    FmpInvalidApiKeyError,
    FmpMissingApiKeyError,
    FmpProvider,
    TransportResponse,
)
from edge.data.market_data import (
    MarketDataConfig,
    MarketDataConfigurationError,
    MarketDataDataError,
    MarketDataProviderError,
    ProviderInfo,
)

SECRET = "synthetic-secret-never-print"
PRIVATE = "SYNTHETIC-COMPANY-DO-NOT-OUTPUT"
FLAGS = ["--confirm-private-research", "--confirm-endpoint-access"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    def blocked(*args, **kwargs):
        raise AssertionError("Network forbidden.")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv(API_KEY_ENV_VAR, SECRET)
    monkeypatch.chdir(tmp_path)


def records():
    today = datetime.now(UTC).date()
    start = today - timedelta(days=14)
    return {
        ENDPOINTS[0]: [
            {
                "symbol": "MSFT",
                "companyName": PRIVATE,
                "exchange": "NASDAQ",
                "exchangeFullName": "NASDAQ Global Select",
                "country": "US",
                "currency": "USD",
                "isEtf": False,
                "isFund": False,
                "isAdr": False,
                "isActivelyTrading": True,
                "cik": "0000000001",
            }
        ],
        ENDPOINTS[1]: [
            {
                "symbol": "MSFT",
                "date": start.isoformat(),
                "open": 123.45,
                "high": 130.55,
                "low": 120.25,
                "close": 125.75,
                "volume": 987654321,
            }
        ],
        ENDPOINTS[2]: [],
        ENDPOINTS[3]: [],
    }


def install_transport(monkeypatch, payload=None, *, status=200, fail_at=None):
    payload = records() if payload is None else payload
    calls = []

    def get(self, url, *, headers, timeout_seconds, max_response_bytes):
        calls.append(url)
        assert headers["apikey"] == SECRET
        assert SECRET not in url
        assert timeout_seconds == 10
        assert max_response_bytes == 4 * 1024 * 1024
        if fail_at == len(calls):
            raise RuntimeError(f"{SECRET} https://unsafe.example {PRIVATE}")
        return TransportResponse(
            status, json.dumps(payload[urlsplit(url).path]).encode()
        )

    monkeypatch.setattr(fmp_check.UrllibTransport, "get", get)
    return calls


def assert_safe(text):
    for forbidden in (
        SECRET,
        PRIVATE,
        "https://",
        "123.45",
        "130.55",
        "125.75",
        "987654321",
        "Traceback",
        "apikey",
        "companyName",
    ):
        assert forbidden not in text
    assert "storage_allowed=false" in text
    assert "calculation_eligible=false" in text
    assert "nautilus_eligible=false" in text
    assert "backtest_eligible=false" in text
    assert "redistribution_allowed=false" in text
    assert "entitlement_verified=false" in text
    assert "research_display_eligible=" in text
    if "status=validated_research_snapshot;" not in text:
        assert "research_display_eligible=false" in text
        assert "research_display_eligible=true" not in text


def test_success_bounded_no_writes_or_data_output(monkeypatch, capsys, tmp_path):
    calls = install_transport(monkeypatch)

    def no_file_access(*args, **kwargs):
        raise AssertionError("Check must not open files.")

    with monkeypatch.context() as guard:
        guard.setattr(builtins, "open", no_file_access)
        guard.setattr(io, "open", no_file_access)
        guard.setattr(os, "open", no_file_access)
        guard.setattr(sqlite3, "connect", no_file_access)
        assert fmp_check.main(FLAGS) == 0
    out = capsys.readouterr()
    assert_safe(out.out)
    assert out.err == ""
    assert "identity_validation=passed" in out.out
    assert "research_display_eligible=true" in out.out
    assert "backtest_eligible=false" in out.out
    assert "redistribution_allowed=false" in out.out
    assert "whole_ohlc_basis=unknown" in out.out
    assert "close_basis=unknown" in out.out
    assert "basis_evidence=insufficient_endpoint_specific_adjustment_evidence" in out.out
    assert "session_coverage=unverified; freshness=unknown" in out.out
    assert "action_kind=split; returned_rows=0; in_window_rows=0" in out.out
    assert "point_in_time_unverified" in out.out
    assert "rows=1; actions=0; splits=0; dividends=0" in out.out
    assert "partial_boundary_coverage" in out.out
    assert "requests_attempted=4; request_limit=4; retries=0" in out.out
    assert list(tmp_path.iterdir()) == []
    assert [urlsplit(url).path for url in calls] == list(ENDPOINTS)
    history = parse_qs(urlsplit(calls[1]).query)
    today = datetime.now(UTC).date()
    assert history == {
        "symbol": ["MSFT"],
        "from": [(today - timedelta(days=14)).isoformat()],
        "to": [(today - timedelta(days=1)).isoformat()],
    }


@pytest.mark.parametrize(
    "args",
    [
        [],
        FLAGS[:1],
        FLAGS[1:],
        FLAGS + ["--symbol", "AAPL"],
        FLAGS + ["--plan", SECRET],
        FLAGS + ["--confirm-private"],
        FLAGS + ["--start-date", SECRET],
    ],
)
def test_gates_and_argument_errors_make_no_requests(args, monkeypatch, capsys):
    calls = install_transport(monkeypatch)
    assert fmp_check.main(args) == 2
    assert calls == []
    output = capsys.readouterr()
    assert_safe(output.out)
    assert output.err == ""


def test_missing_key_no_requests(monkeypatch, capsys):
    monkeypatch.delenv(API_KEY_ENV_VAR)
    calls = install_transport(monkeypatch)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    assert_safe(capsys.readouterr().out)


def test_reported_preflight_summary_identifies_missing_key(monkeypatch, capsys):
    """Reproduce the reported zero-attempt summary without a real credential."""
    monkeypatch.delenv(API_KEY_ENV_VAR)
    calls = install_transport(monkeypatch)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == (
        "status=configuration_error; identity_validation=not_established\n"
        "configuration_reason=missing_key; recovery=enter_key_at_hidden_prompt\n"
        "warning=check_incomplete_no_acceptance\n"
        "requests_attempted=0; request_limit=4; retries=0\n"
        "endpoints_http_200=none; "
        "http_success_is_not_data_or_entitlement_acceptance\n"
        "storage_allowed=false; calculation_eligible=false; "
        "backtest_eligible=false; nautilus_eligible=false; "
        "redistribution_allowed=false; entitlement_verified=false\n"
        "research_display_eligible=false\n"
    )


@pytest.mark.parametrize(
    "value,reason",
    [
        ("", "missing_key"),
        (" ", "invalid_key_format"),
        (" synthetic-key", "invalid_key_format"),
        ("synthetic-key ", "invalid_key_format"),
        ("synthetic-key\r\nforged=status", "invalid_key_format"),
        ("Read-Host 'synthetic-key'", "invalid_key_format"),
        ("$env:EDGE_FMP_API_KEY=synthetic-key", "invalid_key_format"),
        ("https://unsafe.example/synthetic-key", "invalid_key_format"),
        ("synthetic-key\u200b", "invalid_key_format"),
        ("x" * 513, "invalid_key_format"),
        ("private", "credential_in_plan_label"),
    ],
)
def test_credential_preflight_allowlisted_no_leaks(
    value, reason, monkeypatch, capsys, tmp_path
):
    monkeypatch.setenv(API_KEY_ENV_VAR, value)
    calls = install_transport(monkeypatch)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert f"configuration_reason={reason}; recovery=" in output.out
    if value.strip():
        assert value not in output.out
    assert len(output.out.splitlines()) == 7
    assert "requests_attempted=0; request_limit=4; retries=0" in output.out
    assert_safe(output.out)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "args,reason,recovery",
    [
        ([], "missing_confirmations", "confirm_rights_and_access_if_permitted"),
        (FLAGS[:1], "missing_confirmations", "confirm_rights_and_access_if_permitted"),
        (FLAGS[1:], "missing_confirmations", "confirm_rights_and_access_if_permitted"),
        (FLAGS + ["--unknown", SECRET], "invalid_arguments", "use_documented_flags"),
        (FLAGS + ["--symbol", SECRET], "invalid_arguments", "use_documented_flags"),
    ],
)
def test_argument_preflight_reasons(args, reason, recovery, monkeypatch, capsys):
    calls = install_transport(monkeypatch)
    assert fmp_check.main(args) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert f"configuration_reason={reason}; recovery={recovery}" in output.out
    assert_safe(output.out)


@pytest.mark.parametrize(
    "error_type,reason,recovery",
    [
        (
            MarketDataConfigurationError,
            "invalid_configuration",
            "review_local_configuration",
        ),
        (FmpMissingApiKeyError, "missing_key", "enter_key_at_hidden_prompt"),
        (
            FmpInvalidApiKeyError,
            "invalid_key_format",
            "reenter_key_only_at_hidden_prompt",
        ),
        (
            FmpCredentialInPlanLabelError,
            "credential_in_plan_label",
            "use_nonsecret_plan_label",
        ),
    ],
)
def test_configuration_exceptions_never_echo_message(
    error_type, reason, recovery, monkeypatch, capsys
):
    def rejected(*args, **kwargs):
        error = error_type(f"{SECRET}\nconfiguration_reason=forged; recovery=unsafe")
        error.reason = SECRET
        error.recovery = SECRET
        raise error

    calls = install_transport(monkeypatch)
    monkeypatch.setattr(fmp_check, "fetch_daily_snapshot", rejected)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert f"configuration_reason={reason}; recovery={recovery}" in output.out
    assert "forged" not in output.out
    assert_safe(output.out)


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": SECRET},
        {"plan_label": f"{SECRET}\nforged=status"},
        {"max_history_days": 0},
        {"max_history_days": 1831},
        {"max_calls_per_snapshot": 0},
        {"max_calls_per_snapshot": 3},
        {"max_calls_per_snapshot": 5},
        {"timeout_seconds": 0},
        {"timeout_seconds": 31},
        {"max_response_bytes": 1023},
        {"max_response_bytes": 8 * 1024 * 1024 + 1},
    ],
)
def test_real_config_validation_branches_make_no_calls(overrides, monkeypatch, capsys):
    def config(**kwargs):
        return replace(MarketDataConfig(**kwargs), **overrides)

    calls = install_transport(monkeypatch)
    monkeypatch.setattr(fmp_check, "MarketDataConfig", config)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert "configuration_reason=invalid_configuration;" in output.out
    assert "requests_attempted=0;" in output.out
    assert_safe(output.out)


@pytest.mark.parametrize("case", ["clock", "adapter_name", "adapter_description"])
def test_provider_contract_preflight_no_calls(case, monkeypatch, capsys):
    class InvalidProvider(FmpProvider):
        name = SECRET if case == "adapter_name" else "fmp"

        def describe(self):
            if case == "adapter_description":
                return ProviderInfo("fmp", SECRET, (SECRET,))
            return super().describe()

    def provider(**kwargs):
        if case == "clock":
            kwargs["clock"] = lambda: datetime(2026, 1, 1)
        return InvalidProvider(**kwargs)

    calls = install_transport(monkeypatch)
    monkeypatch.setattr(fmp_check, "FmpProvider", provider)
    assert fmp_check.main(FLAGS) == 2
    assert calls == []
    output = capsys.readouterr()
    assert output.err == ""
    assert "configuration_reason=invalid_configuration;" in output.out
    assert "requests_attempted=0;" in output.out
    assert_safe(output.out)


@pytest.mark.parametrize(
    "status,code,label",
    [
        (401, 3, "access_rejected"),
        (403, 3, "access_rejected"),
        (429, 4, "rate_limit"),
        (404, 5, "not_found"),
        (302, 7, "provider_error"),
        (503, 7, "provider_error"),
    ],
)
def test_http_failures_stop_without_retry(status, code, label, monkeypatch, capsys):
    calls = install_transport(monkeypatch, status=status)
    assert fmp_check.main(FLAGS) == code
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert f"status={label}" in out
    assert "endpoints_http_200=none;" in out
    assert_safe(out)


@pytest.mark.parametrize("attempt", [1, 2, 3, 4])
def test_partial_transport_failures_are_sanitized(attempt, monkeypatch, capsys):
    calls = install_transport(monkeypatch, fail_at=attempt)
    assert fmp_check.main(FLAGS) == 7
    assert len(calls) == attempt
    out = capsys.readouterr().out
    endpoints = ",".join(ENDPOINTS[: attempt - 1]) or "none"
    assert f"endpoints_http_200={endpoints};" in out
    assert_safe(out)


@pytest.mark.parametrize(
    "case,code",
    [
        ("missing", 5),
        ("malformed", 6),
        ("identity", 6),
        ("duplicate", 6),
        ("stale", 6),
    ],
)
def test_invalid_records_never_escape(case, code, monkeypatch, capsys):
    payload = records()
    if case == "missing":
        payload[ENDPOINTS[1]] = []
    elif case == "malformed":
        payload[ENDPOINTS[1]][0]["close"] = SECRET
    elif case == "identity":
        payload[ENDPOINTS[0]][0]["symbol"] = "AAPL"
    elif case == "duplicate":
        payload[ENDPOINTS[1]] *= 2
    else:
        payload[ENDPOINTS[1]][0]["date"] = "2001-01-01"
    calls = install_transport(monkeypatch, payload)
    assert fmp_check.main(FLAGS) == code
    assert len(calls) == 4
    out = capsys.readouterr().out
    expected_identity = "not_established" if case == "identity" else "passed"
    assert f"identity_validation={expected_identity}" in out
    assert_safe(out)


def test_independent_budget_rejects_fifth_or_repeated_endpoint():
    class Transport:
        calls = 0

        def get(self, *args, **kwargs):
            self.calls += 1
            return TransportResponse(200, b"[]")

    underlying = Transport()
    bounded = fmp_check._BoundedTransport(underlying)
    kwargs = {"headers": {}, "timeout_seconds": 10, "max_response_bytes": 1024}
    for endpoint in ENDPOINTS:
        bounded.get(f"{fmp_check.BASE_URL}{endpoint}", **kwargs)
    with pytest.raises(MarketDataProviderError):
        bounded.get(f"{fmp_check.BASE_URL}{ENDPOINTS[0]}", **kwargs)
    assert underlying.calls == 4
    repeated = fmp_check._BoundedTransport(underlying)
    repeated.get(f"{fmp_check.BASE_URL}{ENDPOINTS[0]}", **kwargs)
    with pytest.raises(MarketDataProviderError):
        repeated.get(f"{fmp_check.BASE_URL}{ENDPOINTS[0]}", **kwargs)
    assert underlying.calls == 5


def test_unexpected_error_is_sanitized(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise ValueError(f"{SECRET} {PRIVATE}")

    monkeypatch.setattr(fmp_check, "fetch_daily_snapshot", fail)
    assert fmp_check.main(FLAGS) == 8
    assert_safe(capsys.readouterr().out)


def test_action_counts_without_amounts(monkeypatch, capsys):
    payload = records()
    day = payload[ENDPOINTS[1]][0]["date"]
    payload[ENDPOINTS[2]] = [
        {
            "symbol": "MSFT",
            "date": day,
            "numerator": 2,
            "denominator": 1,
        }
    ]
    payload[ENDPOINTS[3]] = [
        {
            "symbol": "MSFT",
            "date": day,
            "dividend": 0.123456,
        }
    ]
    install_transport(monkeypatch, payload)
    assert fmp_check.main(FLAGS) == 0
    out = capsys.readouterr().out
    assert "actions=2; splits=1; dividends=1" in out
    assert "0.123456" not in out
    assert_safe(out)


@pytest.mark.parametrize(
    "endpoint,attributes,reason,field",
    [
        (2, [{"numerator": 2, "denominator": 1},
             {"numerator": 3, "denominator": 1}], "ambiguous_action", "date"),
        (3, [{"dividend": 0.271839},
             {"dividend": 0.271839, "adjDividend": 0.271839}], "ambiguous_action", "date"),
        (3, [{"dividend": 0.271839, "paymentDate": "2024-01-02",
              "declarationDate": "2024-01-03"}], "action_date_order", "payment_date"),
        (3, [{"dividend": 0.271839, "recordDate": "2024-01-02",
              "declarationDate": "2024-01-03"}], "action_date_order", "record_date"),
        (3, [{"dividend": 0.271839, "recordDate": "2024-01-03",
              "paymentDate": "2024-01-02"}], "action_date_order", "payment_date"),
    ],
)
def test_action_ambiguity_and_chronology_fail_closed_without_values(
    endpoint, attributes, reason, field, monkeypatch, capsys, tmp_path
):
    payload = records()
    day = payload[ENDPOINTS[1]][0]["date"]
    payload[ENDPOINTS[endpoint]] = [
        {"symbol": "MSFT", "date": day, "private_note": SECRET, **item}
        for item in attributes
    ]
    calls = install_transport(monkeypatch, payload)
    assert fmp_check.main(FLAGS) == 6
    output = capsys.readouterr()
    assert output.err == ""
    stage = "splits" if endpoint == 2 else "dividends"
    assert (
        f"validation_stage={stage}; validation_reason={reason}; "
        f"validation_field={field}; recovery="
    ) in output.out
    assert "identity_validation=passed" in output.out
    assert "research_display_eligible=false" in output.out
    assert "0.271839" not in output.out
    assert "2024-01-02" not in output.out
    assert "2024-01-03" not in output.out
    assert len(calls) == 4
    assert_safe(output.out)
    assert list(tmp_path.iterdir()) == []


def test_malformed_json_is_sanitized(monkeypatch, capsys):
    calls = []

    def malformed(self, *args, **kwargs):
        calls.append(1)
        return TransportResponse(200, f"invalid {SECRET} {PRIVATE}".encode())

    monkeypatch.setattr(fmp_check.UrllibTransport, "get", malformed)
    assert fmp_check.main(FLAGS) == 6
    assert len(calls) == 1
    assert_safe(capsys.readouterr().out)


def test_interruption_is_sanitized(monkeypatch, capsys):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt(SECRET)

    monkeypatch.setattr(fmp_check, "fetch_daily_snapshot", interrupted)
    assert fmp_check.main(FLAGS) == 130
    assert_safe(capsys.readouterr().out)


@pytest.mark.parametrize(
    "endpoint,field,value,reason,safe_field",
    [
        (0, "symbol", SECRET, "symbol_mismatch", "symbol"),
        (0, "companyName", None, "text_invalid", "company_name"),
        (0, "companyName", "x" * 201, "name_length", "company_name"),
        (0, "exchange", None, "text_invalid", "exchange"),
        (0, "exchange", "LSE", "listing_unsupported", "exchange"),
        (0, "country", None, "text_invalid", "country"),
        (0, "country", "GB", "listing_unsupported", "country"),
        (0, "currency", None, "text_invalid", "currency"),
        (0, "currency", "GBp", "currency_unsupported", "currency"),
        (0, "isActivelyTrading", None, "inactive_security", "is_actively_trading"),
        (0, "isEtf", None, "type_flags_invalid", "type_flags"),
        (0, "isFund", "false", "type_flags_invalid", "type_flags"),
        (0, "isAdr", None, "type_flags_invalid", "type_flags"),
        (0, "isAdr", True, "security_type_unsupported", "type_flags"),
        (0, "isFund", True, "security_type_unsupported", "type_flags"),
        (0, "cik", SECRET, "identifier_invalid", "cik"),
        (0, "cik", None, "identifiers_missing", "none"),
        (1, "symbol", SECRET, "symbol_mismatch", "symbol"),
        (1, "date", None, "date_invalid", "date"),
        (1, "date", "2001-01-01", "date_outside_window", "date"),
        (1, "open", None, "number_invalid", "open"),
        (1, "close", SECRET, "number_invalid", "close"),
        (1, "low", 0, "number_range", "low"),
        (1, "high", 100, "ohlc_inconsistent", "none"),
        (1, "vwap", 999, "vwap_range", "vwap"),
        (1, "adjClose", -1, "number_range", "adj_close"),
        (1, "volume", 42.5, "volume_invalid", "volume"),
        (2, "symbol", SECRET, "symbol_mismatch", "symbol"),
        (2, "date", None, "date_invalid", "date"),
        (2, "numerator", None, "split_ratio_invalid", "numerator"),
        (2, "denominator", 0, "split_ratio_invalid", "denominator"),
        (3, "symbol", SECRET, "symbol_mismatch", "symbol"),
        (3, "date", None, "date_invalid", "date"),
        (3, "dividend", None, "number_invalid", "dividend"),
        (3, "adjDividend", -1, "number_range", "adj_dividend"),
        (3, "recordDate", SECRET, "date_invalid", "record_date"),
        (3, "paymentDate", "2024-02-30", "date_invalid", "payment_date"),
        (3, "declarationDate", 20240101, "date_invalid", "declaration_date"),
        (3, "currency", "EUR", "currency_unsupported", "currency"),
    ],
)
def test_field_failure_codes_no_values_or_persistence(
    endpoint, field, value, reason, safe_field, monkeypatch, capsys, tmp_path
):
    payload = records()
    day = payload[ENDPOINTS[1]][0]["date"]
    payload[ENDPOINTS[2]] = [
        {"symbol": "MSFT", "date": day, "numerator": 4, "denominator": 1,
         "splitType": "stock-split"}
    ]
    payload[ENDPOINTS[3]] = [
        {"symbol": "MSFT", "date": day, "dividend": 0.27, "adjDividend": 0.27,
         "recordDate": None, "paymentDate": "", "declarationDate": "",
         "yield": 0.42, "frequency": "Quarterly"}
    ]
    payload[ENDPOINTS[endpoint]][0][field] = value
    calls = install_transport(monkeypatch, payload)
    assert fmp_check.main(FLAGS) == 6
    assert len(calls) == 4
    out = capsys.readouterr()
    assert out.err == ""
    stage = ("profile", "history", "splits", "dividends")[endpoint]
    identity = "not_established" if endpoint == 0 else "passed"
    assert f"identity_validation={identity}" in out.out
    assert (
        f"validation_stage={stage}; validation_reason={reason}; "
        f"validation_field={safe_field}; recovery="
    ) in out.out
    assert "offline_no_retry" in out.out
    assert "requests_attempted=4; request_limit=4; retries=0" in out.out
    assert_safe(out.out)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("endpoint", range(4))
@pytest.mark.parametrize(
    "body,reason",
    [
        (b"not-json", "json_invalid"),
        (b'[{"symbol":"MSFT","symbol":"MSFT"}]', "json_invalid"),
        (b'[{"volume":NaN}]', "json_invalid"),
        (b'{"Error Message":"synthetic-secret-never-print"}', "response_shape"),
        (b'{"symbol":"MSFT","historical":[]}', "response_shape"),
        (b"[null]", "record_shape"),
    ],
)
def test_decode_stage_is_endpoint_not_http_acceptance(
    endpoint, body, reason, monkeypatch, capsys
):
    payload = records()
    calls = []

    def get(self, url, **kwargs):
        calls.append(url)
        path = urlsplit(url).path
        data = body if path == ENDPOINTS[endpoint] else json.dumps(payload[path]).encode()
        return TransportResponse(200, data)

    monkeypatch.setattr(fmp_check.UrllibTransport, "get", get)
    assert fmp_check.main(FLAGS) == 6
    out = capsys.readouterr().out
    stage = ("profile", "history", "splits", "dividends")[endpoint]
    assert f"validation_stage={stage}; validation_reason={reason};" in out
    # Profile was fetched but NOT normalized before decoding the other endpoints.
    assert "identity_validation=not_established" in out
    assert len(calls) == endpoint + 1
    assert_safe(out)


@pytest.mark.parametrize(
    "endpoint,kind,reason,code,identity",
    [
        (0, "empty", "profile_count", 5, "not_established"),
        (0, "duplicate", "profile_count", 5, "not_established"),
        (1, "empty", "history_empty", 5, "passed"),
        (1, "duplicate", "duplicate_bar", 6, "passed"),
        (1, "limit", "record_limit", 6, "not_established"),
        (2, "limit", "record_limit", 6, "not_established"),
        (3, "limit", "record_limit", 6, "not_established"),
        (2, "duplicate", "duplicate_action", 6, "passed"),
        (3, "duplicate", "duplicate_action", 6, "passed"),
    ],
)
def test_collection_and_limit_codes(
    endpoint, kind, reason, code, identity, monkeypatch, capsys
):
    payload = records()
    day = payload[ENDPOINTS[1]][0]["date"]
    payload[ENDPOINTS[2]] = [
        {"symbol": "MSFT", "date": day, "numerator": 2, "denominator": 1}
    ]
    payload[ENDPOINTS[3]] = [
        {"symbol": "MSFT", "date": day, "dividend": 0.27}
    ]
    if kind == "empty":
        payload[ENDPOINTS[endpoint]] = []
    else:
        count = (5001 if endpoint == 1 else 1001) if kind == "limit" else 2
        payload[ENDPOINTS[endpoint]] *= count
    calls = install_transport(monkeypatch, payload)
    assert fmp_check.main(FLAGS) == code
    out = capsys.readouterr().out
    stage = ("profile", "history", "splits", "dividends")[endpoint]
    assert f"validation_stage={stage}; validation_reason={reason};" in out
    assert f"identity_validation={identity}" in out
    assert len(calls) <= 4
    assert_safe(out)


@pytest.mark.parametrize("attribute", ["validation_stage", "validation_reason", "validation_field"])
@pytest.mark.parametrize("value", [SECRET, [SECRET], {"unsafe": SECRET}, None])
def test_untrusted_diagnostic_attributes_are_not_emitted(
    attribute, value, monkeypatch, capsys
):
    def fail(*args, **kwargs):
        error = MarketDataDataError(f"{PRIVATE} {SECRET} https://unsafe.example")
        setattr(error, attribute, value)
        error.identity_validated = True
        raise error

    monkeypatch.setattr(fmp_check, "fetch_daily_snapshot", fail)
    assert fmp_check.main(FLAGS) == 6
    out = capsys.readouterr().out
    assert "identity_validation=not_established" in out
    assert (
        "validation_stage=snapshot; validation_reason=invalid_record; "
        "validation_field=none;"
    ) in out
    assert_safe(out)
