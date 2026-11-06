"""Opt-in SEC filing *metadata* lookup with fail-closed validation.

Scope (deliberately narrow):

* Resolves a US ticker to an SEC CIK and returns filing metadata (form type,
  accession number, filing/report/acceptance dates, SEC archive URLs).
* It is NOT price data, NOT financial-statement data and must never be used as
  input to the Nautilus simulation engine.
* Nothing here performs network access at import time. Requests are made only
  when :func:`lookup_sec_filings` is called explicitly AND the user has
  configured an SEC-compliant User-Agent in the ``EDGE_SEC_USER_AGENT``
  environment variable. EDGE never invents a contact and never returns, logs or
  persists the configured User-Agent.
* No cache is read or written; every lookup reflects the provider response at
  retrieval time, or fails with an explicit error.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol

from edge.errors import EdgeError

USER_AGENT_ENV_VAR = "EDGE_SEC_USER_AGENT"

#: Form types this adapter is permitted to return. Exact matches only.
ALLOWED_FORM_TYPES: frozenset[str] = frozenset({
    "10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A",
    "20-F", "20-F/A", "40-F", "40-F/A", "6-K", "6-K/A", "DEF 14A",
})
DEFAULT_FORM_TYPES: tuple[str, ...] = ("10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A")

EDGAR_EPOCH = date(1993, 1, 1)
USAGE_SCOPE = "research_metadata_only; not prices, not financial statements, not simulation input"

_TICKER_RE = re.compile(r"^[A-Z]{1,5}(?:[.-][A-Z]{1,2})?$")
_CIK_RE = re.compile(r"^\d{1,10}$")
_ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
_PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net", "fakecompany.com",
    "domain.com", "email.com", "company.com", "test.com", "localhost.localdomain",
})
_MISSING_TEXT = frozenset({"", "none", "nan", "nat", "null"})
_MAX_MESSAGE = 300


# ---------------------------------------------------------------- errors ---

class SecFilingError(EdgeError):
    """Base error for the SEC filing metadata adapter."""


class SecFilingConfigurationError(SecFilingError):
    """Lookup refused: missing/invalid User-Agent or incompatible provider install."""


class SecFilingInputError(SecFilingError):
    """Caller-supplied ticker, form types or dates are invalid."""


class SecFilingProviderError(SecFilingError):
    """The provider call failed (network, HTTP, provider exception or timeout)."""


class SecFilingNotFoundError(SecFilingProviderError):
    """The provider reported no CIK for the ticker or no filings for the CIK."""


class SecFilingDataError(SecFilingError):
    """Provider returned missing, duplicate, malformed or inconsistent data."""


# ---------------------------------------------------------------- models ---

@dataclass(frozen=True)
class SecCompanyIdentity:
    requested_ticker: str
    sec_ticker: str
    """Ticker in SEC share-class notation (``BRK.B`` -> ``BRK-B``)."""
    cik: str
    """Ten-digit, zero-padded Central Index Key."""
    sec_primary_ticker: str
    """Ticker that SEC's company ticker map returns for this CIK."""


@dataclass(frozen=True)
class SecFilingRecord:
    cik: str
    accession_number: str
    form_type: str
    filing_date: date
    report_date: date | None
    accepted_at: datetime | None
    """SEC acceptance timestamp in UTC, when supplied by the provider."""
    primary_document: str | None
    primary_document_url: str | None
    filing_index_url: str


@dataclass(frozen=True)
class SecFilingProvenance:
    source: str
    provider: str
    provider_version: str
    framework_version: str
    endpoints: tuple[str, ...]
    retrieved_at_start: datetime
    retrieved_at_end: datetime
    timeliness: str
    cache_used: bool
    user_agent_configured: bool
    usage_scope: str


@dataclass(frozen=True)
class SecFilingLookupResult:
    identity: SecCompanyIdentity
    filings: tuple[SecFilingRecord, ...]
    requested_form_types: tuple[str, ...]
    start_date: date | None
    end_date: date | None
    provider_record_count: int
    excluded_record_count: int
    """Provider records outside the requested forms/date window."""
    coverage_start: date
    """Earliest filing date present in the provider response."""
    coverage_end: date
    coverage_may_be_incomplete: bool
    """True when the requested window begins before ``coverage_start``."""
    warnings: tuple[str, ...]
    provenance: SecFilingProvenance


