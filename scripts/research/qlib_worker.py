"""Actual in-memory Qlib DatasetH/LinearModel fit on a fixed synthetic panel."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.metadata
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from worker_protocol import execute

FIXTURE_ID = "qlib-linear-three-assets-v1"


def run() -> dict:
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import pandas as pd
        from qlib.contrib.model.linear import LinearModel
        from qlib.data.dataset import DatasetH
        from qlib.data.dataset.handler import DataHandlerLP

        dates = pd.date_range("2000-01-01", periods=60, freq="D")
        index = pd.MultiIndex.from_product(
            [dates, ["SYNTH_A", "SYNTH_B", "SYNTH_C"]],
            names=["datetime", "instrument"],
        )
        rows = []
        for day in range(60):
            for asset in range(3):
                first = float(np.sin(day / 7 + asset))
                second = float(np.cos(day / 11 + asset / 3))
                label = .02 * first - .01 * second + .001 * asset
                rows.append([first, second, float(asset), label])
        frame = pd.DataFrame(
            rows, index=index,
            columns=pd.MultiIndex.from_tuples([
                ("feature", "first"), ("feature", "second"),
                ("feature", "asset"), ("label", "synthetic_target"),
            ]),
        )
        handler = DataHandlerLP.from_df(frame)
        dataset = DatasetH(handler=handler, segments={
            "train": (dates[0], dates[39]),
            "valid": (dates[40], dates[49]),
            "test": (dates[50], dates[59]),
        })
        model = LinearModel(estimator="ols", fit_intercept=True, include_valid=False)
        model.fit(dataset)
        predictions = model.predict(dataset, segment="test")
        test_labels = dataset.prepare("test", col_set="label").iloc[:, 0]
        if len(predictions) != 30 or not np.isfinite(predictions.to_numpy()).all():
            raise ValueError("Invalid Qlib synthetic output.")
        mse = float(np.mean((predictions.to_numpy() - test_labels.to_numpy()) ** 2))
        # Constant training-target mean baseline uses training rows only.
        baseline = float(frame.loc[(slice(dates[0], dates[39]), slice(None)), ("label", "synthetic_target")].mean())
        baseline_mse = float(np.mean((test_labels.to_numpy() - baseline) ** 2))
        # Holdout perturbation must not affect learned coefficients.
        changed = frame.copy()
        changed.loc[(slice(dates[50], dates[59]), slice(None)), ("label", "synthetic_target")] += 100
        alternate = DatasetH(handler=DataHandlerLP.from_df(changed), segments=dataset.segments)
        alternate_model = LinearModel(estimator="ols", fit_intercept=True, include_valid=False)
        alternate_model.fit(alternate)
        if not np.array_equal(model.coef_, alternate_model.coef_):
            raise ValueError("Synthetic holdout affected training.")
    return {
        "framework_executed": True, "engine_version": importlib.metadata.version("pyqlib"),
        "model": "qlib.contrib.model.linear.LinearModel(ols)",
        "input_rows": 180, "train_rows": 120, "validation_rows": 30, "test_rows": 30,
        "fixture_sha256": hashlib.sha256(frame.to_json().encode()).hexdigest(),
        "holdout_training_independent": True,
        "synthetic_test_mse": repr(mse), "constant_training_mean_baseline_mse": repr(baseline_mse),
        "predictions": [repr(float(value)) for value in predictions.to_numpy()],
        "interpretation": "Manufactured linear targets; near-perfect fit is expected, not market predictive evidence.",
    }


if __name__ == "__main__":
    execute("qlib", FIXTURE_ID, run)
