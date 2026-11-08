"""Strict, bounded CSV import for explicitly user-authored observations.

This module parses data already supplied in memory. It does not open paths,
persist records, fetch provider data, fill gaps, sort rows, infer identity, or
certify freshness/rights. Vendor-origin records are rejected because EDGE has
no independently accepted vendor-retention evidence contract.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from edge.decision_contracts import (
    FxRate,
    ManualProvenance,
    PriceQuote,
    normalize_utc,
)
from edge.portfolio import Instrument

MAX_CSV_BYTES = 1_048_576
MAX_ROWS = 5_000
MAX_FIELD_CHARS = 256
PRICE_CSV_HEADERS = (
    "symbol",
    "market",
    "currency",
    "exchange",
    "session_date",
    "as_of",
    "declared_at",
    "source",
    "source_origin",
    "price_unit",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "volume_unit",
)
FX_CSV_HEADERS = (
    "from_currency",
    "to_currency",
    "rate",
    "as_of",
    "declared_at",
    "source",
    "source_origin",
    "purpose",
)
FUNDAMENTAL_CSV_HEADERS = (
    "symbol",
    "market",
    "currency",
    "exchange",
    "fact",
    "value",
    "unit",
    "period_start",
    "period_end",
    "value_status",
    "as_of",
    "declared_at",
    "source",
    "source_origin",
)
_PRICE_UNITS = frozenset({"USD", "GBP", "GBp"})
_FUNDAMENTAL_STATUSES = frozenset(
    {"reported", "estimated", "derived", "unspecified"}
)
_TEXT_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
_DECIMAL_RE = re.compile(r"^-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)$")
_INTEGER_RE = re.compile(r"^(?:0|[1-9][0-9]*)$")
_UNIT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9%/._ -]{0,31}$")


class ManualObservationError(ValueError):
    """Sanitized import failure with a stable diagnostic code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ManualSchemaError(ManualObservationError):
    """CSV structure or field value is invalid."""


class ManualDeclarationError(ManualObservationError):
    """Explicit user-authorship / retention declaration is missing."""


class ExternalVendorRetentionError(ManualObservationError):
    """External vendor retention lacks independently accepted rights evidence."""


@dataclass(frozen=True)
class ManualDataDeclaration:
    """User attestation for a manual import; never a provider-rights grant.

    Vendor-origin rows are intentionally rejected even if the boolean
    declarations are true: those booleans are not independent license evidence.
    """

    source_origin: Literal["user-authored", "external-vendor", "unknown"]
    user_authored: bool
    local_retention_declared: bool


@dataclass(frozen=True)
class ManualPriceObservation:
    instrument: Instrument
    exchange: str
    session_date: date
    open: PriceQuote
    high: PriceQuote
    low: PriceQuote
    close: PriceQuote
    volume: int | None
    volume_unit: Literal["shares"]
    field_provenance: tuple[tuple[str, ManualProvenance], ...]


@dataclass(frozen=True)
class ManualFXObservation:
    rate: FxRate
    purpose: Literal["cash"]
    field_provenance: tuple[tuple[str, ManualProvenance], ...]


@dataclass(frozen=True)
class ManualFundamentalObservation:
    instrument: Instrument
    exchange: str
    fact: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date | None
    value_status: Literal["reported", "estimated", "derived", "unspecified"]
    as_of: datetime
    provenance: ManualProvenance
    field_provenance: tuple[tuple[str, ManualProvenance], ...]


