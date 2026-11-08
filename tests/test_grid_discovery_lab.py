"""Discovery and Research lab render on the configurable grid without changing their data."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import layout as lay


def _surface(module: str, root: Path) -> AppTest:
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    )


def _heads(app: AppTest) -> list[str]:
    order = []
    for item in app.markdown:
        value = str(item.value)
        if value.startswith('<div class="edge-pane-head'):
            order.append(value.split('edge-pane-title">', 1)[1].split("<", 1)[0])
    return order


def _popovers(app: AppTest) -> int:
    return len(app.get("popover"))


def _companies():
    from edge.decision_contracts import ManualProvenance
    from edge.discovery import CompanyObservations, FundamentalObservation
    from edge.research_workflows import CompanyIdentity

    now = datetime.now(UTC)
    source = ManualProvenance("User statement", now)
    return tuple(
        CompanyObservations(CompanyIdentity(sym, f"{sym} plc", "LSE", "GBP"), (
            FundamentalObservation("margin", value, "fraction", "FY2020", now, source, "claimed-reported"),
        ))
        for sym, value in (("AAA", Decimal("0.3")), ("BBB", Decimal("0.05")), ("CCC", None))
    )


def _fill_screen(app: AppTest) -> None:
    for label, value in (
        ("Filter metric / exact label", "margin"), ("Filter unit / exact label", "fraction"),
        ("Required reporting period / exact label", "FY2020"), ("Minimum / optional decimal", "0.1"),
        ("Oldest allowed observation / ISO timestamp with UTC offset", "2020-01-01T00:00:00Z"),
    ):
        next(item for item in app.text_input if item.label == label).set_value(value)


UNIVERSE_HEADS = ["Discovery universe", "Declare an observation", "Universe map", "Declared companies"]


def test_discovery_universe_panes_edit_mode_and_views(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_discovery", root)
    app.session_state[f"edge_discovery_{root.resolve()}"] = _companies()
    app.run()
    assert not app.exception
    assert _heads(app)[:4] == UNIVERSE_HEADS  # then one nested card per company
    assert _popovers(app) == 0
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == len(UNIVERSE_HEADS)
    assert app.radio(key="edge_lay_discovery_universe_view").value == "all"
    app.radio(key="edge_lay_discovery_universe_view").set_value("coverage").run()
    assert not app.exception
    app.session_state["edge_layout_edit"] = False
    app.run()
    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 1  # coverage map only
    assert not root.exists()


def test_discovery_accumulation_view_in_fixed_panel(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_discovery", root)
    app.session_state[f"edge_discovery_{root.resolve()}"] = _companies()
    app.session_state["edge_layout_draft"] = {"discovery": (
        lay.Slot("universe", 0, 0, 12, 10, False, "accumulation", False),
    )}
    app.run()
    assert not app.exception
    assert len(app.get("vega_lite_chart")) == 1
    assert not root.exists()


def test_discovery_screen_panes_and_result_shows_when_reordered_above_form(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_discovery", root)
    app.session_state[f"edge_discovery_{root.resolve()}"] = _companies()
    app.session_state["edge_discovery_task"] = "Screen"
    app.run()
    assert not app.exception
    heads = _heads(app)
    assert heads[:4] == ["Discovery universe", "Screen definition",
                         "Submitted screen / inclusion and exclusion evidence",
                         "Per-company screen evidence"]
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == 4
    app.session_state["edge_layout_edit"] = False
    # Results above the form: a submitted screen must still show in this interaction.
    app.session_state["edge_layout_draft"] = {"discovery": (
        lay.Slot("results", 0, 0, 12, 10, False, "", True),
        lay.Slot("evidence", 0, 10, 12, 10, False, "", True),
    )}
    app.run()
    assert _heads(app)[0] == "Submitted screen / inclusion and exclusion evidence"
    _fill_screen(app)
    next(item for item in app.button if item.label == "Run deterministic manual screen").click().run()
    assert not app.exception
    texts = [item.value for item in app.text]
    assert sum("included: True" in item for item in texts) == 1
    assert sum("included: False" in item for item in texts) == 2
    assert "edge_discovery_once" not in app.session_state
    assert not root.exists()


def test_discovery_import_task_is_one_panel(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_discovery", root)
    app.session_state["edge_discovery_task"] = "Import CSV"
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _heads(app) == ["Discovery universe", "Import fundamentals CSV"]
    assert _popovers(app) == 2
    assert not root.exists()


LAB_HEADS = ["Synthetic research lab", "Experiment control", "Run history", "Real-data boundary"]


def test_lab_panes_edit_mode_and_history_view(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_research_lab", root).run()
    assert not app.exception
    assert _heads(app) == LAB_HEADS
    assert len(app.button) == 2
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == len(LAB_HEADS)
    app.session_state["edge_layout_edit"] = False
    app.session_state["edge_research_lab_history"] = [{
        "Finished (UTC)": "2026-01-01 00:00", "_when": "2026-01-01T00:00:00+00:00", "Engine": "qlib",
        "Fixed fixture": "qlib-linear-three-assets-v1", "Outcome": "blocked", "Result accepted": "no",
        "Request digest prefix": "—",
    }]
    app.session_state["edge_layout_draft"] = {"lab": (
        lay.Slot("history", 0, 0, 12, 8, False, "table", False),
    )}
    app.run()
    assert not app.exception
    assert len(app.dataframe) == 1
    assert not app.get("vega_lite_chart")
    app.session_state["edge_layout_draft"] = {"lab": (
        lay.Slot("history", 0, 0, 12, 8, False, "chart", False),
    )}
    app.run()
    assert not app.exception
    assert not app.dataframe
    assert app.get("vega_lite_chart")
    assert not root.exists()


def _finished_job(status="ok", response=None):
    from edge.dashboard_research_lab import _Job

    job = _Job("qlib", "qlib-linear-three-assets-v1")
    job.status = status
    job.response = response
    job.message = "" if status == "ok" else "Runtime unavailable."
    job.finished_at = time.monotonic() - 60
    job.finished_wall = datetime.now(UTC)
    job.done.set()
    return job


def _qlib_envelope(fixture: str) -> dict:
    digest = "a" * 64
    return {
        "schema_version": 1, "framework": "qlib", "status": "ok", "synthetic": True,
        "fixture_id": fixture, "request_sha256": digest, "limitations": ["Synthetic only."],
        "runtime": {"python_version": "3.12.16", "primary_package": "pyqlib", "primary_version": "test",
                    "worker_sha256": "b" * 64, "local_consistency_verified": True,
                    "signed_security_attestation": False},
        "result": {"framework_executed": True, "engine_version": "0.9", "fixture_sha256": "c" * 64,
                   "synthetic_test_mse": "0.0001", "constant_training_mean_baseline_mse": "0.5",
                   "predictions": [repr(0.1 * i) for i in range(10)]},
    }


def test_lab_accepted_result_panes_in_edit_mode(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_research_lab", root)
    app.session_state["edge_research_lab_job"] = _finished_job(
        response=_qlib_envelope("qlib-linear-three-assets-v1"))
    app.run()
    assert not app.exception
    assert _heads(app) == ["Synthetic research lab", "Experiment control", "qlib synthetic result",
                           "Provenance &amp; digests", "Limitations", "Run history", "Real-data boundary"]
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == 7
    assert not root.exists()


def test_lab_refused_run_shows_outcome_pane_only(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_research_lab", root)
    app.session_state["edge_research_lab_job"] = _finished_job(status="blocked")
    app.run()
    assert not app.exception
    heads = _heads(app)
    assert "Experiment outcome" in heads and "Provenance &amp; digests" not in heads
    assert any("Experiment blocked" in item.value for item in app.warning)
    assert not root.exists()
