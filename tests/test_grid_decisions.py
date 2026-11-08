"""Decisions workspace on the configurable grid: presentation only, no writes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import layout as lay

TASKS = ["Saved revisions", "New research", "Risk policy", "Trade plan", "Valuation scenario"]


def surface(root: Path) -> AppTest:
    return AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_decisions import render\n"
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


@pytest.mark.parametrize("task", TASKS)
def test_view_mode_renders_summary_pane_and_task_body(tmp_path, task):
    root = tmp_path / "data"
    app = surface(root)
    app.session_state["edge_decision_task"] = task
    app.run()
    assert not app.exception
    heads = _heads(app)
    assert heads[0] == "Decision summary"
    assert any("Research / decisions" in h.value for h in app.header)
    assert not app.get("popover")
    markup = " ".join(str(m.value) for m in app.markdown)
    assert "Risk sizing" in markup and "Horizon" in markup
    if task == "Risk policy":
        assert "Policy revision history" in heads
    assert not root.exists()


@pytest.mark.parametrize("task", TASKS)
def test_edit_mode_has_one_options_menu_per_panel(tmp_path, task):
    root = tmp_path / "data"
    app = surface(root)
    app.session_state["edge_decision_task"] = task
    app.run()
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert len(app.get("popover")) == 2
    heads = _heads(app)
    assert "Decision summary" in heads and task in heads
    app.radio(key="edge_lay_decisions_summary_view").set_value("list").run()
    assert not app.exception
    assert app.session_state["edge_layout_draft"]["decisions"]
    assert not root.exists()


def test_list_view_and_reordered_draft_render(tmp_path):
    root = tmp_path / "data"
    app = surface(root)
    app.session_state["edge_layout_draft"] = {"decisions": (
        lay.Slot("task", 0, 0, 12, 20, fit=True),
        lay.Slot("summary", 0, 20, 6, 4, view="list", fit=True),
    )}
    app.run()
    assert not app.exception
    assert "Decision summary" in _heads(app)
    texts = " ".join(str(m.value) for m in app.markdown)
    assert "Risk sizing" in texts and "DISABLED" in texts
    # Selectors stay above the grid; widget order is unchanged.
    assert app.radio[0].key == "edge_decision_horizon"
    assert not root.exists()