def parse_price_csv(
    source: str | bytes,
    *,
    instrument: Instrument,
    exchange: str,
    declaration: ManualDataDeclaration,
) -> tuple[ManualPriceObservation, ...]:
    """Parse ordered daily OHLCV rows for exactly one declared instrument.

    Price unit stays explicitly ``USD``, ``GBP`` or ``GBp``. GBp is not scaled
    here; consumers may call ``PriceQuote.per_share_money`` exactly once.
    ``as_of`` is a user-declared observation timestamp, not verified market
    close, publication time, freshness, or exchange-session coverage.
    """
    _require_manual_declaration(declaration)
    if not isinstance(instrument, Instrument):
        raise ManualSchemaError("instrument_required", "A declared instrument is required.")
    _require_text(exchange, "exchange")
    rows = _csv_rows(source, PRICE_CSV_HEADERS)
    observations: list[ManualPriceObservation] = []
    seen_dates: set[date] = set()
    previous_date: date | None = None
    file_unit: str | None = None

    for row in rows:
        _require_row_origin(row)
        if (
            row["symbol"] != instrument.symbol
            or row["market"] != instrument.market
            or row["currency"] != instrument.currency
            or row["exchange"] != exchange
        ):
            raise ManualSchemaError(
                "identity_mismatch",
                "Every row must match the explicitly declared symbol, market, currency and exchange.",
            )
        session_date = _parse_date(row["session_date"], "session_date")
        if session_date in seen_dates:
            raise ManualSchemaError("duplicate_record", "Duplicate price session date.")
        if previous_date is not None and session_date <= previous_date:
            raise ManualSchemaError(
                "record_order", "Price rows must be strictly ordered by session date."
            )
        seen_dates.add(session_date)
        previous_date = session_date

        unit = row["price_unit"]
        _validate_price_unit(instrument, unit)
        if file_unit is None:
            file_unit = unit
        elif file_unit != unit:
            raise ManualSchemaError(
                "unit_mix", "A price import cannot mix price units."
            )
        as_of = _parse_aware_datetime(row["as_of"], "as_of")
        provenance = ManualProvenance(
            _require_text(row["source"], "source"),
            _parse_aware_datetime(row["declared_at"], "declared_at"),
        )
        prices = {
            field: _parse_decimal(row[field], field, positive=True)
            for field in ("open", "high", "low", "close")
        }
        if (
            prices["low"] > prices["high"]
            or not prices["low"] <= prices["open"] <= prices["high"]
            or not prices["low"] <= prices["close"] <= prices["high"]
        ):
            raise ManualSchemaError(
                "ohlc_inconsistent", "OHLC prices are internally inconsistent."
            )
        volume_text = row["volume"]
        volume = None if volume_text == "" else _parse_integer(volume_text, "volume")
        if row["volume_unit"] != "shares":
            raise ManualSchemaError(
                "volume_unit", "Volume unit must be explicitly declared as shares."
            )
        fields = _field_provenance(PRICE_CSV_HEADERS, provenance)
        quotes = {
            field: PriceQuote(prices[field], unit, as_of, provenance)
            for field in ("open", "high", "low", "close")
        }
        observations.append(
            ManualPriceObservation(
                instrument=instrument,
                exchange=exchange,
                session_date=session_date,
                open=quotes["open"],
                high=quotes["high"],
                low=quotes["low"],
                close=quotes["close"],
                volume=volume,
                volume_unit="shares",
                field_provenance=fields,
            )
        )
    return tuple(observations)


def parse_fx_csv(
    source: str | bytes,
    *,
    declaration: ManualDataDeclaration,
) -> tuple[ManualFXObservation, ...]:
    """Parse ordered, explicitly directed FX observations; never invert rates."""
    _require_manual_declaration(declaration)
    rows = _csv_rows(source, FX_CSV_HEADERS)
    observations: list[ManualFXObservation] = []
    previous_as_of: datetime | None = None

    for row in rows:
        _require_row_origin(row)
        from_currency, to_currency = row["from_currency"], row["to_currency"]
        if (
            from_currency not in {"USD", "GBP"}
            or to_currency not in {"USD", "GBP"}
            or from_currency == to_currency
        ):
            raise ManualSchemaError(
                "fx_pair", "FX currencies must be an explicit USD/GBP pair."
            )
        if row["purpose"] != "cash":
            raise ManualSchemaError(
                "fx_purpose", "FX purpose must be explicitly declared as cash."
            )
        as_of = _parse_aware_datetime(row["as_of"], "as_of")
        if previous_as_of is not None and as_of <= previous_as_of:
            raise ManualSchemaError(
                "record_order", "FX rows must be strictly ordered by observation time."
            )
        previous_as_of = as_of
        provenance = ManualProvenance(
            _require_text(row["source"], "source"),
            _parse_aware_datetime(row["declared_at"], "declared_at"),
        )
        rate = FxRate(
            from_currency=from_currency,
            to_currency=to_currency,
            rate=_parse_decimal(row["rate"], "rate", positive=True),
            as_of=as_of,
            provenance=provenance,
        )
        observations.append(
            ManualFXObservation(
                rate=rate,
                purpose="cash",
                field_provenance=_field_provenance(FX_CSV_HEADERS, provenance),
            )
        )
    return tuple(observations)


