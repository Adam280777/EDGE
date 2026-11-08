"""Worker validation tests; actual framework smoke only via isolated interpreter."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from test_portfolio_optimization import constraints, panel

from edge.portfolio_optimization import (
    _parse_response,
    _request,
    optimize_allocation,
    skfolio_synthetic_inputs,
    skfolio_synthetic_request,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "research" / "skfolio_worker.py"


def worker():
    spec = importlib.util.spec_from_file_location("edge_test_skfolio_worker", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validation_never_imports_framework() -> None:
    module = worker()
    before = set(sys.modules)
    validated = module.validate_request(_request(panel(), constraints()))
    assert len(validated["matrix"]) == 64
    assert not {"skfolio", "numpy", "cvxpy", "pandas"} & (set(sys.modules) - before)


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(pickle="unsafe"),
    lambda r: r["constraints"].update(solver_params={"execute": "unsafe"}),
    lambda r: r["panel"].update(assets=[]),
    lambda r: r["panel"].update(returns=[[float("nan")]] * 64),
    lambda r: r["panel"]["returns"][0].__setitem__(0, "nan"),
    lambda r: r["panel"]["returns"][0].__setitem__(0, "-1"),
    lambda r: r["panel"]["returns"][0].__setitem__(0, "1e-9999"),
    lambda r: r["panel"].update(timestamps=["2026-11-07"] * 64),
    lambda r: r["panel"].update(train_count=63),
    lambda r: r["constraints"].update(max_weights=[".1", ".1"]),
    lambda r: r["constraints"].update(previous_weights=[".9", ".9"]),
    lambda r: r["constraints"].update(max_variance="0"),
])
def test_worker_rejects_untrusted_boundary_payload(mutate) -> None:
    request = _request(panel(), constraints())
    mutate(request)
    response = worker().optimize(request)
    assert response["status"] == "error" and "result" not in response


def test_real_panel_blocked_even_when_claims_accepted() -> None:
    request = _request(panel(), constraints())
    request["panel"]["synthetic"] = False
    request["panel"]["manifest_reference"] = "claimed-accepted"
    response = worker().optimize(request)
    assert response["status"] == "blocked"
    assert "verifier" in response["reason"]


def test_duplicate_json_and_nonfinite_stdout_protocol() -> None:
    response = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], input=b'{"schema_version":1,"schema_version":1}',
        capture_output=True, timeout=10, check=True,
    )
    result = json.loads(response.stdout)
    assert result["status"] == "error"
    assert b"NaN" not in response.stdout


def test_statistical_holdout_cost_drawdown_not_trade_win_rate() -> None:
    result = worker().holdout_metrics([[.1, 0], [-.1, 0], [0, 0], [0, 0]],
                                     [.5, .3], [0, 0], [.01, 0])
    assert abs(float(result["total_return"]) - (-.01)) < 1e-12
    assert result["trade_count"] is None
    assert result["periods"] == 4
    assert result["initial_cost_fraction"] == "0.0050000000000000001"
    assert float(result["max_drawdown"]) > 0


def test_framework_absent_explicit_not_fake_estimator(monkeypatch) -> None:
    module = worker()
    def missing(name):
        raise module.importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(module.importlib.metadata, "version", missing)
    response = module.optimize(_request(panel(), constraints()))
    assert response["status"] == "blocked"
    assert "result" not in response


def isolated_python() -> Path:
    from edge.research_workers import research_runtime_readiness
    reasons = research_runtime_readiness("skfolio", project_root=ROOT)
    if reasons:
        pytest.skip("Canonical runtime unavailable: " + "; ".join(reasons))
    executable = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research" / "envs" / "skfolio" / "Scripts" / "python.exe"
    if not executable.is_file():
        pytest.skip("Canonical isolated skfolio interpreter unavailable; no staging fallback.")
    return executable


def actual(request: dict) -> dict:
    response = subprocess.run(
        [str(isolated_python()), "-I", str(SCRIPT)],
        input=json.dumps(request, allow_nan=False).encode(), capture_output=True,
        timeout=90, check=True,
    )
    return json.loads(response.stdout)


def test_actual_skfolio_constrained_fit_and_holdout_no_leakage() -> None:
    request = _request(panel(), constraints())
    response = actual(request)
    assert response["status"] == "ok", response
    allocation = _parse_response(response, request, panel(), constraints())
    assert allocation.framework_executed and allocation.solver_status == "optimal"
    assert allocation.estimated_turnover <= constraints().max_turnover
    altered = _request(panel(), constraints())
    altered["panel"]["returns"][40:] = [[".5", "-.5"]] * 24
    second = actual(altered)
    assert second["status"] == "ok", second
    assert response["result"]["weights"] == second["result"]["weights"]
    assert response["result"]["estimated_period_variance"] == second["result"]["estimated_period_variance"]
    assert response["result"]["holdout"] != second["result"]["holdout"]


def test_actual_infeasible_constraint_never_returns_proxy_weights() -> None:
    request = _request(panel(), constraints())
    request["constraints"]["max_turnover"] = "0"
    response = actual(request)
    assert response["status"] == "error" and "result" not in response
    assert response["reason"].startswith("Optimization failed"), response


def test_actual_shared_runner_three_asset_fixture() -> None:
    isolated_python()
    fixture, limits = skfolio_synthetic_inputs("minimum-variance-three-assets")
    assert skfolio_synthetic_request("minimum-variance-three-assets") == _request(fixture, limits)
    result = optimize_allocation(fixture, limits, project_root=ROOT, timeout_seconds=90)
    assert result.status == "ok", result.reasons
    assert result.framework_executed and len(result.target_weights) == 3
    assert result.solver_status == "optimal"
    assert result.estimated_period_variance <= limits.max_variance
