from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from edge.data.manual_observations import (
    FUNDAMENTAL_CSV_HEADERS,
    FX_CSV_HEADERS,
    MAX_CSV_BYTES,
    PRICE_CSV_HEADERS,
    ExternalVendorRetentionError,
    ManualDataDeclaration,
    ManualObservationError,
    ManualPriceObservation,
    parse_fundamental_csv,
    parse_fx_csv,
    parse_price_csv,
)
from edge.decision_contracts import DecisionInputError, FinanceMoney
from edge.portfolio import Instrument

DECLARATION = ManualDataDeclaration(
    source_origin="user-authored",
    user_authored=True,
    local_retention_declared=True,
)
DECLARED_AT = "2026-11-07T14:00:00+00:00"

PRICE_HEADERS = PRICE_CSV_HEADERS
FX_HEADERS = FX_CSV_HEADERS
FUNDAMENTAL_HEADERS = FUNDAMENTAL_CSV_HEADERS


def csv_text(headers: tuple[str, ...], rows: list[dict[str, str]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def price_row(**changes: str) -> dict[str, str]:
    row = {
        "symbol": "VOD.L",
        "market": "UK",
        "currency": "GBP",
        "exchange": "LSE",
        "session_date": "2026-11-05",
        "as_of": "2026-11-05T16:30:00+00:00",
        "declared_at": DECLARED_AT,
        "source": "User-entered observation",
        "source_origin": "user-authored",
        "price_unit": "GBp",
        "open": "123.45",
        "high": "126.00",
        "low": "122.00",
        "close": "124.25",
        "volume": "1000",
        "volume_unit": "shares",
    }
    row.update(changes)
    return row


def fx_row(**changes: str) -> dict[str, str]:
    row = {
        "from_currency": "USD",
        "to_currency": "GBP",
        "rate": "0.80",
        "as_of": "2026-11-07T14:30:00Z",
        "declared_at": DECLARED_AT,
        "source": "User-entered FX observation",
        "source_origin": "user-authored",
        "purpose": "cash",
    }
    row.update(changes)
    return row


def fundamental_row(**changes: str) -> dict[str, str]:
    row = {
        "symbol": "VOD.L",
        "market": "UK",
        "currency": "GBP",
        "exchange": "LSE",
        "fact": "revenue",
        "value": "123456.78",
        "unit": "GBP",
        "period_start": "2025-04-01",
        "period_end": "2026-03-31",
        "value_status": "reported",
        "as_of": "2026-05-01T10:00:00+01:00",
        "declared_at": DECLARED_AT,
        "source": "User-entered filing note",
        "source_origin": "user-authored",
    }
    row.update(changes)
    return row


def test_price_import_preserves_exact_values_units_and_per_field_provenance():
    instrument = Instrument("VOD.L", "UK", "GBP")
    rows = [
        price_row(session_date="2026-11-05"),
        price_row(
            session_date="2026-11-06",
            as_of="2026-11-06T16:30:00Z",
            open="124.25",
            high="127.00",
            low="123.50",
            close="126.75",
        ),
    ]
    result = parse_price_csv(
        csv_text(PRICE_HEADERS, rows),
        instrument=instrument,
        exchange="LSE",
        declaration=DECLARATION,
    )
    assert len(result) == 2
    observation = result[0]
    assert isinstance(observation, ManualPriceObservation)
    assert observation.instrument == instrument
    assert observation.exchange == "LSE"
    assert observation.close.amount == Decimal("124.25")
    assert observation.close.unit == "GBp"
    assert observation.close.as_of == datetime(2026, 11, 5, 16, 30, tzinfo=UTC)
    assert observation.close.provenance.kind == "user-declared"
    assert observation.close.provenance.reference == "User-entered observation"
    assert dict(observation.field_provenance).keys() == set(PRICE_HEADERS)
    # Unit normalization is an explicit consumer operation, not an import rewrite.
    first_conversion = observation.close.per_share_money()
    second_conversion = observation.close.per_share_money()
    assert first_conversion == FinanceMoney(Decimal("1.2425"), "GBP")
    assert second_conversion == first_conversion
    assert observation.close.amount == Decimal("124.25")


def test_us_usd_and_uk_gbp_are_explicit_and_never_index_mixed():
    us = Instrument("MSFT", "US", "USD")
    row = price_row(
        symbol="MSFT",
        market="US",
        currency="USD",
        exchange="NASDAQ",
        price_unit="USD",
        open="10",
        high="11",
        low="9",
        close="10.5",
    )
    item = parse_price_csv(
        csv_text(PRICE_HEADERS, [row]),
        instrument=us,
        exchange="NASDAQ",
        declaration=DECLARATION,
    )[0]
    assert item.close.unit == "USD"
    mixed = price_row(session_date="2026-11-06", symbol="MSFT")
    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            csv_text(PRICE_HEADERS, [row, mixed]),
            instrument=us,
            exchange="NASDAQ",
            declaration=DECLARATION,
        )
    assert error.value.code == "identity_mismatch"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"price_unit": "GBP"}, "unit_mix"),
        ({"currency": "GBp"}, "identity_mismatch"),
        ({"market": "US"}, "identity_mismatch"),
        ({"exchange": "NYSE"}, "identity_mismatch"),
        ({"as_of": "2026-11-05T16:30:00"}, "timestamp_invalid"),
        ({"close": "NaN"}, "decimal_invalid"),
        ({"close": "1e2"}, "decimal_invalid"),
        ({"volume": "1.5"}, "integer_invalid"),
        ({"volume": " 1000"}, "integer_invalid"),
        ({"volume_unit": ""}, "volume_unit"),
        ({"high": "121"}, "ohlc_inconsistent"),
    ],
)
def test_price_import_fails_closed_for_bad_units_values_or_provenance(changes, code):
    rows = [price_row(), price_row(session_date="2026-11-06")]
    rows[1].update(changes)
    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            csv_text(PRICE_HEADERS, rows),
            instrument=Instrument("VOD.L", "UK", "GBP"),
            exchange="LSE",
            declaration=DECLARATION,
        )
    assert error.value.code == code
    assert "NaN" not in str(error.value)


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([price_row(), price_row()], "duplicate_record"),
        (
            [
                price_row(session_date="2026-11-06"),
                price_row(session_date="2026-11-05"),
            ],
            "record_order",
        ),
    ],
)
def test_price_import_rejects_duplicate_or_unsorted_sessions(rows, code):
    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            csv_text(PRICE_HEADERS, rows),
            instrument=Instrument("VOD.L", "UK", "GBP"),
            exchange="LSE",
            declaration=DECLARATION,
        )
    assert error.value.code == code


