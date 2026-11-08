"""Redesigned Discovery / Research lab panes render engine values only, with provenance."""

from datetime import UTC, datetime
from decimal import Decimal

from streamlit.testing.v1 import AppTest

HASH_A, HASH_B, HASH_C = "a" * 64, "b" * 64, "c" * 64


def _envelope(framework, fixture, result, package):
    return {
        "schema_version": 1, "framework": framework, "status": "ok", "synthetic": True,
        "fixture_id": fixture, "request_sha256": HASH_A, "limitations": ["Synthetic only."],
        "runtime": {"python_version": "3.12.16", "primary_package": package,
                    "primary_version": "test", "worker_sha256": HASH_B,
                    "local_consistency_verified": True, "signed_security_attestation": False},
        "result": result,
    }


RESULTS = {
    "qlib": ("qlib-linear-three-assets-v1", "pyqlib", {
        "framework_executed": True, "engine_version": "0.9", "fixture_sha256": HASH_C,
        "input_rows": 180, "train_rows": 120, "validation_rows": 30, "test_rows": 30,
        "holdout_training_independent": True, "synthetic_test_mse": "0.0001",
        "constant_training_mean_baseline_mse": "0.5",
        "predictions": [repr(0.1 * i) for i in range(30)], "interpretation": "Manufactured.",
    }),
    "kronos": ("kronos-mini-ohlcv-v1", "kronos", {
        "framework_executed": True, "engine_version": "abc", "fixture_sha256": HASH_C,
        "input_rows": 32, "prediction_rows": 2, "device": "cpu", "torch_version": "2",
        "predictions": [["100.0", "101.0", "99.0", "100.5", "1000.0", "100500.0"],
                        ["100.5", "102.0", "99.5", "101.0", "1010.0", "102010.0"]],
        "models": [{"payload_verified": True}], "interpretation": "Unconstrained demo.",
    }),
    "skfolio": ("minimum-variance-three-assets", "skfolio", {
        "package_version": "0.1", "panel_digest": HASH_C, "limitations": ["No market data."],
        "solver": "CLARABEL", "solver_status": "optimal", "weights": ["0.5", "0.3", "0.2"],
        "naive_weights": ["0.333", "0.333", "0.333"], "estimated_period_variance": "0.0004",
        "estimated_net_period_return": "0.001", "estimated_turnover": "0.1",
        "estimated_one_off_cost_fraction": "0.0001",
        "covariance": [["0.1", "0.01", "0"], ["0.01", "0.2", "0"], ["0", "0", "0.3"]],
        "expected_asset_returns": ["0.01", "0.02", "0.03"], "train_count": 40, "holdout_count": 20,
        "holdout_start": "2000-02-10", "holdout": {"total_return": "0.02", "max_drawdown": "0.01"},
        "naive_holdout": {"total_return": "0.01", "max_drawdown": "0.02"},
    }),
}


def test_accepted_synthetic_results_render_charts_digests_and_history(tmp_path):
    import time

    from edge.dashboard_research_lab import _Job

    for framework, (fixture, package, result) in RESULTS.items():
        job = _Job(framework, fixture)
        job.status = "ok"
        job.response = _envelope(framework, fixture, result, package)
        job.finished_at = time.monotonic() - 60
        job.finished_wall = datetime.now(UTC)
        job.done.set()
        root = tmp_path / "data"
        app = AppTest.from_string(
            "from pathlib import Path\n"
            "from edge.dashboard_research_lab import render\n"
            f"render(Path({str(root)!r}))\n",
            default_timeout=30,
        )
        app.session_state["edge_research_lab_job"] = job
        app.run()
        assert not app.exception, framework
        texts = [item.value for item in app.text]
        assert f"Engine: {framework} / fixed fixture: {fixture}" in texts
        assert sum("SHA-256: " in item for item in texts) == 3
        assert any("Synthetic experiment completed" in item.value for item in app.success)
        markdown = " ".join(item.value for item in app.markdown)
        assert "SYNTHETIC · NOT MARKET DATA" in markdown
        assert app.get("vega_lite_chart"), framework
        assert len(app.session_state["edge_research_lab_history"]) == 1
        assert len(app.button) == 2
        assert not root.exists()
        assert list(tmp_path.iterdir()) == []


def test_discovery_screen_funnel_and_evidence(tmp_path):
    from edge.decision_contracts import ManualProvenance
    from edge.discovery import CompanyObservations, FundamentalObservation
    from edge.research_workflows import CompanyIdentity

    now = datetime.now(UTC)
    source = ManualProvenance("User statement", now)
    companies = tuple(
        CompanyObservations(CompanyIdentity(sym, f"{sym} plc", "LSE", "GBP"), (
            FundamentalObservation("margin", value, "fraction", "FY2020", now, source, "claimed-reported"),
        ))
        for sym, value in (("AAA", Decimal("0.3")), ("BBB", Decimal("0.05")), ("CCC", None))
    )
    root = tmp_path / "data"
    app = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_discovery import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    )
    app.session_state[f"edge_discovery_{root.resolve()}"] = companies
    app.run()
    assert not app.exception
    assert app.get("vega_lite_chart")  # timeline + coverage of declared observations
    app.radio(key="edge_discovery_task").set_value("Screen").run()
    for label, value in (
        ("Filter metric / exact label", "margin"), ("Filter unit / exact label", "fraction"),
        ("Required reporting period / exact label", "FY2020"), ("Minimum / optional decimal", "0.1"),
        ("Oldest allowed observation / ISO timestamp with UTC offset", "2020-01-01T00:00:00Z"),
    ):
        next(item for item in app.text_input if item.label == label).set_value(value)
    next(item for item in app.selectbox if item.label == "Rank by the same metric").set_value("higher")
    next(item for item in app.button if item.label == "Run deterministic manual screen").click().run()
    assert not app.exception
    texts = [item.value for item in app.text]
    assert sum("included: True" in item for item in texts) == 1
    assert sum("included: False" in item for item in texts) == 2
    assert len(app.get("vega_lite_chart")) >= 3  # funnel, exclusion donut, ranked values
    assert not root.exists()