def parse_fundamental_csv(
    source: str | bytes,
    *,
    instrument: Instrument,
    exchange: str,
    declaration: ManualDataDeclaration,
) -> tuple[ManualFundamentalObservation, ...]:
    """Parse manually declared numeric facts without deriving or converting them."""
    _require_manual_declaration(declaration)
    if not isinstance(instrument, Instrument):
        raise ManualSchemaError("instrument_required", "A declared instrument is required.")
    _require_text(exchange, "exchange")
    rows = _csv_rows(source, FUNDAMENTAL_CSV_HEADERS)
    observations: list[ManualFundamentalObservation] = []
    seen: set[tuple[str, date | None, date | None, datetime]] = set()

    for row in rows:
        _require_row_origin(row)
        if (
            row["symbol"] != instrument.symbol
            or row["market"] != instrument.market
            or row["currency"] != instrument.currency
            or row["exchange"] != exchange
        ):
            raise ManualSchemaError(
                "identity_mismatch",
                "Every fact must match the explicitly declared symbol, market, currency and exchange.",
            )
        fact = _require_text(row["fact"], "fact")
        unit = _require_unit(row["unit"])
        value_status = row["value_status"]
        if value_status not in _FUNDAMENTAL_STATUSES:
            raise ManualSchemaError(
                "value_status",
                "Fundamental value status must be reported, estimated, derived or unspecified.",
            )
        period_start = _optional_date(row["period_start"], "period_start")
        period_end = _optional_date(row["period_end"], "period_end")
        if (period_start is None) != (period_end is None):
            raise ManualSchemaError(
                "period_bounds", "Period start and end must both be present or both absent."
            )
        if period_start is not None and period_end is not None and period_start > period_end:
            raise ManualSchemaError(
                "period_order", "Fundamental period start must not follow its end."
            )
        as_of = _parse_aware_datetime(row["as_of"], "as_of")
        key = (fact, period_start, period_end, as_of)
        if key in seen:
            raise ManualSchemaError(
                "duplicate_record", "Duplicate fundamental fact and reporting period."
            )
        seen.add(key)
        provenance = ManualProvenance(
            _require_text(row["source"], "source"),
            _parse_aware_datetime(row["declared_at"], "declared_at"),
        )
        observations.append(
            ManualFundamentalObservation(
                instrument=instrument,
                exchange=exchange,
                fact=fact,
                value=_parse_decimal(row["value"], "value"),
                unit=unit,
                period_start=period_start,
                period_end=period_end,
                value_status=value_status,  # type: ignore[arg-type]
                as_of=as_of,
                provenance=provenance,
                field_provenance=_field_provenance(FUNDAMENTAL_CSV_HEADERS, provenance),
            )
        )
    return tuple(observations)


def _require_manual_declaration(value: object) -> None:
    if not isinstance(value, ManualDataDeclaration):
        raise ManualDeclarationError(
            "declaration_required", "An explicit manual-data declaration is required."
        )
    if value.source_origin == "external-vendor":
        raise ExternalVendorRetentionError(
            "vendor_rights_unverified",
            "External vendor records cannot be retained without independently accepted rights evidence.",
        )
    if value.source_origin != "user-authored":
        raise ManualDeclarationError(
            "origin_unverified", "Only explicitly user-authored observations are accepted."
        )
    if value.user_authored is not True or value.local_retention_declared is not True:
        raise ManualDeclarationError(
            "declaration_incomplete",
            "Declare user authorship and local retention before importing.",
        )


def _require_row_origin(row: dict[str, str]) -> None:
    if row["source_origin"] == "external-vendor":
        raise ExternalVendorRetentionError(
            "vendor_rights_unverified",
            "External vendor records cannot be retained without independently accepted rights evidence.",
        )
    if row["source_origin"] != "user-authored":
        raise ManualDeclarationError(
            "origin_unverified", "Every row must explicitly declare user-authored origin."
        )


