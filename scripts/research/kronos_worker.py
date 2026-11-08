"""Explicit fixed CPU Kronos-mini fixture; all local payloads verified before loading."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
from pathlib import Path

# -I deliberately excludes workspace/PYTHONPATH; only this fixed resource dir.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from worker_protocol import execute

FIXTURE_ID = "kronos-mini-ohlcv-v1"


def run() -> dict:
    manifest = json.loads((HERE / "kronos-artifacts.json").read_text(encoding="utf-8"))
    model_root = Path(os.environ["LOCALAPPDATA"]) / "EDGE" / "research" / "models"
    for model in manifest["models"]:
        directory = model_root / model["repository"].rsplit("/", 1)[-1]
        for file in model["files"]:
            with (directory / file["name"]).open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != file["sha256"]:
                raise ValueError("Local pinned model payload hash mismatch.")
    verification = json.loads((HERE / "kronos-payload-verification.json").read_text())
    vendor = HERE / "vendor" / "kronos"
    for file in verification["files"]:
        if hashlib.sha256((vendor / file["path"]).read_bytes()).hexdigest() != file["sha256"]:
            raise ValueError("Local pinned source hash mismatch.")
    sys.path.insert(0, str(vendor))
    with contextlib.redirect_stdout(sys.stderr):
        import numpy as np
        import pandas as pd
        import torch
        from model import Kronos, KronosPredictor, KronosTokenizer

        torch.set_num_threads(1)
        torch.manual_seed(271828)
        np.random.seed(271828)
        tokenizer = KronosTokenizer.from_pretrained(
            str(model_root / "Kronos-Tokenizer-2k"), local_files_only=True
        )
        model = Kronos.from_pretrained(
            str(model_root / "Kronos-mini"), local_files_only=True
        )
        predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=64)
        # Deliberately manufactured demonstration, not real-market history.
        values = []
        for index in range(32):
            close = 100.0 + index * 0.1 + float(np.sin(index))
            volume = 1000.0 + index * 10
            values.append([close - .2, close + .5, close - .5, close, volume, close * volume])
        frame = pd.DataFrame(values, columns=["open", "high", "low", "close", "volume", "amount"])
        times = pd.Series(pd.date_range("2000-01-01", periods=32, freq="D"))
        future = pd.Series(pd.date_range("2000-02-02", periods=2, freq="D"))
        prediction = predictor.predict(
            frame, times, future, pred_len=2, T=1.0, top_k=1,
            top_p=1.0, sample_count=1, verbose=False,
        )
        if prediction.shape != (2, 6) or not np.isfinite(prediction.to_numpy()).all():
            raise ValueError("Invalid synthetic model output.")
    return {
        "framework_executed": True, "engine_version": manifest["source"]["commit"],
        "torch_version": torch.__version__, "device": "cpu", "input_rows": 32,
        "prediction_rows": 2, "fixture_sha256": hashlib.sha256(frame.to_json().encode()).hexdigest(),
        "predictions": [[repr(float(value)) for value in row] for row in prediction.to_numpy()],
        "models": [
            {"repository": model["repository"], "revision": model["revision"],
             "weights_sha256": model["files"][0]["sha256"], "payload_verified": True}
            for model in manifest["models"]
        ],
        "interpretation": "Model output is unconstrained synthetic demonstration; no OHLC validity or forecast skill claim.",
    }


if __name__ == "__main__":
    execute("kronos", FIXTURE_ID, run)
