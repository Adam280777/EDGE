"""Offline tests for the opt-in SEC filing metadata adapter.

No test performs real network I/O: sockets are blocked for every test, and the
OpenBB integration tests replace openbb-sec's rate-limited request function
with fixture responses.
"""

from __future__ import annotations

import importlib.util
import socket
import subprocess
import sys
from datetime import date, datetime, timezone
from typing import Any

import pytest

from edge.data.sec_filings import (
    ALLOWED_FORM_TYPES,
    USER_AGENT_ENV_VAR,
    SecFilingConfigurationError,
    SecFilingDataError,
    SecFilingInputError,
    SecFilingNotFoundError,
    SecFilingProviderError,
    SecProviderInfo,
    SecProviderPayload,
    lookup_sec_filings,
)

UA = "Jane Analyst jane.analyst@edge-research-tests.org"
ENV = {USER_AGENT_ENV_VAR: UA}
NOW = datetime(2026, 11, 5, 17, 0, tzinfo=timezone.utc)
CIK = "0000320193"
BASE = "https://www.sec.gov/Archives/edgar/data/320193/"


def clock() -> datetime:
    return NOW


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    real_connect = socket.socket.connect

    def blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("network access attempted during tests")

    def loopback_only(sock: socket.socket, address: Any) -> None:
        # asyncio's Windows event loop builds a loopback socketpair; allow only that.
        if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            blocked()
        real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("OPENBB_SEC_CACHE_DIR", str(tmp_path / "openbb-sec-cache"))
    monkeypatch.delenv(USER_AGENT_ENV_VAR, raising=False)


def record(accession: str, form: str, filed: str, report: str | None = "2024-09-28",
           accepted: str | None = "2024-11-01T06:01:36.000Z", doc: str | None = "doc.htm") -> dict[str, Any]:
    return {
        "accession_number": accession, "report_type": form,
        "filing_date": date.fromisoformat(filed) if filed else None,
        "report_date": date.fromisoformat(report) if report else None,
        "accepted_date": accepted, "primary_doc": doc,
        "report_url": f"{BASE}{accession.replace('-', '')}/{doc}",
        "filing_detail_url": f"{BASE}{accession}-index.htm",
    }


GOOD = (
    record("0000320193-24-000123", "10-K", "2024-11-01"),
    record("0000320193-24-000120", "8-K", "2024-10-31", report=None),
    record("0001140361-24-000001", "4", "2024-10-15"),  # filer-agent prefix; not requested
    record("0000320193-24-000081", "10-Q", "2024-08-02", report="2024-06-29"),
)


class FakeProvider:
    def __init__(self, payload: SecProviderPayload | None = None, error: Exception | None = None) -> None:
        self.payload = payload or SecProviderPayload(CIK, "AAPL", GOOD)
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def describe(self) -> SecProviderInfo:
        return SecProviderInfo("fake", "0", "none", ("fixture://sec",))

    def lookup(self, sec_ticker: str, *, user_agent: str) -> SecProviderPayload:
        self.calls.append((sec_ticker, user_agent))
        if self.error:
            raise self.error
        return self.payload


def run(provider: FakeProvider, ticker: str = "aapl", **kwargs: Any):
    return lookup_sec_filings(ticker, environ=kwargs.pop("environ", ENV), provider=provider, clock=clock, **kwargs)


# ------------------------------------------------------------ service ---

def test_valid_lookup_returns_identity_dates_and_provenance() -> None:
    provider = FakeProvider()
    result = run(provider)
    assert provider.calls == [("AAPL", UA)]
    assert result.identity.cik == CIK and result.identity.sec_primary_ticker == "AAPL"
    assert [f.form_type for f in result.filings] == ["10-K", "8-K", "10-Q"]
    first = result.filings[0]
    assert first.filing_date == date(2024, 11, 1) and first.report_date == date(2024, 9, 28)
    assert first.accepted_at == datetime(2024, 11, 1, 6, 1, 36, tzinfo=timezone.utc)
    assert first.filing_index_url == f"{BASE}0000320193-24-000123-index.htm"
    assert result.provider_record_count == 4 and result.excluded_record_count == 1
    assert result.coverage_start == date(2024, 8, 2) and result.coverage_may_be_incomplete
    prov = result.provenance
    assert prov.cache_used is False and prov.user_agent_configured
    assert prov.retrieved_at_start == NOW and "not real-time" in prov.timeliness
    assert "not simulation input" in prov.usage_scope