def test_missing_volume_is_preserved_as_none_and_never_filled():
    row = price_row(volume="")
    observation = parse_price_csv(
        csv_text(PRICE_HEADERS, [row]),
        instrument=Instrument("VOD.L", "UK", "GBP"),
        exchange="LSE",
        declaration=DECLARATION,
    )[0]
    assert observation.volume is None


def test_external_vendor_retention_is_rejected_even_with_user_declaration_flags():
    external = ManualDataDeclaration(
        source_origin="external-vendor",
        user_authored=True,
        local_retention_declared=True,
    )
    with pytest.raises(ExternalVendorRetentionError) as error:
        parse_price_csv(
            csv_text(PRICE_HEADERS, [price_row()]),
            instrument=Instrument("VOD.L", "UK", "GBP"),
            exchange="LSE",
            declaration=external,
        )
    assert error.value.code == "vendor_rights_unverified"

    vendor_row = price_row(source_origin="external-vendor")
    with pytest.raises(ExternalVendorRetentionError):
        parse_price_csv(
            csv_text(PRICE_HEADERS, [vendor_row]),
            instrument=Instrument("VOD.L", "UK", "GBP"),
            exchange="LSE",
            declaration=DECLARATION,
        )


def test_price_import_has_strict_schema_encoding_and_resource_bounds():
    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            "symbol,symbol\nMSFT,MSFT\n",
            instrument=Instrument("MSFT", "US", "USD"),
            exchange="NASDAQ",
            declaration=DECLARATION,
        )
    assert error.value.code == "headers"

    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            b"\xff",
            instrument=Instrument("MSFT", "US", "USD"),
            exchange="NASDAQ",
            declaration=DECLARATION,
        )
    assert error.value.code == "encoding"

    with pytest.raises(ManualObservationError) as error:
        parse_price_csv(
            "x" * (MAX_CSV_BYTES + 1),
            instrument=Instrument("MSFT", "US", "USD"),
            exchange="NASDAQ",
            declaration=DECLARATION,
        )
    assert error.value.code == "size_limit"


