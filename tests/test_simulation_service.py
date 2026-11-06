import sys
from types import ModuleType

import pytest

from edge.domain import SimulationConfig
from edge.errors import EdgeError
from edge.fixtures import demo_dataset
from edge.run_store import RunStore
from edge.simulation_service import run


def test_engine_exception_is_failed_run_not_successful_empty_report(tmp_path, monkeypatch) -> None:
    fake = ModuleType("edge.nautilus_engine")

    def failing_engine(*args):
        raise RuntimeError("controlled engine failure")

    fake.run_simulation = failing_engine
    monkeypatch.setitem(sys.modules, "edge.nautilus_engine", fake)
    with pytest.raises(EdgeError, match="controlled engine failure"):
        run(demo_dataset(), SimulationConfig(), tmp_path)
    runs = RunStore(tmp_path).list_runs()
    assert len(runs) == 1
    assert runs[0]["status"] == "failed"
    assert "controlled engine failure" in runs[0]["error"]
    result = RunStore(tmp_path).get(runs[0]["run_id"])["result"]
    assert result["status"] == "failed"
    assert "outcome" not in result
