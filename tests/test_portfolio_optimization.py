"""Typed adapter/translation tests. Mock transport is not framework evidence."""

import hashlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge.decision_contracts import (
    DecisionInputError,
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
)
from edge.portfolio_optimization import (
    AllocationAsset,
    AllocationConstraints,
    ReturnPanel,
    TranslationAsset,
    _request,
    allocation_readiness,
    optimize_allocation,
    translate_targets,
)

D = Decimal
ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def panel() -> ReturnPanel:
    assets = tuple(AllocationAsset(name, name, "SYNTHETIC", "GBP", "Generated fixture identity")
                   for name in ("FIX_A", "FIX_B"))
    rows = tuple(
        (D((i * 7) % 19 - 9) / 1000 + D(".001"),
         D((i * 3) % 13 - 6) / 2000 + D(".0005"))
        for i in range(64)
    )
    return ReturnPanel(
        assets, tuple(NOW + timedelta(days=i) for i in range(64)), rows, 40,
        True, "Deterministic generated fixture", "Synthetic aligned daily periods",
        "Synthetic generated; no vendor license", "synthetic-not-applicable",
        "synthetic-fixture-v1",
    )


def constraints() -> AllocationConstraints:
    return AllocationConstraints(
        (D(0), D(0)), (D(".8"), D(".8")), (D(0), D(0)),
        D(".8"), D(1), D(".01"), D("-.1"), (D(".001"), D(".001")), 20, D(".01"),
    )


