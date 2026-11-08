"""Configurable workspace grid: layout edits are presentation-only and explicit to save."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge.layout_store import layouts_path

APP = Path(__file__).resolve().parents[1] / "app.py"


def _app(root: Path) -> AppTest:
    script = (
        "import runpy\n"
        "from pathlib import Path\n"
        f"namespace = runpy.run_path({str(APP)!r})\n"
        f"namespace['main'](Path({str(root)!r}))\n"
    )
    return AppTest.from_string(script, default_timeout=60)


def _heads(app: AppTest) -> list[str]:
    order = []
    for item in app.markdown:
        value = str(item.value)
        if value.startswith('<div class="edge-pane-head'):
            order.append(value.split('edge-pane-title">', 1)[1].split("<", 1)[0])
    return order


@pytest.fixture
def root(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    return data


def test_view_mode_adds_no_edit_controls_and_writes_nothing(root):
    app = _app(root).run()
    assert not app.exception
    assert _heads(app) == ["Book overview", "Allocation", "Currency exposure", "Risk monitor",
                           "Research pipeline", "Price tracks", "System status"]
    assert app.selectbox(key="edge_layout_profile").value == "Default"
    assert app.toggle(key="edge_layout_edit").value is False
    assert not app.slider and not app.text_input
    assert [b.key for b in app.button] == [f"edge_home_jump_{c}" for c in
                                           ("DSC", "DEC", "PRT", "MON", "JRN", "SEC")]
    assert not list(root.parent.rglob("*.json"))


def test_edit_resize_hide_restore_and_save_as_new_profile(root):
    app = _app(root).run()
    app.toggle(key="edge_layout_edit").set_value(True).run()
    assert not app.exception
    assert app.button(key="edge_layout_save").disabled  # nothing changed yet
    app.slider(key="edge_lay_home_alloc_w").set_value(12).run()
    assert not app.exception
    assert any("UNSAVED LAYOUT" in str(m.value) for m in app.markdown)
    assert app.selectbox(key="edge_layout_profile").disabled
    assert app.button(key="edge_layout_save").disabled  # built-in profiles are read-only
    assert not layouts_path(root).exists()

    app.button(key="edge_lay_home_fx_hide").click().run()
    assert "Currency exposure" not in _heads(app)
    app.selectbox(key="edge_layout_restore_home").set_value("fx").run()
    app.button(key="edge_layout_show_home").click().run()
    assert "Currency exposure" in _heads(app)

    app.radio(key="edge_lay_home_alloc_view").set_value("table").run()
    assert not app.exception

    app.text_input(key="edge_layout_new_name").set_value("My desk").run()
    app.button(key="edge_layout_save_as").click().run()
    assert not app.exception
    saved = json.loads(layouts_path(root).read_text(encoding="utf-8"))
    assert saved["active"] == "My desk"
    alloc = next(s for s in saved["custom"]["My desk"]["home"] if s["id"] == "alloc")
    assert alloc["w"] == 12 and alloc["view"] == "table"
    assert not any("UNSAVED LAYOUT" in str(m.value) for m in app.markdown)

    fresh = _app(root).run()
    assert fresh.selectbox(key="edge_layout_profile").value == "My desk"
    fresh.toggle(key="edge_layout_edit").set_value(True).run()
    assert fresh.slider(key="edge_lay_home_alloc_w").value == 12


def test_switching_to_a_builtin_template_reorders_panels_and_persists_choice(root):
    app = _app(root).run()
    app.selectbox(key="edge_layout_profile").set_value("Swing trading").run()
    assert not app.exception
    assert _heads(app)[0] == "Risk monitor"
    assert "Currency exposure" not in _heads(app)  # hidden by the swing template
    assert json.loads(layouts_path(root).read_text(encoding="utf-8"))["active"] == "Swing trading"


def test_reset_and_discard_never_touch_disk(root):
    app = _app(root).run()
    app.toggle(key="edge_layout_edit").set_value(True).run()
    app.button(key="edge_lay_home_status_up").click().run()
    assert _heads(app).index("System status") < _heads(app).index("Price tracks")
    app.button(key="edge_layout_discard").click().run()
    assert _heads(app).index("System status") > _heads(app).index("Price tracks")
    app.button(key="edge_lay_home_status_up").click().run()
    app.button(key="edge_layout_reset").click().run()
    assert _heads(app).index("System status") > _heads(app).index("Price tracks")
    assert _heads(app)[0] == "Book overview"
    assert not layouts_path(root).exists()


def test_corrupt_layout_file_is_reported_and_never_overwritten(root):
    path = layouts_path(root)
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    app = _app(root).run()
    assert not app.exception
    assert any("Saved layouts file is unreadable" in e.value for e in app.error)
    assert _heads(app)[0] == "Book overview"
    app.selectbox(key="edge_layout_profile").set_value("Long-term").run()
    assert _heads(app)[0] == "Book overview" and _heads(app)[1] == "Allocation"
    app.toggle(key="edge_layout_edit").set_value(True).run()
    assert app.button(key="edge_layout_save").disabled
    assert path.read_text(encoding="utf-8") == "{broken"


def test_drag_payload_is_validated_and_normalized(root, monkeypatch):
    from edge import ui_grid

    monkeypatch.setattr(ui_grid, "_drag", lambda **kwargs: {})
    # The real change callback runs before the script body; the script mirrors that order.
    script = (
        "import streamlit as st\n"
        "from edge import ui_grid\n"
        "from edge.ui_grid import Panel, grid\n"
        "st.session_state['edge_layout_edit'] = True\n"
        "ui_grid._accept('demo', st.session_state.pop('raw', None))\n"
        "grid('demo', [Panel('a', 'A', 'Alpha', lambda c: st.write('a', c.w)),\n"
        "              Panel('b', 'B', 'Beta', lambda c: st.write('b', c.w))])\n"
    )
    app = AppTest.from_string(script).run()
    assert not app.exception
    app.session_state["raw"] = json.dumps({"ws": "demo", "rev": 1, "slots": [
        {"id": "b", "x": 0, "y": 0, "w": 99, "h": 3, "hidden": False, "view": "", "fit": False},
    ]})
    app.run()  # w=99 is outside the grid: strict parsing rejects it with a warning, no crash
    assert any("rejected" in w.value for w in app.warning)
    app.session_state["raw"] = json.dumps({"ws": "demo", "rev": 2, "slots": [
        {"id": "b", "x": 0, "y": 0, "w": 12, "h": 4, "hidden": False, "view": "", "fit": False},
        {"id": "a", "x": 0, "y": 4, "w": 6, "h": 9, "hidden": False, "view": "", "fit": False},
        {"id": "zz", "x": 0, "y": 0, "w": 6, "h": 9, "hidden": False, "view": "", "fit": False},
    ]})
    app.run()
    assert not app.exception
    assert not app.warning
    draft = app.session_state["edge_layout_draft"]["demo"]
    assert [(s.id, s.y) for s in draft] == [("b", 0), ("a", 4)]
    app.session_state["raw"] = json.dumps({"ws": "other", "rev": 3, "slots": []})
    app.run()
    assert any("rejected" in w.value for w in app.warning)
    assert app.session_state["edge_layout_draft"]["demo"] == draft

def test_engine_panes_are_configurable_and_a_run_shows_even_when_reordered(root):
    from edge import layout as lay

    app = _app(root)
    app.session_state["edge_workspace"] = "Engine acceptance"
    app.run()
    assert not app.exception
    app.toggle(key="edge_layout_edit").set_value(True).run()
    assert not app.exception
    grips = [str(m.value) for m in app.markdown if 'class="edge-pane-head edge-grip"' in str(m.value)]
    assert len(grips) == 4
    assert [h.value for h in app.header] == [
        "Engine acceptance", "Synthetic fixture / provenance",
        "Most recent run in this session", "Persisted run history",
    ]
    app.toggle(key="edge_layout_edit").set_value(False).run()
    # Put the run panes above the buttons: a new run must still appear in this interaction.
    app.session_state["edge_layout_draft"] = {"engine": (
        lay.Slot("recent", 0, 0, 6, 12, False, "", True),
        lay.Slot("history", 6, 0, 6, 12, False, "", True),
    )}
    app.run()
    assert [h.value for h in app.header][:2] == [
        "Most recent run in this session", "Persisted run history",
    ]
    app.button(key="run_round_trip").click().run()
    assert not app.exception
    assert "edge_recent_run" in app.session_state
    assert len(app.metric) == 3
    run_id = app.session_state["edge_recent_run"]["manifest"]["run_id"]
    assert any(run_id in frame.value.to_string() for frame in app.dataframe)


def test_saving_while_one_task_shows_keeps_other_tasks_panel_placement(root):
    app = _app(root).run()
    app.radio(key="edge_workspace").set_value("Discovery").run()
    app.toggle(key="edge_layout_edit").set_value(True).run()
    app.slider(key="edge_lay_discovery_declare_w").set_value(4).run()
    app.text_input(key="edge_layout_new_name").set_value("Desk").run()
    app.button(key="edge_layout_save_as").click().run()
    assert not app.exception

    app.radio(key="edge_discovery_task").set_value("Import CSV").run()
    app.slider(key="edge_lay_discovery_import_w").set_value(8).run()
    app.button(key="edge_layout_save").click().run()
    assert not app.exception
    saved = {s["id"]: s for s in json.loads(layouts_path(root).read_text(encoding="utf-8"))
             ["custom"]["Desk"]["discovery"]}
    assert saved["import"]["w"] == 8
    assert saved["declare"]["w"] == 4  # Universe-task panel survived a save made on another task