# ------------------------------------------------------ provider contract ---

@dataclass(frozen=True)
class SecProviderInfo:
    name: str
    version: str
    framework_version: str
    endpoints: tuple[str, ...]


@dataclass(frozen=True)
class SecProviderPayload:
    """Raw provider output, validated by this module before use."""

    resolved_cik: object
    reverse_ticker: object
    records: tuple[Mapping[str, Any], ...]
    warnings: tuple[str, ...] = ()


class SecFilingsProvider(Protocol):
    """Replaceable provider interface. Implementations must not cache."""

    def describe(self) -> SecProviderInfo: ...

    def lookup(self, sec_ticker: str, *, user_agent: str) -> SecProviderPayload: ...


# ------------------------------------------------------------ validation ---

def normalize_ticker(value: object) -> tuple[str, str]:
    """Return ``(requested_ticker, sec_ticker)`` or raise ``SecFilingInputError``."""
    if not isinstance(value, str):
        raise SecFilingInputError("Ticker must be a string.")
    ticker = value.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise SecFilingInputError(
            "Ticker must be a US symbol of 1-5 letters with an optional class suffix (e.g. BRK.B)."
        )
    return ticker, ticker.replace(".", "-")


def normalize_cik(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SecFilingDataError("Provider CIK is missing or not a string/integer.")
    text = str(value).strip()
    if not _CIK_RE.fullmatch(text) or int(text) == 0:
        raise SecFilingDataError("Provider CIK is not a 1-10 digit positive number.")
    return text.zfill(10)


def normalize_form_types(form_types: Iterable[str] | None) -> tuple[str, ...]:
    if form_types is None:
        return DEFAULT_FORM_TYPES
    if isinstance(form_types, str):
        raise SecFilingInputError("form_types must be a collection of form names, not a string.")
    forms: list[str] = []
    for form in form_types:
        if not isinstance(form, str) or form.strip().upper() not in ALLOWED_FORM_TYPES:
            raise SecFilingInputError(
                f"Form type is not allowlisted. Allowed: {', '.join(sorted(ALLOWED_FORM_TYPES))}."
            )
        normalized = form.strip().upper()
        if normalized not in forms:
            forms.append(normalized)
    if not forms:
        raise SecFilingInputError("At least one form type is required.")
    return tuple(forms)


def _validate_window(start: object, end: object, today: date) -> tuple[date | None, date | None]:
    for label, value in (("start_date", start), ("end_date", end)):
        if value is not None and (isinstance(value, datetime) or not isinstance(value, date)):
            raise SecFilingInputError(f"{label} must be a datetime.date (not datetime) or None.")
        if value is not None and value < EDGAR_EPOCH:
            raise SecFilingInputError(f"{label} predates EDGAR electronic filings ({EDGAR_EPOCH}).")
    if start is not None and start > today:
        raise SecFilingInputError("start_date is in the future.")
    if start is not None and end is not None and start > end:
        raise SecFilingInputError("start_date is after end_date.")
    return start, end  # type: ignore[return-value]


def validate_user_agent(environ: Mapping[str, str]) -> str:
    """Return the configured User-Agent or raise. Never echoes the value."""
    raw = environ.get(USER_AGENT_ENV_VAR)
    if raw is None or not raw.strip():
        raise SecFilingConfigurationError(
            f"{USER_AGENT_ENV_VAR} is not set. SEC requires a declared User-Agent such as "
            "'Your Name or Company your.email@your-domain'. EDGE will not invent one."
        )
    value = raw.strip()
    if len(value) > 200 or any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in value):
        raise SecFilingConfigurationError(
            f"{USER_AGENT_ENV_VAR} must be printable ASCII on one line, at most 200 characters."
        )
    match = _EMAIL_RE.search(value)
    if match is None:
        raise SecFilingConfigurationError(f"{USER_AGENT_ENV_VAR} must include a contact email address.")
    domain = match.group(1).lower()
    if domain in _PLACEHOLDER_DOMAINS or domain.endswith((".example", ".invalid", ".test")):
        raise SecFilingConfigurationError(f"{USER_AGENT_ENV_VAR} uses a placeholder email domain.")
    if not value.replace(match.group(0), "").strip():
        raise SecFilingConfigurationError(
            f"{USER_AGENT_ENV_VAR} must include a name or company in addition to the email."
        )
    return value