def mock_response(request: dict) -> dict:
    metrics = {
        "total_return": ".01", "max_drawdown": ".01", "mean_period_return": ".001",
        "period_volatility": ".01", "mean_over_volatility": ".1",
        "positive_period_fraction": ".5", "average_positive_period": ".01",
        "average_negative_period": "-.01", "period_profit_factor": "1",
        "initial_cost_fraction": ".0008", "periods": 24, "trade_count": None,
    }
    return {
        "schema_version": 1, "framework": "skfolio", "status": "ok",
        "result": {
            "package_version": "1.12.5", "solver": "CLARABEL", "solver_status": "optimal",
            "panel_digest": hashlib.sha256(json.dumps(request["panel"], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "weights": [".4", ".4"], "estimated_period_variance": ".0000032",
            "estimated_net_period_return": ".00076", "estimated_turnover": ".8",
            "estimated_one_off_cost_fraction": ".0008",
            "covariance": [[".00001", "0"], ["0", ".00001"]],
            "expected_asset_returns": [".001", ".001"],
            "train_count": 40, "holdout_count": 24,
            "train_end": request["panel"]["timestamps"][39],
            "holdout_start": request["panel"]["timestamps"][40],
            "holdout": metrics, "naive_holdout": metrics, "naive_weights": [".4", ".4"],
            "limitations": ["Mock transport test only; not actual framework evidence."],
        },
    }


def install_mock(monkeypatch, mutate=None):
    def runner(root, request, timeout):
        response = mock_response(request)
        if mutate:
            mutate(response)
        return SimpleNamespace(status="ok", response=response, error=None)
    monkeypatch.setattr("edge.portfolio_optimization._run", runner)


def test_real_data_claim_never_promoted_or_worker_run(monkeypatch) -> None:
    def forbidden(*args):
        pytest.fail("Real panel must not reach worker without accepted manifest verifier.")
    monkeypatch.setattr("edge.portfolio_optimization._run", forbidden)
    real = replace(panel(), synthetic=False, corporate_actions="User claims adjusted",
                   manifest_reference="user-claims-accepted")
    result = optimize_allocation(real, constraints(), project_root=ROOT, timeout_seconds=20)
    assert result.status == "blocked" and not result.framework_executed
    assert not result.target_weights
    assert "manifest verifier" in result.reasons[0]


def test_unavailable_explicit_reason_no_fallback(monkeypatch) -> None:
    def unavailable(*args):
        raise ImportError("Runner not present yet")
    monkeypatch.setattr("edge.portfolio_optimization._run", unavailable)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    assert result.status == "blocked"
    assert not result.target_weights and not result.framework_executed


def test_worker_blocker_reason_preserved_from_shared_runner(monkeypatch) -> None:
    monkeypatch.setattr("edge.portfolio_optimization._run", lambda *args: SimpleNamespace(
        status="blocked", response={"schema_version": 1, "framework": "skfolio",
                                   "status": "blocked", "reason": "Pinned skfolio required."},
        error=None,
    ))
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    assert result.status == "blocked" and result.reasons == ("Pinned skfolio required.",)


def test_shared_transport_digest_validated_without_relaxing_envelope(monkeypatch) -> None:
    def runner(root, request, timeout):
        response = mock_response(request)
        response["request_sha256"] = hashlib.sha256(
            json.dumps(request, allow_nan=False).encode("utf-8")
        ).hexdigest()
        return SimpleNamespace(status="ok", response=response, error=None)
    monkeypatch.setattr("edge.portfolio_optimization._run", runner)
    assert optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10).status == "ok"
    install_mock(monkeypatch, lambda response: response.update(request_sha256="wrong"))
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    assert result.status == "error" and not result.target_weights


def test_mock_transport_strict_trace_and_no_claim_about_framework_test(monkeypatch) -> None:
    install_mock(monkeypatch)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    assert result.status == "ok" and result.target_weights == (D(".4"), D(".4"))
    assert result.train_end < result.holdout_start
    assert result.estimated_period_variance == D(".0000032")
    assert "Mock" in result.limitations[0]
    with pytest.raises(FrozenInstanceError):
        result.status = "error"


@pytest.mark.parametrize("mutate", [
    lambda r: r["result"].update(weights=["nan", ".4"]),
    lambda r: r["result"].update(weights=[".8", ".8"]),
    lambda r: r["result"].update(solver_status="optimal_inaccurate"),
    lambda r: r["result"].update(package_version="other"),
    lambda r: r["result"].update(panel_digest="wrong"),
    lambda r: r["result"].update(train_end="2026-11-07T00:00:00+00:00"),
    lambda r: r["result"].update(estimated_turnover=".1"),
    lambda r: r["result"].update(estimated_period_variance=".001"),
    lambda r: r["result"].update(estimated_one_off_cost_fraction=".1"),
    lambda r: r["result"].update(weights=[.4, .4]),
    lambda r: r["result"].update(covariance=[["-.1", "0"], ["0", ".1"]]),
    lambda r: r["result"]["holdout"].update(trade_count=2),
    lambda r: r.update(extra="config"),
])
def test_untrusted_solver_payload_rejected(monkeypatch, mutate) -> None:
    install_mock(monkeypatch, mutate)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    assert result.status == "error"
    assert not result.framework_executed and not result.target_weights


@pytest.mark.parametrize("changes", [
    {"returns": ((D(".1"),),) * 64},
    {"returns": ((.1, D(".1")),) * 64},
    {"timestamps": (NOW,) * 64}, {"train_count": 63}, {"train_count": True},
    {"source": ""}, {"assets": (panel().assets[0],) * 2},
    {"returns": ((D("NaN"), D(".1")),) * 64},
    {"reporting_currency": "USD"}, {"corporate_actions": "unknown"},
])
def test_panel_safeguards(changes: dict) -> None:
    with pytest.raises(DecisionInputError):
        replace(panel(), **changes)


@pytest.mark.parametrize("changes", [
    {"max_weights": (D(".1"), D(".1"))}, {"min_weights": (D(".9"), D(0))},
    {"previous_weights": (D(".9"), D(".9"))}, {"max_turnover": D(3)},
    {"max_variance": D(0)}, {"min_net_return": .1},
    {"holding_periods": True}, {"one_off_costs": (D(0),)},
])
def test_no_implicit_constraints_or_coercion(changes: dict) -> None:
    with pytest.raises(DecisionInputError):
        replace(constraints(), **changes)


def translation_assets() -> tuple[TranslationAsset, ...]:
    provenance = ManualProvenance("Synthetic manual quote", NOW)
    return tuple(TranslationAsset(
        identity.asset_id, 0, PriceQuote(D(100), "GBP", NOW, provenance), None,
        FinanceMoney(D(1), "GBP"), FinanceMoney(D(1), "GBP"), D(".2"), D(".1"),
    ) for identity in panel().assets)


def test_cash_cost_rounding_recommendations_not_mutation(monkeypatch) -> None:
    install_mock(monkeypatch)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    holdings = translation_assets()
    translated = translate_targets(
        result, holdings, constraints=constraints(), portfolio_value_gbp=FinanceMoney(D(10000), "GBP"),
        available_cash=(FinanceMoney(D(10000), "GBP"),), max_cost_gbp=FinanceMoney(D(30), "GBP"),
        not_before=NOW, as_of=NOW,
    )
    assert translated.status == "ok"
    assert tuple(r.target_shares for r in translated.recommendations) == (39, 39)
    assert translated.estimated_cost_gbp.amount == D("17.6")
    assert translated.rounding_cash_gbp.amount == D("2182.4")
    assert all(asset.current_shares == 0 for asset in holdings)


@pytest.mark.parametrize("change,reason", [
    ({"available_cash": (FinanceMoney(D(1), "GBP"),)}, "cash"),
    ({"available_cash": (FinanceMoney(D(10000), "USD"),)}, "cash"),
    ({"max_cost_gbp": FinanceMoney(D(1), "GBP")}, "cost"),
    ({"not_before": NOW + timedelta(seconds=1), "as_of": NOW + timedelta(seconds=1)}, "dates"),
    ({"constraints": replace(constraints(), min_weights=(D(".4"), D(".4")))}, "rounding"),
    ({"constraints": replace(constraints(), max_variance=D(".0000001"))}, "risk"),
    ({"constraints": replace(constraints(), previous_weights=(D(".1"), D(0)))}, "holdings"),
])
def test_translation_cannot_silently_relax_cash_risk_dates_constraints(monkeypatch, change, reason) -> None:
    install_mock(monkeypatch)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    kwargs = {
        "constraints": constraints(), "portfolio_value_gbp": FinanceMoney(D(10000), "GBP"),
        "available_cash": (FinanceMoney(D(10000), "GBP"),), "max_cost_gbp": FinanceMoney(D(30), "GBP"),
        "not_before": NOW, "as_of": NOW,
    }
    if "constraints" in change:
        # Isolate post-floor guards from the independent changed-constraint guard.
        result = replace(result, constraints=change["constraints"])
    translated = translate_targets(result, translation_assets(), **(kwargs | change))
    assert translated.status == "blocked" and not translated.recommendations
    assert reason in translated.reasons[0]


def test_translation_gbp_pence_and_usd_direction(monkeypatch) -> None:
    install_mock(monkeypatch)
    result = optimize_allocation(panel(), constraints(), project_root=ROOT, timeout_seconds=10)
    asset = translation_assets()[0]
    pence = replace(asset, quote=replace(asset.quote, amount=D(10000), unit="GBp"),
                    spread_per_share=D(20), slippage_per_side=D(10))
    kwargs = {
        "constraints": constraints(), "portfolio_value_gbp": FinanceMoney(D(10000), "GBP"),
        "available_cash": (FinanceMoney(D(10000), "GBP"),), "max_cost_gbp": FinanceMoney(D(30), "GBP"),
        "not_before": NOW, "as_of": NOW,
    }
    translated = translate_targets(result, (pence, translation_assets()[1]), **kwargs)
    assert translated.recommendations[0].target_shares == 39
    with pytest.raises(DecisionInputError):
        replace(asset, quote=replace(asset.quote, unit="USD"))
    with pytest.raises(DecisionInputError):
        replace(asset, fx_to_gbp=FxRate("USD", "GBP", D(".8"), NOW, asset.quote.provenance))


def test_request_is_typed_json_no_config_paths_or_pickle() -> None:
    request = _request(panel(), constraints())
    encoded = json.dumps(request, allow_nan=False)
    assert "pickle" not in encoded and "solver_params" not in encoded
    assert isinstance(request["panel"]["returns"][0][0], str)


def test_readiness_never_runs_or_imports_heavy_framework(tmp_path, monkeypatch) -> None:
    def forbidden(*args):
        pytest.fail("Readiness must not run a subprocess.")
    monkeypatch.setattr("edge.portfolio_optimization._run", forbidden)
    monkeypatch.setattr(
        "edge.research_workers.research_runtime_readiness",
        lambda framework, *, project_root: ("Canonical runtime attestation unavailable.",),
    )
    reasons = allocation_readiness(panel(), project_root=tmp_path)
    assert "Canonical runtime attestation unavailable." in reasons
    assert "explicit" in reasons[-1].lower()
    assert list(tmp_path.iterdir()) == []
