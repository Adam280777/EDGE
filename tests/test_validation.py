from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from edge.domain import SimulationConfig
from edge.errors import ValidationError
from edge.fixtures import demo_dataset
from edge.validation import dataset_document, load_dataset, validate_config, validate_dataset


def test_fixture_is_explicitly_fictional_and_reproducible() -> None:
    dataset = demo_dataset()
    assert dataset.synthetic
    assert dataset.instrument_id == "EDGE_TEST.SIM"
    assert "not financial market data" in dataset.source
    assert validate_dataset(dataset_document(dataset)) == dataset
    assert demo_dataset().checksum == dataset.checksum


@pytest.mark.parametrize(("field", "value"), [
    ("synthetic", False), ("synthetic", "true"), ("version", True),
    ("format", "other"), ("corporate_action_status", "unknown"),
    ("corporate_action_status", "split"), ("currency", "GBP"),
    ("price_unit", "GBp"), ("source", ""), ("retrieved_at", "2024-01-01"),
    ("retrieved_at", "nonsense"), ("retrieved_at", "2999-01-01T00:00:00Z"),
    ("retrieved_at", "2020-01-01T00:00:00Z"), ("instrument_id", "AAPL.XNAS"),
])
def test_invalid_dataset_metadata_is_blocked(field: str, value: object) -> None:
    raw = dataset_document(demo_dataset())
    raw[field] = value
    with pytest.raises(ValidationError):
        validate_dataset(raw)


@pytest.mark.parametrize(("field", "value"), [
    ("bid", "NaN"), ("ask", "Infinity"), ("bid", "-1.00"), ("ask", "0.00"),
    ("bid", 100.0), ("ask", "100.001"), ("ask", None),
    ("bid_size", True), ("ask_size", 0), ("timestamp_ns", False),
])
def test_invalid_quote_values_are_blocked(field: str, value: object) -> None:
    raw = dataset_document(demo_dataset())
    raw["quotes"][0][field] = value
    with pytest.raises(ValidationError):
        validate_dataset(raw)


def test_duplicate_out_of_order_crossed_and_suspicious_quotes_rejected() -> None:
    original = dataset_document(demo_dataset())
    for mode in ("duplicate", "out-of-order", "crossed", "jump", "missing", "unknown-field"):
        raw = deepcopy(original)
        if mode == "duplicate":
            raw["quotes"][1]["timestamp_ns"] = raw["quotes"][0]["timestamp_ns"]
        elif mode == "out-of-order":
            raw["quotes"][1], raw["quotes"][0] = raw["quotes"][0], raw["quotes"][1]
        elif mode == "crossed":
            raw["quotes"][0]["bid"] = "999.00"
        elif mode == "jump":
            raw["quotes"][1].update(bid="199.00", ask="200.00")
        elif mode == "missing":
            del raw["quotes"][0]["bid"]
        else:
            raw["live"] = True
        with pytest.raises(ValidationError):
            validate_dataset(raw)


def test_loader_surfaces_missing_malformed_duplicate_and_large_files(tmp_path) -> None:
    with pytest.raises(ValidationError, match="Cannot read"):
        load_dataset(tmp_path / "missing.json")
    file = tmp_path / "bad.json"
    file.write_text('{"format": "edge.quote-dataset", "format": "x"}', encoding="utf-8")
    with pytest.raises(ValidationError, match="Duplicate JSON"):
        load_dataset(file)
    file.write_bytes(b"[]" + b" " * 5_000_000)
    with pytest.raises(ValidationError, match="5 MB"):
        load_dataset(file)
    file.write_text("{", encoding="utf-8")
    with pytest.raises(ValidationError, match="Cannot read"):
        load_dataset(file)


@pytest.mark.parametrize("config", [
    SimulationConfig(initial_cash=Decimal("NaN")),
    SimulationConfig(initial_cash=Decimal("-1")),
    SimulationConfig(initial_cash=Decimal("100.001")),
    SimulationConfig(commission_per_order=Decimal("-1")),
    SimulationConfig(commission_per_order=Decimal("Infinity")),
    SimulationConfig(quantity=True),
    SimulationConfig(quantity=0),
    SimulationConfig(quantity=1001),
    SimulationConfig(initial_cash=Decimal("10")),
    replace(SimulationConfig(), scenario="live"),
])
def test_config_does_not_silently_change_bad_or_unfunded_requests(config: SimulationConfig) -> None:
    with pytest.raises(ValidationError):
        validate_config(config, demo_dataset())