def redact(text: str, secret: str | None) -> str:
    """Remove the configured User-Agent (and its email) from a message."""
    out = text
    if secret:
        out = out.replace(secret, "[redacted]")
        match = _EMAIL_RE.search(secret)
        if match:
            out = out.replace(match.group(0), "[redacted]")
    out = " ".join(out.split())
    return out if len(out) <= _MAX_MESSAGE else out[: _MAX_MESSAGE - 3] + "..."


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _MISSING_TEXT else text


def _date_field(value: object, label: str, accession: str) -> date | None:
    if value is None or (isinstance(value, str) and value.strip().lower() in _MISSING_TEXT):
        return None
    if isinstance(value, datetime):
        raise SecFilingDataError(f"{accession}: {label} is a datetime, expected a date.")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise SecFilingDataError(f"{accession}: {label} is malformed.")


def _accepted_at(value: object, accession: str) -> datetime | None:
    text = _optional_text(value)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise SecFilingDataError(f"{accession}: acceptance timestamp is malformed.") from None
    if parsed.tzinfo is None:
        raise SecFilingDataError(f"{accession}: acceptance timestamp has no timezone.")
    return parsed.astimezone(timezone.utc)


def _record(raw: Mapping[str, Any], cik: str, today: date) -> SecFilingRecord:
    accession = _optional_text(raw.get("accession_number"))
    if accession is None or not _ACCESSION_RE.fullmatch(accession):
        raise SecFilingDataError("Filing has a missing or malformed accession number.")
    form = _optional_text(raw.get("report_type"))
    if form is None:
        raise SecFilingDataError(f"{accession}: form type is missing.")
    filing_date = _date_field(raw.get("filing_date"), "filing_date", accession)
    if filing_date is None:
        raise SecFilingDataError(f"{accession}: filing_date is missing.")
    if not EDGAR_EPOCH <= filing_date <= today:
        raise SecFilingDataError(f"{accession}: filing_date {filing_date} is outside {EDGAR_EPOCH}..{today}.")
    report_date = _date_field(raw.get("report_date"), "report_date", accession)
    if report_date is not None and report_date > today:
        raise SecFilingDataError(f"{accession}: report_date {report_date} is in the future.")
    accepted_at = _accepted_at(raw.get("accepted_date"), accession)
    if accepted_at is not None and accepted_at.date() > today:
        raise SecFilingDataError(f"{accession}: acceptance timestamp is in the future.")

    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
    index_url = _optional_text(raw.get("filing_detail_url"))
    if index_url != f"{base}{accession}-index.htm":
        raise SecFilingDataError(f"{accession}: filing index URL does not match CIK/accession.")
    primary_doc = _optional_text(raw.get("primary_doc"))
    primary_url = _optional_text(raw.get("report_url"))
    if primary_doc is None:
        primary_url = None  # provider builds '<base>/<acc>/None'; do not expose a broken link
    elif primary_url != f"{base}{accession.replace('-', '')}/{primary_doc}":
        raise SecFilingDataError(f"{accession}: primary document URL does not match CIK/accession.")
    return SecFilingRecord(
        cik=cik, accession_number=accession, form_type=form, filing_date=filing_date,
        report_date=report_date, accepted_at=accepted_at, primary_document=primary_doc,
        primary_document_url=primary_url, filing_index_url=index_url,
    )