def test_fx_is_explicitly_directed_and_has_per_field_provenance():
    observation = parse_fx_csv(
        csv_text(FX_HEADERS, [fx_row()]), declaration=DECLARATION
    )[0]
    assert observation.rate.from_currency == "USD"
    assert observation.rate.to_currency == "GBP"
    assert observation.rate.rate == Decimal("0.80")
    assert observation.rate.as_of == datetime(2026, 11, 7, 14, 30, tzinfo=UTC)
    assert set(dict(observation.field_provenance)) == set(FX_HEADERS)
    assert observation.rate.convert(FinanceMoney(Decimal("10.00"), "USD")) == (
        FinanceMoney(Decimal("8.0000"), "GBP")
    )
    with pytest.raises(DecisionInputError):
        observation.rate.convert(FinanceMoney(Decimal(8), "GBP"))
    assert FinanceMoney(Decimal(10), "USD").currency == "USD"


def test_fx_reverse_direction_requires_its_own_explicit_rate():
    row = fx_row(from_currency="GBP", to_currency="USD", rate="1.25")
    reverse = parse_fx_csv(
        csv_text(FX_HEADERS, [row]), declaration=DECLARATION
    )[0].rate
    assert reverse.from_currency == "GBP"
    assert reverse.to_currency == "USD"
    assert reverse.rate == Decimal("1.25")


@pytest.mark.parametrize(
    ("rows", "code"),
    [
        ([fx_row(to_currency="GBp")], "fx_pair"),
        ([fx_row(rate="0")], "decimal_range"),
        ([fx_row(rate="Infinity")], "decimal_invalid"),
        ([fx_row(as_of="2026-11-07T14:30:00")], "timestamp_invalid"),
        ([fx_row(purpose="portfolio")], "fx_purpose"),
        (
            [
                fx_row(as_of="2026-11-07T14:30:00Z"),
                fx_row(as_of="2026-11-07T14:29:00Z"),
            ],
            "record_order",
        ),
    ],
)
def test_fx_import_rejects_ambiguous_values_or_direction(rows, code):
    with pytest.raises(ManualObservationError) as error:
        parse_fx_csv(csv_text(FX_HEADERS, rows), declaration=DECLARATION)
    assert error.value.code == code


def test_fundamental_import_preserves_manual_status_period_units_and_provenance():
    rows = [
        fundamental_row(),
        fundamental_row(
            fact="net_income",
            value="-20.50",
            unit="GBP million",
            period_start="2025-01-01",
            period_end="2025-12-31",
            value_status="estimated",
        ),
    ]
    observations = parse_fundamental_csv(
        csv_text(FUNDAMENTAL_HEADERS, rows),
        instrument=Instrument("VOD.L", "UK", "GBP"),
        exchange="LSE",
        declaration=DECLARATION,
    )
    assert len(observations) == 2
    fact = observations[0]
    assert fact.value == Decimal("123456.78")
    assert fact.unit == "GBP"
    assert fact.period_start == date_from_iso("2025-04-01")
    assert fact.period_end == date_from_iso("2026-03-31")
    assert fact.value_status == "reported"
    assert fact.as_of == datetime(2026, 5, 1, 9, tzinfo=UTC)
    assert set(dict(fact.field_provenance)) == set(FUNDAMENTAL_HEADERS)
    assert observations[1].value == Decimal("-20.50")
    assert observations[1].value_status == "estimated"


def date_from_iso(value: str):
    from datetime import date

    return date.fromisoformat(value)


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"symbol": "FTSE"}, "identity_mismatch"),
        ({"unit": ""}, "unit_invalid"),
        ({"value": "NaN"}, "decimal_invalid"),
        ({"value_status": "actual"}, "value_status"),
        ({"period_start": ""}, "period_bounds"),
        (
            {"period_start": "2026-04-01", "period_end": "2026-03-31"},
            "period_order",
        ),
    ],
)
def test_fundamental_import_rejects_mixed_identity_or_unstated_units(changes, code):
    with pytest.raises(ManualObservationError) as error:
        parse_fundamental_csv(
            csv_text(FUNDAMENTAL_HEADERS, [fundamental_row(**changes)]),
            instrument=Instrument("VOD.L", "UK", "GBP"),
            exchange="LSE",
            declaration=DECLARATION,
        )
    assert error.value.code == code