def test_user_agent_is_never_exposed_in_result() -> None:
    result = run(FakeProvider(SecProviderPayload(CIK, "AAPL", GOOD, (f"warn mentioning {UA}",))))
    text = repr(result)
    assert UA not in text and "jane.analyst@" not in text
    assert any("[redacted]" in w for w in result.warnings)


def test_date_window_and_form_filter_are_exact() -> None:
    result = run(FakeProvider(), form_types=["10-q"], start_date=date(2024, 8, 1), end_date=date(2024, 10, 31))
    assert [f.accession_number for f in result.filings] == ["0000320193-24-000081"]
    assert result.coverage_may_be_incomplete is True  # 2024-08-01 precedes coverage_start


def test_empty_selection_is_explicit_not_an_error() -> None:
    result = run(FakeProvider(), form_types=["DEF 14A"])
    assert result.filings == ()
    assert any("No filings of the requested form types" in w for w in result.warnings)


@pytest.mark.parametrize("environ", [{}, {USER_AGENT_ENV_VAR: "  "}, {USER_AGENT_ENV_VAR: "no email here"},
    {USER_AGENT_ENV_VAR: "only@edge-research-tests.org"},
    {USER_AGENT_ENV_VAR: "my real company name definitelynot@fakecompany.com"},
    {USER_AGENT_ENV_VAR: "Jane jane@example.com"},
    {USER_AGENT_ENV_VAR: "Jane jane@x.org\r\nX-Injected: 1"}])
def test_missing_or_invalid_user_agent_fails_before_provider(environ: dict[str, str]) -> None:
    provider = FakeProvider()
    with pytest.raises(SecFilingConfigurationError) as info:
        run(provider, environ=environ)
    assert provider.calls == []
    assert "jane@" not in str(info.value) and "definitelynot" not in str(info.value)


def test_user_agent_read_from_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_AGENT_ENV_VAR, UA)
    provider = FakeProvider()
    lookup_sec_filings("AAPL", provider=provider, clock=clock)
    assert provider.calls[0][1] == UA


@pytest.mark.parametrize("kwargs", [
    {"ticker": "AAPL1"}, {"ticker": "TOOLONG"}, {"ticker": "AA PL"}, {"ticker": ""}, {"ticker": 123},
    {"form_types": ["S-1"]}, {"form_types": "10-K"}, {"form_types": []}, {"form_types": ["10-K*"]},
    {"start_date": date(2024, 2, 1), "end_date": date(2024, 1, 1)},
    {"start_date": datetime(2024, 1, 1)}, {"start_date": "2024-01-01"},
    {"start_date": date(2027, 1, 1)}, {"start_date": date(1990, 1, 1)},
])
def test_invalid_inputs_fail_before_provider(kwargs: dict[str, Any]) -> None:
    provider = FakeProvider()
    ticker = kwargs.pop("ticker", "AAPL")
    with pytest.raises(SecFilingInputError):
        lookup_sec_filings(ticker, environ=ENV, provider=provider, clock=clock, **kwargs)
    assert provider.calls == []


def test_share_class_ticker_is_normalized_and_alias_warned() -> None:
    result = run(FakeProvider(SecProviderPayload("1067983", "BRK-A", GOOD_BRK)), ticker="brk.b")
    assert result.identity.sec_ticker == "BRK-B" and result.identity.cik == "0001067983"
    assert any("primary ticker BRK-A" in w for w in result.warnings)


GOOD_BRK = (
    {**record("0000950170-24-000001", "10-K", "2024-02-26"),
     "report_url": "https://www.sec.gov/Archives/edgar/data/1067983/000095017024000001/doc.htm",
     "filing_detail_url": "https://www.sec.gov/Archives/edgar/data/1067983/0000950170-24-000001-index.htm"},
)