# ---------------------------------------------------------------- service ---

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def lookup_sec_filings(
    ticker: str,
    *,
    form_types: Iterable[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    environ: Mapping[str, str] | None = None,
    provider: SecFilingsProvider | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> SecFilingLookupResult:
    """Explicitly fetch SEC filing metadata for ``ticker``. Fails closed.

    Inputs and configuration are validated before any provider is imported or
    contacted. When ``provider`` is omitted, the OpenBB ``openbb-sec`` adapter
    is used (requires ``pip install -e .[sec]``).
    """
    requested, sec_ticker = normalize_ticker(ticker)
    forms = normalize_form_types(form_types)
    started = clock()
    if started.tzinfo is None:
        raise SecFilingConfigurationError("Clock must return timezone-aware UTC datetimes.")
    today = started.astimezone(timezone.utc).date()
    start_date, end_date = _validate_window(start_date, end_date, today)
    user_agent = validate_user_agent(os.environ if environ is None else environ)

    if provider is None:
        from edge.data.openbb_sec_provider import OpenBBSecFilingsProvider

        provider = OpenBBSecFilingsProvider()
    info = provider.describe()
    started = clock()
    payload = provider.lookup(sec_ticker, user_agent=user_agent)
    finished = clock()
    if not isinstance(payload, SecProviderPayload):
        raise SecFilingDataError("Provider returned an unexpected payload type.")

    cik = normalize_cik(payload.resolved_cik)
    if not isinstance(payload.reverse_ticker, str):
        raise SecFilingDataError("Provider reverse CIK->ticker mapping is missing.")
    primary = payload.reverse_ticker.strip().upper()
    if primary.startswith("ERROR") or not _TICKER_RE.fullmatch(primary):
        raise SecFilingDataError(
            f"CIK {cik} is not in SEC's operating-company ticker map; refusing an "
            "unverified (possibly fund/ETF fallback) identity."
        )
    warnings = [redact(w, user_agent) for w in payload.warnings]
    if primary != sec_ticker:
        warnings.append(
            f"SEC maps CIK {cik} to primary ticker {primary}; requested {sec_ticker} "
            "(share class or alias of the same registrant)."
        )

    if not payload.records:
        raise SecFilingNotFoundError(f"Provider returned no filings for CIK {cik}.")
    seen: set[str] = set()
    all_records: list[SecFilingRecord] = []
    for raw in payload.records:
        if not isinstance(raw, Mapping):
            raise SecFilingDataError("Provider filing record is not a mapping.")
        record = _record(raw, cik, today)
        if record.accession_number in seen:
            raise SecFilingDataError(f"Duplicate accession number {record.accession_number} in provider data.")
        seen.add(record.accession_number)
        all_records.append(record)

    coverage_start = min(r.filing_date for r in all_records)
    coverage_end = max(r.filing_date for r in all_records)
    selected = [
        r for r in all_records
        if r.form_type in forms
        and (start_date is None or r.filing_date >= start_date)
        and (end_date is None or r.filing_date <= end_date)
    ]
    selected.sort(key=lambda r: (r.filing_date, r.accession_number), reverse=True)
    incomplete = start_date is None or start_date < coverage_start
    if incomplete:
        warnings.append(
            f"Provider response covers filings from {coverage_start} to {coverage_end} only "
            "(SEC 'recent' submissions block); earlier filings are not included."
        )
    if not selected:
        warnings.append("No filings of the requested form types in the requested window.")

    provenance = SecFilingProvenance(
        source="SEC EDGAR (www.sec.gov company_tickers.json; data.sec.gov submissions API)",
        provider=info.name, provider_version=info.version, framework_version=info.framework_version,
        endpoints=info.endpoints, retrieved_at_start=started.astimezone(timezone.utc),
        retrieved_at_end=finished.astimezone(timezone.utc),
        timeliness="As-of retrieval snapshot of EDGAR filing index; delayed metadata, not real-time.",
        cache_used=False, user_agent_configured=True, usage_scope=USAGE_SCOPE,
    )
    return SecFilingLookupResult(
        identity=SecCompanyIdentity(requested, sec_ticker, cik, primary),
        filings=tuple(selected), requested_form_types=forms,
        start_date=start_date, end_date=end_date,
        provider_record_count=len(all_records), excluded_record_count=len(all_records) - len(selected),
        coverage_start=coverage_start, coverage_end=coverage_end,
        coverage_may_be_incomplete=incomplete, warnings=tuple(warnings), provenance=provenance,
    )