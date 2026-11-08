"""SEC, journal, watchlist, providers and recovery screens render on the configurable grid.

Presentation only: the same widgets, messages and safety flows, arranged in
panels the user can move, resize, restyle and hide. Nothing here writes data.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import layout as lay
from edge.layout_store import layouts_path


def _surface(module: str, root: Path | None) -> AppTest:
    call = "render()" if root is None else f"render(Path({str(root)!r}))"
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\n"
        f"{call}\n",
        default_timeout=60,
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


def _charts(app: AppTest) -> int:
    return len(app.get("vega_lite_chart"))


SEC_HEADS = ["SEC filing research", "Lookup", "Source limitations",
             "Submitted lookup / validated identity", "Filings / response coverage",
             "Retrieval provenance / evidence"]
JOURNAL_HEADS = ["Research journal", "Journal overview", "Journal entries"]
PROVIDER_HEADS = ["Provider settings / ephemeral prices", "Non-secret request settings",
                  "Windows credential / native entry only", "Explicit authorized fetch"]
RECOVERY_HEADS = ["Journal backup / restore", "Create a backup", "Restore a separate copy"]
BUNDLE_HEADS = ["Application recovery / separate bundle", "Save application bundle",
                "Restore to a new application folder"]


@pytest.mark.parametrize(
    ("module", "use_root", "heads", "panels"),
    [
        ("dashboard_sec", False, SEC_HEADS, 6),
        ("dashboard_journal", True, JOURNAL_HEADS, 3),
        ("dashboard_provider", True, PROVIDER_HEADS, 5),
        ("dashboard_backup", True, RECOVERY_HEADS, 3),
        ("dashboard_application_backup", True, BUNDLE_HEADS, 3),
    ],
)
def test_panes_and_edit_mode(tmp_path, module, use_root, heads, panels):
    root = tmp_path / "data"
    app = _surface(module, root if use_root else None).run()
    assert not app.exception
    assert _heads(app)[: len(heads)] == heads
    assert _popovers(app) == 0  # view mode adds no layout widgets
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == panels  # framed panes plus any bare strip
    assert not layouts_path(root).exists()


def test_watchlist_panes_overview_reflects_a_save_even_when_moved_below(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_company", root).run()
    assert not app.exception
    assert _heads(app)[:4] == ["Company / watchlist workspace", "Watchlist overview",
                               "Add a user-declared company", "Saved watchlist notes"]
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert _popovers(app) == 3
    app.session_state["edge_layout_edit"] = False
    # Overview placed after the form: the KPI strip is still filled last.
    app.session_state["edge_layout_draft"] = {"watchlist": (
        lay.Slot("work", 0, 0, 12, 10, False, "", True),
        lay.Slot("overview", 0, 10, 12, 6, False, "", True),
    )}
    app.run()
    prefix = f"edge_company_{root.resolve()}"
    app.text_input(key=f"{prefix}_symbol").set_value("XY")
    app.text_input(key=f"{prefix}_company").set_value("Example label")
    next(b for b in app.button if b.label == "Save watchlist item").click().run()
    assert not app.exception
    assert any("Watchlist note saved locally." in str(s.value) for s in app.success)
    assert _heads(app)[0] == "Add a user-declared company"
    assert "Watchlist overview" in _heads(app)
    tile_text = "\n".join(str(m.value) for m in app.markdown)
    assert "Watchlist notes" in tile_text
    assert _charts(app) == 1  # cumulative curve drawn from the post-save snapshot


def test_journal_overview_views_change_only_the_charts(tmp_path):
    from edge.journal import JournalDraft
    from edge.journal_store import JournalStore

    root = tmp_path / "data"
    JournalStore(root).create(JournalDraft(
        "swing", "NOTE", "Synthetic company", "Temporary user opinion", date(2020, 1, 2),
        "Test only, not financial research", "", "", "", "", (),
    ))
    app = _surface("dashboard_journal", root).run()
    assert not app.exception
    assert _charts(app) == 2
    app.session_state["edge_layout_edit"] = True
    app.run()
    app.radio(key="edge_lay_journal_overview_view").set_value("timeline").run()
    assert not app.exception
    app.session_state["edge_layout_edit"] = False
    app.run()
    assert not app.exception
    assert _charts(app) == 1
    app.session_state["edge_layout_draft"] = {"journal": (
        lay.Slot("overview", 0, 0, 12, 10, False, "cumulative", False),
    )}
    app.run()
    assert not app.exception
    assert _charts(app) == 1


def test_sec_result_shows_when_reordered_above_lookup_and_views_switch(tmp_path, monkeypatch):
    from test_dashboard_sec import install_lookup

    mock = install_lookup(monkeypatch)
    app = _surface("dashboard_sec", None)
    app.session_state["edge_layout_draft"] = {"sec": (
        lay.Slot("filings", 0, 0, 12, 10, False, "", True),
        lay.Slot("identity", 0, 10, 12, 8, False, "", True),
    )}
    app.run()
    assert _heads(app)[0] == "Filings / response coverage"
    app.text_input(key="edge_sec_ticker").set_value("aapl")
    next(b for b in app.button if b.label == "Look up SEC filings").click().run()
    assert not app.exception
    mock.assert_called_once()
    assert len(app.dataframe) == 1 and _charts(app) == 2  # result in this interaction
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert _popovers(app) == 6
    app.radio(key="edge_lay_sec_filings_view").set_value("table").run()
    app.session_state["edge_layout_edit"] = False
    app.run()
    assert not app.exception
    assert len(app.dataframe) == 1 and _charts(app) == 0
    app.session_state["edge_layout_draft"] = {"sec": (
        lay.Slot("filings", 0, 0, 12, 10, False, "charts", True),
    )}
    app.run()
    assert not app.exception
    assert len(app.dataframe) == 0 and _charts(app) == 2
    mock.assert_called_once()  # layout changes never request data
    assert not list(tmp_path.iterdir())


def test_provider_snapshot_panel_hidden_and_moved_without_errors(tmp_path):
    root = tmp_path / "data"
    app = _surface("dashboard_provider", root)
    app.session_state["edge_layout_draft"] = {"providers": (
        lay.Slot("snapshot", 0, 0, 12, 6, False, "", True),
        lay.Slot("credential", 0, 6, 12, 6, True, "", True),
    )}
    app.run()
    assert not app.exception
    assert "Windows credential / native entry only" not in _heads(app)
    assert any("No validated price snapshot" in str(i.value) for i in app.info)
    # The request-budget board is still drawn (filled after every panel).
    assert any("Request budget" in str(m.value) for m in app.markdown)