def bad(**changes: Any) -> tuple[dict[str, Any], ...]:
    return ({**GOOD[0], **changes},) + GOOD[1:]


@pytest.mark.parametrize(("payload", "message"), [
    (SecProviderPayload(None, "AAPL", GOOD), "CIK"),
    (SecProviderPayload("32O193", "AAPL", GOOD), "CIK"),
    (SecProviderPayload("00000000000", "AAPL", GOOD), "CIK"),
    (SecProviderPayload(CIK, "Error: CIK, 320193, does not have a unique ticker.", GOOD), "fund/ETF"),
    (SecProviderPayload(CIK, None, GOOD), "reverse"),
    (SecProviderPayload(CIK, "AAPL", (GOOD[0], dict(GOOD[0]))), "Duplicate accession"),
    (SecProviderPayload(CIK, "AAPL", bad(accession_number="0000320193-24-12")), "accession"),
    (SecProviderPayload(CIK, "AAPL", bad(accession_number=None)), "accession"),
    (SecProviderPayload(CIK, "AAPL", bad(report_type=None)), "form type is missing"),
    (SecProviderPayload(CIK, "AAPL", bad(report_type="nan")), "form type is missing"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_date=None)), "filing_date is missing"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_date="2024-13-40")), "malformed"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_date=date(2027, 1, 1))), "outside"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_date=date(1980, 1, 1))), "outside"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_date=datetime(2024, 1, 1))), "datetime"),
    (SecProviderPayload(CIK, "AAPL", bad(report_date=date(2027, 1, 1))), "future"),
    (SecProviderPayload(CIK, "AAPL", bad(accepted_date="yesterday")), "acceptance"),
    (SecProviderPayload(CIK, "AAPL", bad(accepted_date="2024-11-01T06:01:36")), "timezone"),
    (SecProviderPayload(CIK, "AAPL", bad(filing_detail_url=f"{BASE}other-index.htm")), "index URL"),
    (SecProviderPayload(CIK, "AAPL", bad(report_url="https://evil.example/doc.htm")), "primary document URL"),
    (SecProviderPayload(CIK, "AAPL", ("not a mapping",)), "mapping"),  # type: ignore[arg-type]
])
def test_malformed_provider_data_fails_closed(payload: SecProviderPayload, message: str) -> None:
    with pytest.raises(SecFilingDataError, match=message):
        run(FakeProvider(payload))


def test_no_provider_records_is_not_found() -> None:
    with pytest.raises(SecFilingNotFoundError):
        run(FakeProvider(SecProviderPayload(CIK, "AAPL", ())))


def test_provider_errors_propagate_without_fallback() -> None:
    with pytest.raises(SecFilingProviderError, match="boom"):
        run(FakeProvider(error=SecFilingProviderError("boom")))


def test_missing_primary_document_has_no_url() -> None:
    result = run(FakeProvider(SecProviderPayload(CIK, "AAPL", bad(primary_doc="None"))))
    assert result.filings[0].primary_document is None and result.filings[0].primary_document_url is None


def test_allowlist_is_conservative() -> None:
    assert {"10-K", "10-Q", "8-K"} <= ALLOWED_FORM_TYPES
    assert not {"4", "S-1", "13F-HR", "SC 13G"} & ALLOWED_FORM_TYPES


def test_import_does_not_load_openbb_or_network() -> None:
    code = (
        "import sys, socket\n"
        "def b(*a, **k): raise SystemExit('network')\n"
        "socket.socket.connect = b; socket.getaddrinfo = b\n"
        "import edge.data, edge.data.sec_filings, edge.data.openbb_sec_provider\n"
        "bad = [m for m in sys.modules if m.startswith(('openbb', 'aiohttp'))]\n"
        "assert not bad, bad\n"
    )
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, completed.stderr


# ------------------------------------------- OpenBB adapter (mocked I/O) ---

