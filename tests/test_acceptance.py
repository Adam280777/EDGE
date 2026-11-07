"""Independent, local-only acceptance tests for the first EDGE foundation.

All simulation output belongs under pytest's temporary directory. These tests
never contact a broker, install dependencies, or modify either virtualenv.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from edge import simulation_service
from edge.domain import SimulationConfig
from edge.errors import EdgeError, ValidationError
from edge.fixtures import demo_dataset
from edge.run_store import RunStore
from edge.validation import dataset_document, load_dataset, validate_dataset

ROOT = Path(__file__).resolve().parents[1]


def cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "1"
    return subprocess.run(
        [sys.executable, "-m", "edge", *map(str, arguments)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_original_virtualenv_remains_pip_only() -> None:
    """Use metadata, not imports or package installation, in the old env."""
    if not (ROOT / ".venv").exists():
        pytest.skip("Original local virtualenv is absent in a fresh checkout.")
    interpreter = ROOT / ".venv" / "Scripts" / "python.exe"
    assert interpreter.is_file()
    result = subprocess.run(
        [
            str(interpreter),
            "-c",
            "import importlib.metadata as m,json;"
            "print(json.dumps(sorted(d.metadata['Name'].lower() "
            "for d in m.distributions())))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert_ok(result)
    assert json.loads(result.stdout) == ["pip"]


def test_original_virtualenv_check_skips_fresh_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CI must not create a local historical environment to run acceptance."""
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    with pytest.raises(pytest.skip.Exception, match="fresh checkout"):
        test_original_virtualenv_remains_pip_only()


def test_doctor_succeeds_with_pinned_engine() -> None:
    result = cli("doctor")
    assert_ok(result)
    assert "2.0.0rc8" in result.stdout


@pytest.mark.parametrize(
    "extra",
    [
        ("--live",),
        ("--broker", "example"),
        ("--scenario", "live"),
    ],
)
def test_cli_cannot_enable_live_or_broker(tmp_path: Path, extra: tuple[str, ...]) -> None:
    output = tmp_path / "runs"
    result = cli("simulate", "--output-dir", str(output), *extra)
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert not list(output.rglob("manifest.json"))


def test_fixture_identity_prices_and_provenance() -> None:
    dataset = demo_dataset()
    assert dataset.instrument_id == "EDGE_TEST.SIM"
    assert dataset.synthetic is True
    assert dataset.currency == dataset.price_unit == "USD"
    assert "synthetic" in dataset.source.lower()
    assert dataset.corporate_action_status == "not-applicable-synthetic"
    assert len(dataset.quotes) == 6
    for index, quote in enumerate(dataset.quotes):
        assert quote.timestamp_ns == 1704205800000000000 + index * 60_000_000_000
        assert quote.bid == Decimal("99.90") + index
        assert quote.ask == Decimal("100.10") + index
        assert quote.bid_size == quote.ask_size == 1000
    document = dataset_document(dataset)
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert dataset.checksum == hashlib.sha256(canonical.encode()).hexdigest()
    assert validate_dataset(document) == dataset


INVALID_DATA = [
    ("missing-source", ("source",), None),
    ("real-history", ("synthetic",), False),
    ("unknown-actions", ("corporate_action_status",), "unknown"),
    ("split-history", ("corporate_action_status",), "split"),
    ("wrong-identity", ("instrument_id",), "AAPL.SIM"),
    ("wrong-currency", ("currency",), "GBP"),
    ("unresolved-units", ("price_unit",), "GBp"),
    ("missing-timezone", ("retrieved_at",), "2026-11-05T00:00:00"),
    ("future-retrieval", ("retrieved_at",), "2999-01-01T00:00:00Z"),
    ("event-after-retrieval", ("retrieved_at",), "2020-01-01T00:00:00Z"),
    ("duplicate-time", ("quotes", 1, "timestamp_ns"), 1704205800000000000),
    ("out-of-order", ("quotes", 1, "timestamp_ns"), 1704205799999999999),
    ("boolean-time", ("quotes", 0, "timestamp_ns"), True),
    ("nonfinite-bid", ("quotes", 0, "bid"), "NaN"),
    ("infinite-ask", ("quotes", 0, "ask"), "Infinity"),
    ("float-price", ("quotes", 0, "bid"), 99.90),
    ("negative-price", ("quotes", 0, "bid"), "-1.00"),
    ("zero-price", ("quotes", 0, "bid"), "0.00"),
    ("wrong-precision", ("quotes", 0, "bid"), "99.901"),
    ("crossed-quote", ("quotes", 0, "bid"), "101.00"),
    ("zero-liquidity", ("quotes", 0, "ask_size"), 0),
    ("fractional-liquidity", ("quotes", 0, "ask_size"), 1.5),
    ("boolean-liquidity", ("quotes", 0, "bid_size"), True),
    ("unexplained-jump", ("quotes", 1, "ask"), "900.10"),
    ("incomplete", ("quotes",), []),
]


