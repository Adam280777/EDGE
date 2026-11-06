"""Focused acceptance tests for the pinned native Nautilus simulation adapter."""

import json
from decimal import Decimal
from pathlib import Path
import subprocess
import sys

import pytest

from edge.domain import SimulationConfig
from edge.errors import ValidationError
from edge.fixtures import demo_dataset
from edge.nautilus_engine import run_simulation


def test_round_trip_uses_native_quote_aware_fills_and_decimal_accounting() -> None:
    dataset = demo_dataset()
    config = SimulationConfig()

    outcome = run_simulation(dataset, config)

    assert outcome.engine_version == "2.0.0rc8"
    assert [(fill["side"], fill["price"]) for fill in outcome.fills] == [
        ("buy", "100.10"),
        ("sell", "100.90"),
    ]
    assert [fill["filled_at_ns"] for fill in outcome.fills] == [
        dataset.quotes[0].timestamp_ns,
        dataset.quotes[1].timestamp_ns,
    ]
    assert [order["submitted_at_ns"] for order in outcome.orders] == [
        dataset.quotes[0].timestamp_ns,
        dataset.quotes[1].timestamp_ns,
    ]
    assert all(order["status"] == "filled" for order in outcome.orders)
    assert [fill["fee"] for fill in outcome.fills] == ["1.00", "1.00"]
    assert len(outcome.positions) == 1
    assert outcome.positions[0]["status"] == "closed"
    assert outcome.positions[0]["quantity"] == "0"
    assert outcome.positions[0]["signed_quantity"] == "0"
    assert outcome.positions[0]["opening_quantity"] == "10"
    assert outcome.ending_quantity == "0"
    assert not outcome.rejections

    # Independent Decimal calculation from the native fill facts, not an
    # alternate simulation or hard-coded realized P&L implementation.
    buy = Decimal(str(outcome.fills[0]["price"]))
    sell = Decimal(str(outcome.fills[1]["price"]))
    quantity = Decimal(str(outcome.fills[0]["quantity"]))
    fees = sum((Decimal(str(fill["fee"])) for fill in outcome.fills), Decimal("0"))
    expected_pnl = (sell - buy) * quantity - fees
    expected_cash = config.initial_cash + expected_pnl
    assert expected_pnl == Decimal("6.00")
    assert Decimal(outcome.realized_pnl) == expected_pnl
    assert Decimal(outcome.ending_cash) == expected_cash
    assert outcome.assumptions
    assert any("never reads a later quote" in item for item in outcome.assumptions)


def test_custom_valid_quantity_and_zero_commission() -> None:
    config = SimulationConfig(quantity=1, commission_per_order=Decimal("0.00"))

    outcome = run_simulation(demo_dataset(), config)

    assert [fill["quantity"] for fill in outcome.fills] == ["1", "1"]
    assert [fill["fee"] for fill in outcome.fills] == ["0.00", "0.00"]
    assert [order["filled_quantity"] for order in outcome.orders] == ["1", "1"]
    assert all(order["status"] == "filled" for order in outcome.orders)
    buy = Decimal(str(outcome.fills[0]["price"]))
    sell = Decimal(str(outcome.fills[1]["price"]))
    fees = sum((Decimal(str(fill["fee"])) for fill in outcome.fills), Decimal("0"))
    expected_pnl = (sell - buy) * Decimal("1") - fees
    assert Decimal(outcome.realized_pnl) == expected_pnl == Decimal("0.80")
    assert Decimal(outcome.ending_cash) == config.initial_cash + expected_pnl


def test_native_risk_engine_denies_order_without_fills() -> None:
    outcome = run_simulation(
        demo_dataset(),
        SimulationConfig(scenario="risk-rejection"),
    )

    assert outcome.fills == ()
    assert len(outcome.orders) == 1
    assert outcome.orders[0]["status"] == "denied"
    assert len(outcome.rejections) == 1
    rejection = outcome.rejections[0]
    assert rejection["native_event"] == "OrderDenied"
    assert rejection["reason"] == (
        "NOTIONAL_EXCEEDS_MAX_PER_ORDER: max=1.00 USD, notional=1001.00 USD"
    )
    assert outcome.ending_cash == "10000.00"
    assert outcome.realized_pnl == "0.00"
    assert outcome.ending_quantity == "0"


def test_simulation_is_repeatable() -> None:
    dataset = demo_dataset()
    config = SimulationConfig()

    assert run_simulation(dataset, config) == run_simulation(dataset, config)


def test_engine_rejects_non_rc8_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    import edge.nautilus_engine as adapter

    monkeypatch.setattr(adapter, "version", lambda _name: "2.0.0")
    with pytest.raises(RuntimeError, match="expected 2.0.0rc8, found 2.0.0"):
        run_simulation(demo_dataset(), SimulationConfig())


def test_engine_rechecks_input_contracts_before_starting() -> None:
    dataset = demo_dataset()
    config = SimulationConfig(quantity=2_000)
    with pytest.raises(ValidationError, match="quantity exceeds displayed fixture liquidity"):
        run_simulation(dataset, config)


def test_cli_simulation_stdout_is_parseable_json(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "edge",
            "simulate",
            "--output-dir",
            str(tmp_path),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    parsed = json.loads(completed.stdout)
    assert parsed["status"] == "succeeded"
    assert parsed["result"]["outcome"]["ending_cash"] == "10006.00"
    assert completed.stderr == ""