requires_openbb = pytest.mark.skipif(
    importlib.util.find_spec("openbb_sec") is None, reason="optional extra: pip install -e .[sec]"
)

COMPANY_TICKERS = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1067983, "ticker": "BRK-A", "title": "Berkshire Hathaway Inc"},
    "2": {"cik_str": 1067983, "ticker": "BRK-B", "title": "Berkshire Hathaway Inc"},
}
MF_TICKERS = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[36405, "S000002277", "C000005930", "VFIAX"]]}


def submissions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cols = ["accessionNumber", "filingDate", "reportDate", "acceptanceDateTime", "act", "form", "fileNumber",
            "filmNumber", "items", "size", "isXBRL", "isInlineXBRL", "primaryDocument", "primaryDocDescription"]
    return {"cik": "320193", "name": "Apple Inc.", "tickers": ["AAPL"],
            "filings": {"recent": {c: [r.get(c, "") for r in rows] for c in cols}, "files": []}}


SUBMISSION_ROWS = [
    {"accessionNumber": "0000320193-24-000123", "filingDate": "2024-11-01", "reportDate": "2024-09-28",
     "acceptanceDateTime": "2024-11-01T06:01:36.000Z", "form": "10-K", "primaryDocument": "aapl-20240928.htm"},
    {"accessionNumber": "0000320193-24-000120", "filingDate": "2024-10-31", "reportDate": "",
     "acceptanceDateTime": "2024-10-31T20:30:00.000Z", "form": "8-K", "primaryDocument": "a8k.htm"},
    {"accessionNumber": "0001140361-24-000001", "filingDate": "2024-10-15", "reportDate": "2024-10-11",
     "acceptanceDateTime": "2024-10-15T21:00:00.000Z", "form": "4", "primaryDocument": "form4.xml"},
]