@pytest.mark.parametrize("name,path,value", INVALID_DATA, ids=[item[0] for item in INVALID_DATA])
def test_invalid_dataset_rejected_without_imputation(name, path, value) -> None:
    document = deepcopy(dataset_document(demo_dataset()))
    target = document
    for part in path[:-1]:
        target = target[part]
    if name == "missing-source":
        del target[path[-1]]
    else:
        target[path[-1]] = value
    original = deepcopy(document)
    with pytest.raises(ValidationError):
        validate_dataset(document)
    assert document == original


@pytest.mark.parametrize("contents", ['{"format":', '{"format":1,"format":2}', "null", "[]"])
def test_malformed_dataset_loader(tmp_path: Path, contents: str) -> None:
    path = tmp_path / "input.json"
    path.write_text(contents, encoding="utf-8")
    with pytest.raises(ValidationError):
        load_dataset(path)


def test_missing_dataset_loader(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        load_dataset(tmp_path / "absent.json")


@pytest.mark.parametrize(
    "changes",
    [
        {"scenario": "live"},
        {"initial_cash": Decimal("NaN")},
        {"initial_cash": Decimal("Infinity")},
        {"initial_cash": Decimal("0")},
        {"initial_cash": Decimal("-10")},
        {"initial_cash": Decimal("100.00")},
        {"initial_cash": 10000.0},
        {"initial_cash": Decimal("10000.001")},
        {"commission_per_order": Decimal("-1")},
        {"commission_per_order": Decimal("NaN")},
        {"commission_per_order": Decimal("1.001")},
        {"quantity": 0},
        {"quantity": -1},
        {"quantity": True},
        {"quantity": 1.5},
        {"quantity": 1001},
    ],
)
def test_invalid_config_fails_before_engine_or_storage(tmp_path: Path, monkeypatch, changes) -> None:
    def forbidden_engine():
        pytest.fail("Engine was reached before configuration validation")

    monkeypatch.setattr(simulation_service, "engine_version", forbidden_engine)
    with pytest.raises(ValidationError):
        simulation_service.run(demo_dataset(), replace(SimulationConfig(), **changes), tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_service_revalidates_dataclass_before_engine(tmp_path: Path, monkeypatch) -> None:
    def forbidden_engine():
        pytest.fail("Engine was reached before dataset validation")

    monkeypatch.setattr(simulation_service, "engine_version", forbidden_engine)
    with pytest.raises(ValidationError):
        simulation_service.run(
            replace(demo_dataset(), currency="GBP"), SimulationConfig(), tmp_path / "runs"
        )
    assert not (tmp_path / "runs").exists()


def assert_audit(root: Path, record: dict) -> None:
    manifest = record["manifest"]
    run_id = manifest["run_id"]
    directory = root / "runs" / run_id
    assert json.loads((directory / "manifest.json").read_text()) == manifest
    assert json.loads((directory / "result.json").read_text()) == record["result"]
    document = json.loads((directory / "dataset.json").read_text())
    assert validate_dataset(document).checksum == manifest["dataset_checksum"]
    with sqlite3.connect(root / "edge.sqlite3") as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        row = connection.execute(
            "SELECT status,manifest_json,result_json,dataset_checksum,finished_at FROM runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
    assert row[0] == record["status"]
    assert json.loads(row[1]) == manifest
    assert json.loads(row[2]) == record["result"]
    assert row[3] == manifest["dataset_checksum"]
    assert row[4] is not None
    assert manifest["mode"] == "simulation-only"
    assert manifest["synthetic"] is True
    assert manifest["source"] and manifest["retrieved_at"]
    assert manifest["engine_version"] == "2.0.0rc8"
    assert manifest["limitations"]


def test_engine_exception_persists_failed_audit(tmp_path: Path, monkeypatch) -> None:
    """Fault injection tests persistence, not the native engine behavior."""
    fake_engine = ModuleType("edge.nautilus_engine")

    def fail(*args):
        raise RuntimeError("controlled acceptance engine failure")

    fake_engine.run_simulation = fail
    monkeypatch.setitem(sys.modules, "edge.nautilus_engine", fake_engine)
    root = tmp_path / "failed-run"
    with pytest.raises(EdgeError, match="controlled acceptance engine failure"):
        simulation_service.run(demo_dataset(), SimulationConfig(), root)
    store = RunStore(root)
    rows = store.list_runs()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    record = store.get(rows[0]["run_id"])
    assert record["result"]["status"] == "failed"
    assert "controlled acceptance engine failure" in record["result"]["error"]
    assert_audit(root, record)
    with pytest.raises(EdgeError, match="finalized"):
        store.finish(rows[0]["run_id"], {"status": "succeeded"})


def test_unrecognized_database_is_not_modified(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
        connection.execute("INSERT INTO unrelated VALUES ('preserve me')")
    before = database.read_bytes()
    with pytest.raises(EdgeError, match="unrecognized"):
        RunStore(tmp_path).begin(
            {"scenario": "round-trip", "dataset_checksum": "example"}, {}
        )
    assert database.read_bytes() == before
    assert not (tmp_path / "runs").exists()


def test_missing_store_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "nonexistent"
    with pytest.raises(EdgeError):
        RunStore(root).list_runs()
    assert not root.exists()


def outcome_from_record(record: dict) -> dict:
    assert record["status"] == "succeeded"
    assert record["result"]["status"] == "succeeded"
    return record["result"]["outcome"]


def simulation_cli_record(result: subprocess.CompletedProcess[str]) -> dict:
    """Keep economic verification independent of the stdout logging defect."""
    assert_ok(result)
    start = result.stdout.find("\n{")
    return json.loads(result.stdout[start + 1:] if start >= 0 else result.stdout)


def assert_round_trip_accounting(outcome: dict, dataset, config: SimulationConfig) -> None:
    """Recompute cash, inventory and net P/L from native reported fill facts."""
    assert outcome["engine_version"] == "2.0.0rc8"
    assert len(outcome["orders"]) == len(outcome["fills"]) == 2
    assert not outcome["rejections"]
    orders = {item["order_id"]: item for item in outcome["orders"]}
    cash = config.initial_cash
    inventory = Decimal("0")
    signed_notional = Decimal("0")
    fees = Decimal("0")
    for index, fill in enumerate(outcome["fills"]):
        order = orders[fill["order_id"]]
        assert fill["instrument_id"] == dataset.instrument_id
        assert fill["currency"] == dataset.currency
        assert fill["side"] == ("buy" if index == 0 else "sell")
        quantity, price, fee = (Decimal(fill[key]) for key in ("quantity", "price", "fee"))
        assert quantity == config.quantity
        assert fee == config.commission_per_order
        assert fill["filled_at_ns"] >= order["submitted_at_ns"]
        observed = [
            quote for quote in dataset.quotes
            if quote.timestamp_ns <= fill["filled_at_ns"]
        ]
        assert observed, "Fill preceded all available market observations"
        quote = observed[-1]
        assert order["submitted_at_ns"] == dataset.quotes[index].timestamp_ns
        assert fill["filled_at_ns"] == quote.timestamp_ns
        assert price == (quote.ask if fill["side"] == "buy" else quote.bid)
        direction = Decimal("1") if fill["side"] == "buy" else Decimal("-1")
        inventory += direction * quantity
        signed_notional -= direction * quantity * price
        fees += fee
        cash -= direction * quantity * price + fee
    assert inventory == Decimal(outcome["ending_quantity"]) == 0
    assert cash == Decimal(outcome["ending_cash"])
    assert signed_notional - fees == Decimal(outcome["realized_pnl"])
    assert cash - config.initial_cash == Decimal(outcome["realized_pnl"])
    assert len(outcome["positions"]) == 1
    assert outcome["positions"][0]["status"] == "closed"
    assert Decimal(outcome["positions"][0]["quantity"]) == 0
    assert outcome["assumptions"]


def test_actual_cli_round_trip_list_show_and_accounting(tmp_path: Path) -> None:
    root = tmp_path / "cli-runs"
    result = cli("simulate", "--scenario", "round-trip", "--output-dir", str(root))
    assert_ok(result)
    record = simulation_cli_record(result)
    outcome = outcome_from_record(record)
    assert_round_trip_accounting(outcome, demo_dataset(), SimulationConfig())
    assert Decimal(outcome["ending_cash"]) == Decimal("10006.00")
    assert Decimal(outcome["realized_pnl"]) == Decimal("6.00")
    assert_audit(root, record)
    listing = cli("runs", "--output-dir", str(root))
    assert_ok(listing)
    rows = json.loads(listing.stdout)
    assert len(rows) == 1
    assert rows[0]["run_id"] == record["manifest"]["run_id"]
    shown = cli("show", rows[0]["run_id"], "--output-dir", str(root))
    assert_ok(shown)
    assert json.loads(shown.stdout) == record


def test_actual_cli_risk_rejection_has_no_fill_or_cash_change(tmp_path: Path) -> None:
    root = tmp_path / "risk-runs"
    result = cli("simulate", "--scenario", "risk-rejection", "--output-dir", str(root))
    assert_ok(result)
    record = simulation_cli_record(result)
    outcome = outcome_from_record(record)
    assert outcome["fills"] == []
    assert outcome["positions"] == []
    assert Decimal(outcome["ending_cash"]) == Decimal("10000")
    assert Decimal(outcome["realized_pnl"]) == 0
    assert Decimal(outcome["ending_quantity"]) == 0
    assert len(outcome["orders"]) == len(outcome["rejections"]) == 1
    rejection = outcome["rejections"][0]
    assert rejection["native_event"] == "OrderDenied"
    assert "NOTIONAL_EXCEEDS_MAX_PER_ORDER" in rejection["reason"]
    assert rejection["order_id"] == outcome["orders"][0]["order_id"]
    assert outcome["orders"][0]["status"] == "denied"
    assert_audit(root, record)


def economic_facts(outcome: dict) -> dict:
    """Exclude engine-generated identity fields, never economic facts."""
    identities = {"order_id", "trade_id", "position_id"}
    return {
        key: [
            {name: value for name, value in item.items() if name not in identities}
            for item in items
        ] if key in {"orders", "fills", "positions", "rejections"} else items
        for key, items in outcome.items()
    }


def test_reproducibility_and_future_quote_independence(tmp_path: Path) -> None:
    dataset = demo_dataset()
    config = SimulationConfig()
    first = simulation_service.run(dataset, config, tmp_path / "first")
    second = simulation_service.run(dataset, config, tmp_path / "second")
    assert first["manifest"]["run_id"] != second["manifest"]["run_id"]
    assert first["manifest"]["dataset_checksum"] == second["manifest"]["dataset_checksum"]
    facts = economic_facts(outcome_from_record(first))
    assert economic_facts(outcome_from_record(second)) == facts
    document = dataset_document(dataset)
    for quote in document["quotes"][2:]:
        quote["bid"], quote["ask"] = "100.90", "101.10"
    changed = validate_dataset(document)
    assert changed.checksum != dataset.checksum
    third = simulation_service.run(changed, config, tmp_path / "changed-future")
    assert economic_facts(outcome_from_record(third)) == facts


def test_external_synthetic_dataset_and_nondefault_decimal_fees(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.json"
    dataset = demo_dataset()
    path.write_text(json.dumps(dataset_document(dataset)), encoding="utf-8")
    root = tmp_path / "external"
    result = cli("simulate", "--dataset", str(path), "--output-dir", str(root))
    assert_ok(result)
    record = simulation_cli_record(result)
    assert_round_trip_accounting(outcome_from_record(record), dataset, SimulationConfig())
    assert_audit(root, record)
    config = SimulationConfig(quantity=7, commission_per_order=Decimal("0.13"))
    other = simulation_service.run(dataset, config, tmp_path / "decimal-fees")
    assert_round_trip_accounting(outcome_from_record(other), dataset, config)
    assert Decimal(other["result"]["outcome"]["realized_pnl"]) == Decimal("5.34")


def test_invalid_cli_dataset_fails_before_creating_run(tmp_path: Path) -> None:
    path = tmp_path / "invalid.json"
    document = dataset_document(demo_dataset())
    document["corporate_action_status"] = "unknown"
    path.write_text(json.dumps(document), encoding="utf-8")
    root = tmp_path / "invalid-runs"
    result = cli("simulate", "--dataset", str(path), "--output-dir", str(root))
    assert result.returncode != 0
    assert "corporate" in result.stderr.lower() or "action" in result.stderr.lower()
    assert "Traceback" not in result.stderr
    assert not root.exists()


def test_round_trip_insufficient_exit_liquidity_is_rejected_before_engine(
    tmp_path: Path, monkeypatch
) -> None:
    document = dataset_document(demo_dataset())
    document["quotes"][1]["bid_size"] = 1
    dataset = validate_dataset(document)

    def forbidden_engine():
        pytest.fail("Insufficient exit liquidity reached the engine")

    monkeypatch.setattr(simulation_service, "engine_version", forbidden_engine)
    with pytest.raises(ValidationError, match="liquidity"):
        simulation_service.run(dataset, SimulationConfig(), tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_simulate_stdout_is_machine_readable_json(tmp_path: Path) -> None:
    result = cli("simulate", "--output-dir", str(tmp_path / "stdout"))
    assert_ok(result)
    assert json.loads(result.stdout)["status"] == "succeeded"


def test_admitted_round_trip_does_not_report_success_with_open_inventory(tmp_path: Path) -> None:
    document = dataset_document(demo_dataset())
    document["quotes"][1]["bid_size"] = 1
    dataset = validate_dataset(document)
    try:
        record = simulation_service.run(dataset, SimulationConfig(), tmp_path / "thin-exit")
    except EdgeError:
        # Explicit preflight or audited execution failure is acceptable.
        return
    outcome = outcome_from_record(record)
    assert Decimal(outcome["ending_quantity"]) == 0, outcome
    assert all(position["status"] == "closed" for position in outcome["positions"]), outcome


# SEC CLI coverage lives alongside the existing CLI acceptance tests. These
# contacts and responses are offline fixtures only, never development credentials.
SEC_TEST_UA = "Offline Analyst offline@edge-research-tests.org"
SEC_NOW = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)


@pytest.fixture
def sec_cli_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    import socket

    def blocked(*args: object, **kwargs: object) -> None:
        pytest.fail("SEC CLI test attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.delenv("EDGE_SEC_USER_AGENT", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def sec_cli_api(monkeypatch: pytest.MonkeyPatch, sec_cli_offline: Path) -> list[str]:
    """Run the real API validations/normalization against a no-I/O provider."""
    import edge.data
    from edge.data.sec_filings import SecProviderInfo, SecProviderPayload

    calls: list[str] = []
    real_lookup = edge.data.lookup_sec_filings
    base = "https://www.sec.gov/Archives/edgar/data/320193/"

    class OfflineProvider:
        def describe(self) -> SecProviderInfo:
            return SecProviderInfo("offline-fixture", "0", "none", ("fixture://sec",))

        def lookup(self, sec_ticker: str, *, user_agent: str) -> SecProviderPayload:
            calls.append(sec_ticker)
            assert user_agent == SEC_TEST_UA
            return SecProviderPayload("320193", "AAPL", (
                {
                    "accession_number": "0000320193-24-000123", "report_type": "10-K",
                    "filing_date": date(2024, 11, 1), "report_date": date(2024, 9, 28),
                    "accepted_date": "2024-11-01T08:01:36+02:00", "primary_doc": "doc.htm",
                    "report_url": f"{base}000032019324000123/doc.htm",
                    "filing_detail_url": f"{base}0000320193-24-000123-index.htm",
                },
                {
                    "accession_number": "0000320193-24-000120", "report_type": "8-K",
                    "filing_date": date(2024, 10, 31), "report_date": None,
                    "accepted_date": None, "primary_doc": None, "report_url": None,
                    "filing_detail_url": f"{base}0000320193-24-000120-index.htm",
                },
            ), (f"offline warning for {SEC_TEST_UA}",))

    def offline_lookup(ticker: str, **kwargs: Any):
        return real_lookup(ticker, provider=OfflineProvider(), clock=lambda: SEC_NOW, **kwargs)

    monkeypatch.setattr(edge.data, "lookup_sec_filings", offline_lookup)
    return calls


def test_sec_cli_full_json_contract(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    sec_cli_api: list[str], sec_cli_offline: Path,
) -> None:
    from edge.cli import main

    monkeypatch.setenv("EDGE_SEC_USER_AGENT", SEC_TEST_UA)
    assert main(["sec-filings", " aapl "]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    document = json.loads(captured.out)
    assert document == {
        "identity": {
            "requested_ticker": "AAPL", "sec_ticker": "AAPL",
            "cik": "0000320193", "sec_primary_ticker": "AAPL",
        },
        "filings": [
            {
                "cik": "0000320193", "accession_number": "0000320193-24-000123",
                "form_type": "10-K", "filing_date": "2024-11-01", "report_date": "2024-09-28",
                "accepted_at": "2024-11-01T06:01:36+00:00", "primary_document": "doc.htm",
                "primary_document_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/doc.htm",
                "filing_index_url": "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000123-index.htm",
            },
            {
                "cik": "0000320193", "accession_number": "0000320193-24-000120",
                "form_type": "8-K", "filing_date": "2024-10-31", "report_date": None,
                "accepted_at": None, "primary_document": None, "primary_document_url": None,
                "filing_index_url": "https://www.sec.gov/Archives/edgar/data/320193/0000320193-24-000120-index.htm",
            },
        ],
        "requested_form_types": ["10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"],
        "start_date": None, "end_date": None,
        "provider_record_count": 2, "excluded_record_count": 0,
        "coverage_start": "2024-10-31", "coverage_end": "2024-11-01",
        "coverage_may_be_incomplete": True,
        "warnings": [
            "offline warning for [redacted]",
            (
                "Provider response covers filings from 2024-10-31 to 2024-11-01 only "
                "(SEC 'recent' submissions block); earlier filings are not included."
            ),
        ],
        "provenance": {
            "source": "SEC EDGAR (www.sec.gov company_tickers.json; data.sec.gov submissions API)",
            "provider": "offline-fixture", "provider_version": "0", "framework_version": "none",
            "endpoints": ["fixture://sec"],
            "retrieved_at_start": "2026-11-05T17:00:00+00:00",
            "retrieved_at_end": "2026-11-05T17:00:00+00:00",
            "timeliness": "As-of retrieval snapshot of EDGAR filing index; delayed metadata, not real-time.",
            "cache_used": False, "user_agent_configured": True,
            "usage_scope": "research_metadata_only; not prices, not financial statements, not simulation input",
        },
    }
    assert SEC_TEST_UA not in captured.out
    assert sec_cli_api == ["AAPL"]
    assert list(sec_cli_offline.iterdir()) == []
    # Identical typed results serialize byte-for-byte identically.
    assert main(["sec-filings", " aapl "]) == 0
    assert capsys.readouterr().out == captured.out


def test_sec_cli_filters_and_empty_selection(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sec_cli_api: list[str],
) -> None:
    from edge.cli import main

    monkeypatch.setenv("EDGE_SEC_USER_AGENT", SEC_TEST_UA)
    assert main(["sec-filings", "AAPL", "--start-date", "2024-10-31",
                 "--end-date", "2024-10-31", "--form", "8-k", "--form", "10-k",
                 "--form", "8-K"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["requested_form_types"] == ["8-K", "10-K"]
    assert document["start_date"] == document["end_date"] == "2024-10-31"
    assert [filing["form_type"] for filing in document["filings"]] == ["8-K"]
    assert document["excluded_record_count"] == 1
    assert document["coverage_may_be_incomplete"] is False
    assert main(["sec-filings", "AAPL", "--form", "DEF 14A"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["filings"] == []
    assert document["excluded_record_count"] == 2
    assert "No filings of the requested form types in the requested window." in document["warnings"]
    assert sec_cli_api == ["AAPL", "AAPL"]


@pytest.mark.parametrize("arguments, expected", [
    (["AAPL1"], "Ticker must"),
    ([""], "Ticker must"),
    (["AAPL", "--form", "S-1"], "not allowlisted"),
    (["AAPL", "--form", "10-K*"], "not allowlisted"),
    (["AAPL", "--form", "10-K,8-K"], "not allowlisted"),
    (["AAPL", "--form", ""], "not allowlisted"),
    (["AAPL", "--start-date", "2024-02-01", "--end-date", "2024-01-01"], "after end_date"),
    (["AAPL", "--start-date", "2027-01-01"], "future"),
    (["AAPL", "--start-date", "1992-12-31"], "predates EDGAR"),
    (["AAPL", "--end-date", "1992-12-31"], "predates EDGAR"),
])
def test_sec_cli_rejects_input_before_contact_or_provider(
    arguments: list[str], expected: str, capsys: pytest.CaptureFixture[str], sec_cli_api: list[str],
) -> None:
    from edge.cli import main

    assert main(["sec-filings", *arguments]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and expected in captured.err
    assert "Traceback" not in captured.err
    assert sec_cli_api == []


@pytest.mark.parametrize("flag", ["--start-date", "--end-date"])
@pytest.mark.parametrize("value", [
    "2024-02-30", "not-a-date", "20241101", "2024-W44-5",
    "2024-1-01", "2024-11-01T00:00:00Z", " 2024-11-01", "",
])
def test_sec_cli_rejects_non_calendar_dates_before_api(
    flag: str, value: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], sec_cli_offline: Path,
) -> None:
    import edge.data
    from edge.cli import main

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid date reached the API")

    monkeypatch.setattr(edge.data, "lookup_sec_filings", forbidden)
    with pytest.raises(SystemExit) as error:
        main(["sec-filings", "AAPL", flag, value])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "must be an ISO calendar date (YYYY-MM-DD)" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("user_agent", [None, "", "not a contact", "Name user@example.com"])
def test_sec_cli_requires_valid_contact_without_request(
    user_agent: str | None, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], sec_cli_api: list[str],
) -> None:
    from edge.cli import main

    if user_agent is not None:
        monkeypatch.setenv("EDGE_SEC_USER_AGENT", user_agent)
    assert main(["sec-filings", "AAPL"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "EDGE_SEC_USER_AGENT" in captured.err
    assert "Traceback" not in captured.err
    assert "user@example.com" not in captured.err
    assert sec_cli_api == []


@pytest.mark.parametrize("error_name", [
    "SecFilingConfigurationError", "SecFilingProviderError", "SecFilingNotFoundError",
    "SecFilingDataError", "ImportError", "OSError",
])
def test_sec_cli_errors_redact_contact_and_do_not_write(
    error_name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    sec_cli_offline: Path,
) -> None:
    import builtins

    import edge.data
    from edge.cli import main
    from edge.data import sec_filings

    monkeypatch.setenv("EDGE_SEC_USER_AGENT", f"  {SEC_TEST_UA}  ")
    error_type = getattr(sec_filings, error_name, None) or getattr(builtins, error_name)

    def failure(*args: object, **kwargs: object) -> None:
        raise error_type(f"Provider failure for {SEC_TEST_UA}; email offline@edge-research-tests.org")

    monkeypatch.setattr(edge.data, "lookup_sec_filings", failure)
    assert main(["sec-filings", "AAPL"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "EDGE error: Provider failure for [redacted]; email [redacted]" in captured.err
    assert "offline@" not in captured.err and SEC_TEST_UA not in captured.err
    assert "Traceback" not in captured.err
    assert list(sec_cli_offline.iterdir()) == []


def test_sec_cli_never_initializes_persistence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sec_cli_api: list[str],
) -> None:
    import edge.cli

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("SEC lookup attempted persistence")

    monkeypatch.setenv("EDGE_SEC_USER_AGENT", SEC_TEST_UA)
    monkeypatch.setattr(edge.cli, "RunStore", forbidden)
    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    assert edge.cli.main(["sec-filings", "AAPL"]) == 0
    assert json.loads(capsys.readouterr().out)["identity"]["sec_ticker"] == "AAPL"
    assert sec_cli_api == ["AAPL"]


def test_cli_json_serializer_is_explicit_and_lossless() -> None:
    from edge.cli import _json_default

    assert json.loads(json.dumps({
        "decimal": Decimal("1234567890123456789.123400"),
        "date": date(2024, 11, 1),
        "timestamp": datetime(2024, 11, 1, 8, 0, tzinfo=timezone(timedelta(hours=2))),
    }, default=_json_default, allow_nan=False)) == {
        "decimal": "1234567890123456789.123400", "date": "2024-11-01",
        "timestamp": "2024-11-01T06:00:00+00:00",
    }
    for unsupported in (object(), Path("not-json"), {"not-a-json-set"}):
        with pytest.raises(TypeError, match="Unsupported JSON value type"):
            json.dumps(unsupported, default=_json_default)
    for invalid in (Decimal("NaN"), Decimal("Infinity"), datetime(2024, 11, 1)):
        with pytest.raises(ValueError):
            json.dumps(invalid, default=_json_default, allow_nan=False)


@pytest.mark.parametrize("arguments, expected_code", [
    ([], 0), (["--help"], 0), (["sec-filings", "--help"], 0),
    (["doctor"], 0), (["runs"], 1), (["show", "missing-run"], 1), (["simulate"], 0),
])
def test_sec_cli_is_lazy_for_import_help_and_existing_commands(
    arguments: list[str], expected_code: int, tmp_path: Path,
) -> None:
    # Fresh process proves import laziness even when other tests imported SEC.
    script = """
import importlib.abc
import sys

class ForbidSEC(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "edge.data" or fullname.startswith(("edge.data.", "openbb", "aiohttp", "httpx")):
            raise AssertionError("Unexpected SEC/provider import: " + fullname)

def no_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise AssertionError("Unexpected external request")

sys.meta_path.insert(0, ForbidSEC())
sys.addaudithook(no_network)
import edge.cli
if len(sys.argv) > 1:
    raise SystemExit(edge.cli.main(sys.argv[1:]))
assert "edge.data" not in sys.modules
"""
    environment = os.environ.copy()
    environment.pop("EDGE_SEC_USER_AGENT", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script, *arguments],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == expected_code, result.stderr
    assert "Traceback" not in result.stderr
    if arguments in (["--help"], ["sec-filings", "--help"]):
        assert "sec-filings" in result.stdout
        assert list(tmp_path.iterdir()) == []


def test_sec_cli_missing_contact_never_imports_provider(tmp_path: Path) -> None:
    script = """
import importlib.abc
import sys

class ForbidProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "edge.data.openbb_sec_provider" or fullname.startswith("openbb"):
            raise AssertionError("Provider loaded without contact configuration")

def no_network(event, args):
    if event in ("socket.connect", "socket.getaddrinfo"):
        raise AssertionError("Unexpected external request")

sys.meta_path.insert(0, ForbidProvider())
sys.addaudithook(no_network)
from edge.cli import main
raise SystemExit(main(["sec-filings", "AAPL"]))
"""
    environment = os.environ.copy()
    environment.pop("EDGE_SEC_USER_AGENT", None)
    result = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "EDGE_SEC_USER_AGENT is not set" in result.stderr
    assert "Traceback" not in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("installed_version, expected", [
    (None, "not installed"), ("9.9.9", "untested"),
])
def test_sec_cli_optional_dependency_gate_is_explicit(
    installed_version: str | None, expected: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], sec_cli_offline: Path,
) -> None:
    from importlib.metadata import PackageNotFoundError

    from edge.cli import main
    from edge.data import openbb_sec_provider

    def fake_version(package: str) -> str:
        if installed_version is None:
            raise PackageNotFoundError(package)
        return installed_version

    def forbidden() -> None:
        pytest.fail("SEC SDK imported before dependency gate")

    monkeypatch.setenv("EDGE_SEC_USER_AGENT", SEC_TEST_UA)
    monkeypatch.setattr(openbb_sec_provider.metadata, "version", fake_version)
    monkeypatch.setattr(openbb_sec_provider, "_load_openbb", forbidden)
    assert main(["sec-filings", "AAPL"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and expected in captured.err
    assert "Traceback" not in captured.err and SEC_TEST_UA not in captured.err
    assert list(sec_cli_offline.iterdir()) == []
