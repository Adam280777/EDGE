"""Application workflow around Nautilus, not a second trading engine."""

from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sqlite3

from edge.domain import Dataset, SimulationConfig
from edge.errors import EdgeError
from edge.run_store import RunStore
from edge.validation import dataset_document, validate_config, validate_dataset

NAUTILUS_VERSION = "2.0.0rc8"


def engine_version() -> str:
    try:
        installed = version("nautilus_trader")
    except PackageNotFoundError as error:
        raise EdgeError(
            "NautilusTrader is missing. Use the isolated simulation environment and install "
            "requirements-simulation.txt; do not modify the existing .venv."
        ) from error
    if installed != NAUTILUS_VERSION:
        raise EdgeError(f"Nautilus version acceptance failed: expected {NAUTILUS_VERSION}, found {installed}.")
    return installed


def run(dataset: Dataset, config: SimulationConfig, output_dir: Path) -> dict[str, object]:
    dataset = validate_dataset(dataset_document(dataset))
    validate_config(config, dataset)
    installed = engine_version()
    store = RunStore(output_dir)
    manifest: dict[str, object] = {
        "format": "edge.simulation-run", "version": 1,
        "mode": "simulation-only", "synthetic": dataset.synthetic,
        "source": dataset.source, "retrieved_at": dataset.retrieved_at,
        "instrument_id": dataset.instrument_id, "currency": dataset.currency,
        "price_unit": dataset.price_unit, "dataset_checksum": dataset.checksum,
        "dataset_event_range_ns": [dataset.quotes[0].timestamp_ns, dataset.quotes[-1].timestamp_ns],
        "corporate_action_status": dataset.corporate_action_status,
        "engine": "NautilusTrader", "engine_version": installed,
        "strategy": "edge-quote-acceptance", "strategy_version": 1,
        "scenario": config.scenario,
        "config": {
            "initial_cash": str(config.initial_cash), "quantity": config.quantity,
            "commission_per_order": str(config.commission_per_order),
        },
        "limitations": [
            "Synthetic acceptance data, not a market backtest, prediction or trade recommendation.",
            "No live broker route or credentials. No actual user portfolio.",
            "Fixed quote fixture: no market impact, latency or queue-position realism.",
            "Real histories are blocked until provider and corporate-action acceptance.",
            "Nautilus v2 prerelease acceptance is limited to tested fixture behaviors.",
        ],
    }
    run_id = store.begin(manifest, dataset_document(dataset))
    try:
        from edge.nautilus_engine import run_simulation

        outcome = run_simulation(dataset, config)
        result: dict[str, object] = {
            "format": "edge.simulation-result", "version": 1,
            "run_id": run_id, "status": "succeeded", "mode": "simulation-only",
            "synthetic": True, "dataset_checksum": dataset.checksum,
            "outcome": asdict(outcome),
        }
    except Exception as error:
        # A failed engine run remains auditable; it is never an empty successful result.
        failure: dict[str, object] = {
            "format": "edge.simulation-result", "version": 1,
            "run_id": run_id, "status": "failed",
            "error": f"{type(error).__name__}: {error}",
        }
        try:
            store.finish(run_id, failure)
        except (OSError, EdgeError, sqlite3.Error) as storage_error:
            raise EdgeError(
                f"Engine failed ({error}); failure audit also failed ({storage_error}). "
                f"Inspect run {run_id} in {output_dir}."
            ) from error
        raise EdgeError(f"Nautilus run {run_id} failed: {error}. Failure recorded in {output_dir}.") from error
    store.finish(run_id, result)
    return store.get(run_id)