class FakeSec:
    """Stands in for openbb_sec.utils.ratelimit.sec_amake_request."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def __call__(self, url: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((url, dict(kwargs.get("headers") or {})))
        value = self.routes.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected URL {url}")
        return value


@pytest.fixture
def fake_sec(monkeypatch: pytest.MonkeyPatch):
    from openbb_sec.utils import ratelimit

    def install(routes: dict[str, Any]) -> FakeSec:
        fake = FakeSec(routes)
        monkeypatch.setattr(ratelimit, "sec_amake_request", fake)
        return fake
    return install


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
MF_URL = "https://www.sec.gov/files/company_tickers_mf.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK0000320193.json"


def openbb_lookup(ticker: str = "AAPL", **kwargs: Any):
    from edge.data.openbb_sec_provider import OpenBBSecFilingsProvider

    return lookup_sec_filings(ticker, environ=ENV, provider=OpenBBSecFilingsProvider(), clock=clock, **kwargs)


@requires_openbb
def test_openbb_adapter_end_to_end_with_user_agent_and_no_cache(fake_sec, tmp_path) -> None:
    from openbb_sec.utils import definitions

    original = (dict(definitions.HEADERS), dict(definitions.SEC_HEADERS))
    fake = fake_sec({TICKERS_URL: COMPANY_TICKERS, SUBMISSIONS_URL: submissions(SUBMISSION_ROWS)})
    result = openbb_lookup()

    assert [url for url, _ in fake.calls] == [TICKERS_URL, TICKERS_URL, SUBMISSIONS_URL]
    assert all(headers["User-Agent"] == UA for _, headers in fake.calls)
    assert (dict(definitions.HEADERS), dict(definitions.SEC_HEADERS)) == original  # restored
    assert not (tmp_path / "openbb-sec-cache").exists()  # use_cache=False: no disk cache
    assert result.identity.cik == CIK and result.identity.sec_primary_ticker == "AAPL"
    assert [(f.form_type, f.accession_number) for f in result.filings] == [
        ("10-K", "0000320193-24-000123"), ("8-K", "0000320193-24-000120")]
    assert result.filings[0].primary_document_url == f"{BASE}000032019324000123/aapl-20240928.htm"
    assert result.filings[1].report_date is None
    assert result.provenance.provider == "openbb-sec" and result.provenance.provider_version == "2.0.0"
    assert UA not in repr(result)


@requires_openbb
def test_openbb_unknown_ticker_is_not_found(fake_sec) -> None:
    fake_sec({TICKERS_URL: COMPANY_TICKERS, MF_URL: MF_TICKERS})
    with pytest.raises(SecFilingNotFoundError, match="not found"):
        openbb_lookup("ZZZZ")


@requires_openbb
def test_openbb_fund_fallback_identity_is_refused(fake_sec) -> None:
    fake = fake_sec({TICKERS_URL: COMPANY_TICKERS, MF_URL: MF_TICKERS})
    with pytest.raises(SecFilingDataError, match="fund/ETF"):
        openbb_lookup("VFIAX")
    assert all("submissions" not in url for url, _ in fake.calls)  # stopped before fetching filings


@requires_openbb
def test_openbb_provider_warnings_are_surfaced_and_redacted(fake_sec) -> None:
    import warnings

    fake = fake_sec({TICKERS_URL: COMPANY_TICKERS, SUBMISSIONS_URL: submissions(SUBMISSION_ROWS)})
    original_call = FakeSec.__call__

    async def warn_then_call(self: FakeSec, url: str, *args: Any, **kwargs: Any) -> Any:
        if url == SUBMISSIONS_URL:
            warnings.warn(f"partial data for {UA}", UserWarning, stacklevel=1)
        return await original_call(self, url, *args, **kwargs)

    fake.__class__ = type("WarningFakeSec", (FakeSec,), {"__call__": warn_then_call})
    result = openbb_lookup()
    assert any(w.startswith("provider warning (UserWarning): partial data for [redacted]") for w in result.warnings)
    assert UA not in repr(result)


@requires_openbb
def test_openbb_network_error_is_explicit_and_redacted(fake_sec) -> None:
    from openbb_sec.utils import definitions

    fake_sec({TICKERS_URL: ConnectionError(f"refused for {UA}")})
    with pytest.raises(SecFilingProviderError) as info:
        openbb_lookup()
    assert "ConnectionError" in str(info.value) and UA not in str(info.value)
    assert info.value.__cause__ is None
    assert definitions.HEADERS["User-Agent"] != UA  # restored after failure


@requires_openbb
@pytest.mark.parametrize("payload", [
    {"unexpected": "shape"},
    submissions([{**SUBMISSION_ROWS[0], "filingDate": "not-a-date"}]),
    submissions([{**SUBMISSION_ROWS[0], "filingDate": ""}]),
])
def test_openbb_malformed_submissions_fail_closed(fake_sec, payload: dict[str, Any]) -> None:
    fake_sec({TICKERS_URL: COMPANY_TICKERS, SUBMISSIONS_URL: payload})
    with pytest.raises((SecFilingProviderError, SecFilingDataError)):
        openbb_lookup()


@requires_openbb
def test_openbb_duplicate_accessions_fail_closed(fake_sec) -> None:
    fake_sec({TICKERS_URL: COMPANY_TICKERS, SUBMISSIONS_URL: submissions([SUBMISSION_ROWS[0], SUBMISSION_ROWS[0]])})
    with pytest.raises(SecFilingDataError, match="Duplicate"):
        openbb_lookup()


@requires_openbb
def test_openbb_version_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from edge.data import openbb_sec_provider

    monkeypatch.setitem(openbb_sec_provider.TESTED_VERSIONS, "openbb-sec", frozenset({"9.9.9"}))
    with pytest.raises(SecFilingConfigurationError, match="untested"):
        openbb_lookup()


@requires_openbb
def test_openbb_header_layout_guard(monkeypatch: pytest.MonkeyPatch, fake_sec) -> None:
    from openbb_sec.models import company_filings

    fake = fake_sec({})
    monkeypatch.setattr(company_filings, "HEADERS", {"User-Agent": "placeholder"})
    with pytest.raises(SecFilingConfigurationError, match="placeholder User-Agent"):
        openbb_lookup()
    assert fake.calls == []