"""Built-in layout templates must match the panels each workspace actually registers."""

from __future__ import annotations

import runpy
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import layout as lay

APP = Path(__file__).resolve().parents[1] / "app.py"
WORKSPACES = runpy.run_path(str(APP))["WORKSPACES"]
INTEGRITY = {"limits", "provenance", "status", "boundary"}
# Panels that only register after a user action (a submitted screen); verified against source.
CONDITIONAL = {("discovery", "results"), ("discovery", "evidence")}
SRC = Path(__file__).resolve().parents[1] / "src" / "edge"


def test_conditional_panels_exist_in_source():
    text = (SRC / "dashboard_discovery.py").read_text(encoding="utf-8")
    for _ws, pid in CONDITIONAL:
        assert f'Panel("{pid}"' in text


def _registered(root: Path) -> dict[str, dict[str, lay.PanelSpec]]:
    script = (
        "import runpy\n"
        "from pathlib import Path\n"
        f"namespace = runpy.run_path({str(APP)!r})\n"
        f"namespace['main'](Path({str(root)!r}))\n"
    )
    app = AppTest.from_string(script, default_timeout=60).run()
    found: dict[str, dict[str, lay.PanelSpec]] = {}

    def collect() -> None:
        assert not app.exception
        for ws, entry in app.session_state["edge_layout_registry"].items():
            found.setdefault(ws, {}).update({s.id: s for s in entry["specs"]})

    collect()
    for name in WORKSPACES:
        app.radio(key="edge_workspace").set_value(name).run()
        collect()
        if name == "Discovery":
            for task in app.radio(key="edge_discovery_task").options:
                app.radio(key="edge_discovery_task").set_value(task).run()
                collect()
    return found


@pytest.fixture(scope="module")
def registered(tmp_path_factory) -> dict[str, dict[str, lay.PanelSpec]]:
    root = tmp_path_factory.mktemp("templates") / "data"
    root.mkdir()
    return _registered(root)


@pytest.mark.parametrize("profile", ["Swing trading", "Long-term"])
def test_template_panels_and_views_exist_and_place_cleanly(profile, registered):
    workspaces = lay.TEMPLATES[profile]
    assert "home" in workspaces
    for ws, slots in workspaces.items():
        assert ws in registered, f"{profile}: unknown workspace {ws}"
        specs = registered[ws]
        for slot in slots:
            if (ws, slot.id) in CONDITIONAL:
                continue
            assert slot.id in specs, f"{profile}/{ws}: unknown panel {slot.id}"
            if slot.view:
                assert slot.view in specs[slot.id].views, f"{profile}/{ws}/{slot.id}: view {slot.view}"
            if slot.id in INTEGRITY:
                assert not slot.hidden, f"{profile}/{ws}: data-integrity panel {slot.id} hidden"
        out = lay.normalize(slots, tuple(specs.values()))
        assert sorted(s.id for s in out) == sorted(specs)
        visible = [s for s in out if not s.hidden]
        for i, a in enumerate(visible):
            for b in visible[i + 1:]:
                assert (a.x + a.w <= b.x or b.x + b.w <= a.x
                        or a.y + a.h <= b.y or b.y + b.h <= a.y), f"{profile}/{ws}: {a.id}/{b.id} overlap"


def test_every_workspace_registers_a_grid(registered):
    assert set(registered) >= {"home", "engine", "decisions", "discovery", "lab", "sec", "journal",
                               "watchlist", "providers", "recovery", "bundle", "portfolio", "monitoring"}
