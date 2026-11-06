"""Strict, fail-closed validation for the synthetic engine acceptance dataset."""

import hashlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from edge.domain import Dataset, Quote, SimulationConfig
from edge.errors import ValidationError

FORMAT = "edge.quote-dataset"
VERSION = 1
MAX_DATASET_BYTES = 5_000_000
DATASET_FIELDS = {
    "format", "version", "instrument_id", "company_name", "currency", "price_unit",
    "source", "retrieved_at", "synthetic", "corporate_action_status", "quotes",
}
QUOTE_FIELDS = {"timestamp_ns", "bid", "ask", "bid_size", "ask_size"}
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _object(value: object, fields: set[str], context: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValidationError(f"{context}: expected exactly {', '.join(sorted(fields))}.")
    return value


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValidationError(f"{context}: expected a non-empty string of at most 500 characters.")
    return value


def _integer(value: object, minimum: int, maximum: int, context: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"{context}: expected an integer from {minimum} to {maximum}.")
    return value


def _price(value: object, context: str) -> Decimal:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+\.[0-9]{2}", value):
        raise ValidationError(f"{context}: expected a positive decimal string with two decimal places.")
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ValidationError(f"{context}: invalid decimal.") from error
    if not result.is_finite() or not Decimal("0.01") <= result <= Decimal("1000000"):
        raise ValidationError(f"{context}: price must be finite and between 0.01 and 1000000.")
    return result


def dataset_document(dataset: Dataset) -> dict[str, object]:
    return {
        "format": FORMAT, "version": VERSION,
        "instrument_id": dataset.instrument_id, "company_name": dataset.company_name,
        "currency": dataset.currency, "price_unit": dataset.price_unit,
        "source": dataset.source, "retrieved_at": dataset.retrieved_at,
        "synthetic": dataset.synthetic,
        "corporate_action_status": dataset.corporate_action_status,
        "quotes": [
            {"timestamp_ns": q.timestamp_ns, "bid": str(q.bid), "ask": str(q.ask),
             "bid_size": q.bid_size, "ask_size": q.ask_size}
            for q in dataset.quotes
        ],
    }


def validate_dataset(value: object) -> Dataset:
    raw = _object(value, DATASET_FIELDS, "Dataset")
    if raw["format"] != FORMAT or type(raw["version"]) is not int or raw["version"] != VERSION:
        raise ValidationError("Unsupported dataset format/version; expected edge.quote-dataset version 1.")
    if raw["synthetic"] is not True:
        raise ValidationError(
            "Real market data is not accepted yet: provider rights, publication times, freshness "
            "and complete corporate-action coverage require a separate acceptance milestone."
        )
    if raw["corporate_action_status"] != "not-applicable-synthetic":
        raise ValidationError("Unknown or action-bearing history is blocked; use not-applicable-synthetic.")
    if raw["instrument_id"] != "EDGE_TEST.SIM":
        raise ValidationError("Only fictional instrument EDGE_TEST.SIM is supported by this acceptance milestone.")
    if raw["currency"] != "USD" or raw["price_unit"] != "USD":
        raise ValidationError("Unresolved currency/unit: this fixture requires USD currency and USD price_unit.")
    company = _text(raw["company_name"], "company_name")
    source = _text(raw["source"], "source")
    retrieved = _text(raw["retrieved_at"], "retrieved_at")
    try:
        retrieved_time = datetime.fromisoformat(retrieved.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValidationError("retrieved_at must be an ISO 8601 timestamp with timezone.") from error
    if retrieved_time.tzinfo is None or retrieved_time.utcoffset() is None:
        raise ValidationError("retrieved_at requires an explicit timezone.")
    retrieved_time = retrieved_time.astimezone(timezone.utc)
    if retrieved_time > datetime.now(timezone.utc):
        raise ValidationError("retrieved_at cannot be in the future.")
    raw_quotes = raw["quotes"]
    if not isinstance(raw_quotes, list) or not 6 <= len(raw_quotes) <= 10_000:
        raise ValidationError("quotes must contain from 6 to 10000 observations.")
    quotes: list[Quote] = []
    previous_timestamp = -1
    previous_mid: Decimal | None = None
    for index, value in enumerate(raw_quotes):
        record = _object(value, QUOTE_FIELDS, f"Quote {index}")
        ts = _integer(record["timestamp_ns"], 1, 2**63 - 1, f"Quote {index} timestamp_ns")
        if ts <= previous_timestamp:
            raise ValidationError(f"Quote {index}: duplicate or out-of-order timestamp.")
        elapsed = retrieved_time - EPOCH
        retrieved_ns = (
            (elapsed.days * 86_400 + elapsed.seconds) * 1_000_000_000
            + elapsed.microseconds * 1_000
        )
        if ts > retrieved_ns:
            raise ValidationError(f"Quote {index}: event time is later than retrieved_at.")
        bid = _price(record["bid"], f"Quote {index} bid")
        ask = _price(record["ask"], f"Quote {index} ask")
        if bid > ask:
            raise ValidationError(f"Quote {index}: crossed bid/ask.")
        mid = (bid + ask) / 2
        if previous_mid is not None and abs(mid / previous_mid - 1) > Decimal("0.10"):
            raise ValidationError(f"Quote {index}: unexplained price movement exceeds fixture quality threshold 10%.")
        quotes.append(Quote(
            timestamp_ns=ts, bid=bid, ask=ask,
            bid_size=_integer(record["bid_size"], 1, 1_000_000_000, f"Quote {index} bid_size"),
            ask_size=_integer(record["ask_size"], 1, 1_000_000_000, f"Quote {index} ask_size"),
        ))
        previous_timestamp, previous_mid = ts, mid
    dataset = Dataset(
        instrument_id="EDGE_TEST.SIM", company_name=company, currency="USD", price_unit="USD",
        source=source, retrieved_at=retrieved, synthetic=True,
        corporate_action_status="not-applicable-synthetic", quotes=tuple(quotes), checksum="",
    )
    checksum = hashlib.sha256(canonical_json(dataset_document(dataset)).encode("utf-8")).hexdigest()
    return Dataset(
        instrument_id=dataset.instrument_id, company_name=company, currency="USD", price_unit="USD",
        source=source, retrieved_at=retrieved, synthetic=True,
        corporate_action_status="not-applicable-synthetic", quotes=tuple(quotes), checksum=checksum,
    )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValidationError(f"Duplicate JSON field {key!r}.")
        value[key] = item
    return value


def load_dataset(path: Path) -> Dataset:
    try:
        with path.open("rb") as stream:
            contents = stream.read(MAX_DATASET_BYTES + 1)
        if len(contents) > MAX_DATASET_BYTES:
            raise ValidationError("Dataset exceeds the 5 MB acceptance input limit.")
        raw = json.loads(contents, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError(f"Cannot read dataset {path}: {error}") from error
    return validate_dataset(raw)


def validate_config(config: SimulationConfig, dataset: Dataset) -> None:
    if config.scenario not in ("round-trip", "risk-rejection"):
        raise ValidationError("Unsupported scenario; live execution is not available.")
    for name in ("initial_cash", "commission_per_order"):
        value = getattr(config, name)
        if not isinstance(value, Decimal) or not value.is_finite() or value.as_tuple().exponent < -2:
            raise ValidationError(f"{name} must be a finite Decimal with at most two fractional digits.")
    if not Decimal("0.01") <= config.initial_cash <= Decimal("1000000000"):
        raise ValidationError("initial_cash must be between 0.01 and 1000000000.")
    if not Decimal("0.00") <= config.commission_per_order <= Decimal("10000"):
        raise ValidationError("commission_per_order must be between 0 and 10000.")
    _integer(config.quantity, 1, 1_000_000, "quantity")
    if config.scenario == "round-trip":
        if config.quantity > min(min(q.ask_size, q.bid_size) for q in dataset.quotes):
            raise ValidationError("quantity exceeds displayed fixture liquidity.")
        if config.quantity * max(q.ask for q in dataset.quotes) + 2 * config.commission_per_order > config.initial_cash:
            raise ValidationError("Insufficient starting cash for the fixture round trip and explicit fees.")