def _csv_rows(source: str | bytes, headers: tuple[str, ...]) -> list[dict[str, str]]:
    if isinstance(source, bytes):
        if len(source) > MAX_CSV_BYTES:
            raise ManualSchemaError("size_limit", "CSV input exceeds the byte limit.")
        try:
            text = source.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ManualSchemaError("encoding", "CSV input must be UTF-8.") from None
    elif isinstance(source, str):
        try:
            encoded = source.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            raise ManualSchemaError("encoding", "CSV input must be valid UTF-8.") from None
        if len(encoded) > MAX_CSV_BYTES:
            raise ManualSchemaError("size_limit", "CSV input exceeds the byte limit.")
        text = source.removeprefix("\ufeff")
    else:
        raise ManualSchemaError("input_type", "CSV input must be text or bytes.")
    if not text:
        raise ManualSchemaError("empty_input", "CSV input is empty.")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        actual_headers = next(reader)
        if (
            len(actual_headers) != len(set(actual_headers))
            or len(actual_headers) != len(headers)
            or set(actual_headers) != set(headers)
        ):
            raise ManualSchemaError(
                "headers", "CSV headers do not match the required schema."
            )
        result: list[dict[str, str]] = []
        for row in reader:
            if not row or all(field == "" for field in row):
                raise ManualSchemaError("blank_row", "Blank CSV rows are not allowed.")
            if len(result) >= MAX_ROWS:
                raise ManualSchemaError("row_limit", "CSV input exceeds the row limit.")
            if len(row) != len(actual_headers):
                raise ManualSchemaError("column_count", "CSV row has the wrong number of columns.")
            if any(len(field) > MAX_FIELD_CHARS for field in row):
                raise ManualSchemaError("field_limit", "CSV field exceeds the character limit.")
            result.append(dict(zip(actual_headers, row, strict=True)))
    except StopIteration:
        raise ManualSchemaError("headers", "CSV header row is missing.") from None
    except csv.Error:
        raise ManualSchemaError("csv_syntax", "CSV syntax is invalid.") from None
    if not result:
        raise ManualSchemaError("no_records", "CSV contains no observation rows.")
    return result


def _parse_decimal(value: str, field: str, *, positive: bool = False) -> Decimal:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or _DECIMAL_RE.fullmatch(value) is None
    ):
        raise ManualSchemaError("decimal_invalid", f"{field} must be an exact decimal string.")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ManualSchemaError("decimal_invalid", f"{field} must be an exact decimal string.") from None
    if not parsed.is_finite() or (positive and parsed <= 0):
        raise ManualSchemaError(
            "decimal_range",
            f"{field} must be a finite {'positive ' if positive else ''}decimal.",
        )
    return parsed


def _parse_integer(value: str, field: str) -> int:
    if not isinstance(value, str) or _INTEGER_RE.fullmatch(value) is None:
        raise ManualSchemaError(
            "integer_invalid", f"{field} must be an exact nonnegative integer."
        )
    if len(value) > 19:
        raise ManualSchemaError("integer_range", f"{field} exceeds the supported range.")
    parsed = int(value)
    if parsed > 9_223_372_036_854_775_807:
        raise ManualSchemaError("integer_range", f"{field} exceeds the supported range.")
    return parsed


def _parse_aware_datetime(value: str, field: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise ManualSchemaError(
            "timestamp_invalid", f"{field} must be an ISO timezone-aware timestamp."
        )
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return normalize_utc(parsed)
    except (ValueError, OverflowError):
        raise ManualSchemaError(
            "timestamp_invalid", f"{field} must be an ISO timezone-aware timestamp."
        ) from None


def _parse_date(value: str, field: str) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ManualSchemaError("date_invalid", f"{field} must be YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ManualSchemaError("date_invalid", f"{field} must be YYYY-MM-DD.") from None
    if parsed.isoformat() != value:
        raise ManualSchemaError("date_invalid", f"{field} must be YYYY-MM-DD.")
    return parsed


def _optional_date(value: str, field: str) -> date | None:
    return None if value == "" else _parse_date(value, field)


def _require_text(value: str, field: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > 200
        or _TEXT_RE.fullmatch(value) is None
        or any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value)
    ):
        raise ManualSchemaError("text_invalid", f"{field} must be bounded plain text.")
    return value


def _require_unit(value: str) -> str:
    if not isinstance(value, str) or _UNIT_RE.fullmatch(value) is None:
        raise ManualSchemaError("unit_invalid", "Fundamental unit must be explicit and bounded.")
    return value


def _validate_price_unit(instrument: Instrument, unit: str) -> None:
    if unit not in _PRICE_UNITS:
        raise ManualSchemaError("price_unit", "Price unit must be USD, GBP or GBp.")
    valid = (
        (instrument.market, instrument.currency, unit) in
        {("US", "USD", "USD"), ("UK", "GBP", "GBP"), ("UK", "GBP", "GBp")}
    )
    if not valid:
        raise ManualSchemaError(
            "currency_unit_mismatch",
            "Price unit must match the explicitly declared US/USD or UK/GBP instrument.",
        )


def _field_provenance(
    fields: tuple[str, ...], provenance: ManualProvenance
) -> tuple[tuple[str, ManualProvenance], ...]:
    return tuple((field, provenance) for field in fields)
