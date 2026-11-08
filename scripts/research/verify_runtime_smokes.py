"""Explicit canonical Job-controlled smoke checks; never called at app startup."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from edge.research_workers import research_runtime_readiness, run_synthetic

FIXTURES = {
    "qlib": "qlib-linear-three-assets-v1",
    "skfolio": "minimum-variance-three-assets",
    "kronos": "kronos-mini-ohlcv-v1",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--final", action="store_true")
    parser.add_argument("--deadline-v2", action="store_true")
    args = parser.parse_args()
    results = {}
    for framework, fixture in FIXTURES.items():
        if not args.deadline_v2:
            blockers = research_runtime_readiness(framework, project_root=ROOT)
            if blockers:
                raise RuntimeError(f"{framework} readiness blocked: {blockers}")
        start = time.monotonic()
        outcome = run_synthetic(
            framework, fixture, project_root=ROOT, timeout_seconds=120
        )
        results[framework] = {
            "elapsed_seconds": time.monotonic() - start, "status": outcome.status,
            "response": outcome.response, "error": outcome.error,
            "diagnostics": outcome.diagnostics,
        }
        print(framework, outcome.status, results[framework]["elapsed_seconds"], flush=True)
    filename = (
        "canonical-deadline-v2-smoke-results.json" if args.deadline_v2 else
        "canonical-final-smoke-results.json" if args.final else "canonical-smoke-results.json"
    )
    path = ROOT / "scripts" / "research" / filename
    path.write_text(json.dumps({
        "schema_version": 1, "production_ready": False,
        "scope": "actual fixed synthetic experiments on canonical Windows312 environments",
        "entry_deadline_contract_v2": args.deadline_v2,
        "configured_timeout_seconds": 120,
        "shared_cleanup_allowance_seconds": 2,
        "outer_bootstrap_hard_realtime_guarantee": False,
        "results": results,
    }, indent=2) + "\n", encoding="utf-8")
    print("smoke_report_sha256", hashlib.sha256(path.read_bytes()).hexdigest())
    if any(result["status"] != "ok" for result in results.values()):
        raise RuntimeError("One or more canonical actual framework checks failed.")


if __name__ == "__main__":
    main()
