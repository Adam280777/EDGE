"""Isolated skfolio 1.12.5 worker. Strict JSON only; no paths/pickle/code input.

The chronological holdout is a statistical buy-and-hold comparison, NOT a trade
simulator. Production execution/backtesting belongs to NautilusTrader.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import json
import math
import sys
from datetime import datetime
from decimal import Decimal
from itertools import pairwise

MAX_REQUEST_BYTES = 2_000_000
VERSION = "1.12.5"
TOLERANCE = 1e-7


def _keys(value: object, expected: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Invalid or unexpected JSON fields.")
    return value


def _number(value: object, *, low: float, high: float) -> float:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Numbers must be bounded exact decimal strings.")
    try:
        exact = Decimal(value)
        result = float(exact)
    except (ValueError, ArithmeticError):
        raise ValueError("Invalid decimal number.") from None
    if not exact.is_finite() or not math.isfinite(result) or not low <= result <= high:
        raise ValueError("Nonfinite or out-of-bounds number.")
    if exact != 0 and result == 0:
        raise ValueError("Decimal underflows solver precision.")
    return result


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Aware ISO timestamps required.")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("Aware ISO timestamps required.")
    return result


def _label(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError("Bounded nonempty identity/provenance label required.")
    if any(ord(char) < 32 for char in value):
        raise ValueError("Control characters prohibited.")
    return value


def _vector(value: object, count: int, *, low: float = 0, high: float = 1) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("Asset vector dimensions differ.")
    return [_number(item, low=low, high=high) for item in value]


def validate_request(request: object) -> dict:
    """Both synthetic identity and all numerical/temporal bounds are checked."""
    request = _keys(request, {
        "schema_version", "framework", "operation", "panel", "constraints",
    })
    if type(request["schema_version"]) is not int or request["schema_version"] != 1:
        raise ValueError("Unsupported schema version.")
    if request["framework"] != "skfolio" or request["operation"] != "target-allocation":
        raise ValueError("Unsupported worker operation.")
    panel = _keys(request["panel"], {
        "synthetic", "assets", "timestamps", "returns", "calendar", "source",
        "rights_reference", "corporate_actions", "reporting_currency", "train_count",
        "manifest_reference",
    })
    if panel["synthetic"] is not True:
        raise PermissionError("Real histories blocked: independently accepted panel manifest verifier absent.")
    if panel["reporting_currency"] != "GBP":
        raise ValueError("Panel must contain GBP total returns, not mixed local returns.")
    for field in ("calendar", "source", "rights_reference", "corporate_actions", "manifest_reference"):
        _label(panel[field])
    if panel["corporate_actions"] != "synthetic-not-applicable":
        raise ValueError("Synthetic corporate-action declaration required.")
    assets = panel["assets"]
    if not isinstance(assets, list) or not 2 <= len(assets) <= 30:
        raise ValueError("Panel needs 2 to 30 assets.")
    names = []
    for asset in assets:
        _keys(asset, {"asset_id", "symbol", "exchange", "currency", "identity_reference"})
        for field in ("asset_id", "symbol", "exchange", "identity_reference"):
            _label(asset[field])
        if asset["currency"] not in ("GBP", "USD"):
            raise ValueError("Asset cash currency must be GBP or USD.")
        names.append(asset["asset_id"])
    if len(set(names)) != len(names) or len({
        (asset["exchange"], asset["symbol"]) for asset in assets
    }) != len(assets):
        raise ValueError("Duplicate asset identity.")
    timestamps = panel["timestamps"]
    if not isinstance(timestamps, list) or not 12 <= len(timestamps) <= 2000:
        raise ValueError("Panel needs 12 to 2000 aligned periods.")
    dates = [_timestamp(item) for item in timestamps]
    if any(a >= b for a, b in pairwise(dates)):
        raise ValueError("Timestamps must be strictly increasing and unique.")
    train_count = panel["train_count"]
    if type(train_count) is not int or not max(8, len(assets) + 2) <= train_count <= len(dates) - 4:
        raise ValueError("Explicit chronological training split needs adequate train and holdout rows.")
    raw_returns = panel["returns"]
    if not isinstance(raw_returns, list) or len(raw_returns) != len(dates):
        raise ValueError("Panel rows must exactly match timestamps; no missing data.")
    matrix = [_vector(row, len(assets), low=-0.999999, high=10) for row in raw_returns]
    constraints = _keys(request["constraints"], {
        "min_weights", "max_weights", "previous_weights", "invested_budget",
        "max_turnover", "max_variance", "min_net_return", "one_off_costs",
        "holding_periods", "max_estimated_cost_fraction",
    })
    vectors = {field: _vector(constraints[field], len(assets)) for field in (
        "min_weights", "max_weights", "previous_weights", "one_off_costs",
    )}
    if any(a > b for a, b in zip(vectors["min_weights"], vectors["max_weights"], strict=True)):
        raise ValueError("Minimum weights exceed maximum weights.")
    budget = _number(constraints["invested_budget"], low=0.000001, high=1)
    if sum(vectors["min_weights"]) > budget + TOLERANCE or sum(vectors["max_weights"]) < budget - TOLERANCE:
        raise ValueError("Weight bounds cannot satisfy the explicit invested budget.")
    if sum(vectors["previous_weights"]) > 1 + TOLERANCE:
        raise ValueError("Previous weights imply leverage.")
    turnover = _number(constraints["max_turnover"], low=0, high=2)
    variance = _number(constraints["max_variance"], low=0.000000000001, high=100)
    net_return = _number(constraints["min_net_return"], low=-0.999999, high=10)
    cost_cap = _number(constraints["max_estimated_cost_fraction"], low=0, high=1)
    holding = constraints["holding_periods"]
    if type(holding) is not int or not 1 <= holding <= 2000:
        raise ValueError("Explicit investment duration must be 1 to 2000 periods.")
    digest = hashlib.sha256(json.dumps(panel, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "panel": panel, "matrix": matrix, "vectors": vectors, "budget": budget,
        "turnover": turnover, "variance": variance, "net_return": net_return,
        "holding": holding, "cost_cap": cost_cap, "digest": digest,
    }


def _finite(value: object) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Worker produced nonfinite output.")
    return result


def _string(value: object) -> str:
    return format(_finite(value), ".17g")


def holdout_metrics(matrix, weights, previous, costs) -> dict:
    """Buy-and-hold from unit NAV; explicit one-off rebalance cost at time zero."""
    count = len(weights)
    wealth = [1.0] * count
    cost = sum(costs[i] * abs(weights[i] - previous[i]) for i in range(count))
    if cost >= 1:
        raise ValueError("Estimated initial cost consumes all NAV.")
    cash = 1 - sum(weights)
    previous_nav = 1.0
    peak = 1.0
    nav_returns = []
    max_drawdown = 0.0
    for row in matrix:
        for i in range(count):
            wealth[i] *= 1 + row[i]
        nav = cash + sum(weights[i] * wealth[i] for i in range(count)) - cost
        if nav <= 0:
            raise ValueError("Holdout NAV is nonpositive.")
        nav_returns.append(nav / previous_nav - 1)
        previous_nav = nav
        peak = max(peak, nav)
        max_drawdown = max(max_drawdown, 1 - nav / peak)
    mean = sum(nav_returns) / len(nav_returns)
    variance = sum((item - mean) ** 2 for item in nav_returns) / (len(nav_returns) - 1)
    winners = [item for item in nav_returns if item > 0]
    losers = [item for item in nav_returns if item < 0]
    return {
        "total_return": _string(previous_nav - 1),
        "max_drawdown": _string(max_drawdown),
        "mean_period_return": _string(mean),
        "period_volatility": _string(math.sqrt(variance)),
        "mean_over_volatility": _string(mean / math.sqrt(variance)) if variance > 0 else None,
        "positive_period_fraction": _string(len(winners) / len(nav_returns)),
        "average_positive_period": _string(sum(winners) / len(winners)) if winners else None,
        "average_negative_period": _string(sum(losers) / len(losers)) if losers else None,
        "period_profit_factor": _string(sum(winners) / -sum(losers)) if losers else None,
        "initial_cost_fraction": _string(cost),
        "periods": len(matrix),
        "trade_count": None,
    }


def optimize(request: object) -> dict:
    envelope = {"schema_version": 1, "framework": "skfolio"}
    try:
        data = validate_request(request)
    except PermissionError as error:
        return {**envelope, "status": "blocked", "reason": str(error)}
    except (ValueError, TypeError, ArithmeticError) as error:
        return {**envelope, "status": "error", "reason": str(error)}
    try:
        if importlib.metadata.version("skfolio") != VERSION:
            return {**envelope, "status": "blocked", "reason": "Pinned skfolio 1.12.5 required."}
        # These imports exist only inside the explicit isolated worker operation.
        import cvxpy as cp
        import numpy as np
        import pandas as pd
        from skfolio import RiskMeasure
        from skfolio.optimization import MeanRisk, ObjectiveFunction

        if "CLARABEL" not in cp.installed_solvers():
            return {**envelope, "status": "blocked", "reason": "CLARABEL solver unavailable."}
        panel = data["panel"]
        vectors = data["vectors"]
        frame = pd.DataFrame(data["matrix"], columns=[asset["asset_id"] for asset in panel["assets"]])
        training = frame.iloc[:panel["train_count"]]
        # Holdout is NEVER supplied to fit or parameter/constraint selection.
        one_off = np.asarray(vectors["one_off_costs"])
        previous = np.asarray(vectors["previous_weights"])

        def cost_constraint(w):
            return cp.sum(cp.multiply(one_off, cp.abs(w - previous))) <= data["cost_cap"]

        model = MeanRisk(
            objective_function=ObjectiveFunction.MINIMIZE_RISK,
            risk_measure=RiskMeasure.VARIANCE,
            min_weights=vectors["min_weights"], max_weights=vectors["max_weights"],
            budget=data["budget"], previous_weights=vectors["previous_weights"],
            max_turnover=data["turnover"], max_variance=data["variance"],
            min_return=data["net_return"],
            transaction_costs=one_off / data["holding"],
            add_constraints=cost_constraint,
            solver="CLARABEL",
            solver_params={"tol_gap_abs": 1e-10, "tol_gap_rel": 1e-10, "tol_feas": 1e-10},
            save_problem=True, raise_on_failure=True,
        )
        model.fit(training)
        solver_status = model.problem_.status
        if solver_status != "optimal":
            return {**envelope, "status": "blocked", "reason": "Solver did not return optimal status."}
        weights = [_finite(item) for item in model.weights_]
        if len(weights) != len(panel["assets"]):
            raise ValueError("Solver asset dimensions differ.")
        for i, weight in enumerate(weights):
            if not vectors["min_weights"][i] - TOLERANCE <= weight <= vectors["max_weights"][i] + TOLERANCE:
                raise ValueError("Solver weight violates explicit user bounds.")
        if abs(sum(weights) - data["budget"]) > TOLERANCE:
            raise ValueError("Solver budget residual exceeds tolerance.")
        turnover = sum(abs(a - b) for a, b in zip(weights, vectors["previous_weights"], strict=True))
        initial_cost = sum(cost * abs(a - b) for cost, a, b in zip(
            vectors["one_off_costs"], weights, vectors["previous_weights"], strict=True
        ))
        distribution = model.prior_estimator_.return_distribution_
        variance = _finite(np.asarray(weights) @ distribution.covariance @ np.asarray(weights))
        gross_return = _finite(distribution.mu @ np.asarray(weights))
        net_return = gross_return - initial_cost / data["holding"]
        if turnover > data["turnover"] + TOLERANCE or variance > data["variance"] + TOLERANCE:
            raise ValueError("Solver risk/turnover constraints violated.")
        if initial_cost > data["cost_cap"] + TOLERANCE or net_return < data["net_return"] - TOLERANCE:
            raise ValueError("Solver cost/net return constraint violated.")
        holdout = data["matrix"][panel["train_count"]:]
        naive = [data["budget"] / len(weights)] * len(weights)
        return {
            **envelope, "status": "ok",
            "result": {
                "package_version": VERSION, "solver": "CLARABEL", "solver_status": solver_status,
                "panel_digest": data["digest"], "weights": [_string(w) for w in weights],
                "estimated_period_variance": _string(variance),
                "estimated_net_period_return": _string(net_return),
                "estimated_turnover": _string(turnover),
                "estimated_one_off_cost_fraction": _string(initial_cost),
                "covariance": [[_string(item) for item in row] for row in distribution.covariance],
                "expected_asset_returns": [_string(item) for item in distribution.mu],
                "train_count": panel["train_count"], "holdout_count": len(holdout),
                "train_end": panel["timestamps"][panel["train_count"] - 1],
                "holdout_start": panel["timestamps"][panel["train_count"]],
                "holdout": holdout_metrics(holdout, weights, vectors["previous_weights"], vectors["one_off_costs"]),
                "naive_holdout": holdout_metrics(holdout, naive, vectors["previous_weights"], vectors["one_off_costs"]),
                "naive_weights": [_string(w) for w in naive],
                "limitations": [
                    "Synthetic fixture, not accepted real-market performance or advice.",
                    "Chronological holdout untouched by fit; user-selected universe may have selection bias.",
                    "Equal-weight benchmark may violate user constraints; it is not a recommendation.",
                    "Empirical mean/covariance estimates are uncertain, not promised returns/risk.",
                    "Holdout buy-and-hold statistical comparison includes user one-off costs, zero cash yield.",
                    "No dividends/FX/corporate actions beyond input total returns; ongoing fees unmodeled.",
                    "No trades, fill simulation, annualization or trading win rate; Nautilus required for simulations.",
                    "No holdout tuning, walk-forward acceptance or multiple-testing correction performed.",
                ],
            },
        }
    except ImportError:
        return {**envelope, "status": "blocked", "reason": "Isolated skfolio dependencies unavailable."}
    except importlib.metadata.PackageNotFoundError:
        return {**envelope, "status": "blocked", "reason": "Pinned skfolio distribution unavailable."}
    except (ValueError, TypeError, ArithmeticError, RuntimeError, cp.error.SolverError) as error:
        # No fallback/proxy estimator; infeasible/failed solve produces no weights.
        return {**envelope, "status": "error", "reason": f"Optimization failed ({type(error).__name__}); no allocation."}


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys.")
        result[key] = value
    return result


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("Request exceeds 2 MB.")
        request = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
        # Framework logs must never pollute the single JSON stdout response.
        with contextlib.redirect_stdout(sys.stderr):
            response = optimize(request)
    except (ValueError, UnicodeError, RecursionError):
        response = {"schema_version": 1, "framework": "skfolio",
                    "status": "error", "reason": "Invalid bounded UTF-8 JSON request."}
    print(json.dumps(response, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
