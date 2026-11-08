"""Portfolio and Monitoring on the configurable grid: same records, any arrangement."""

from __future__ import annotations

import html
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import layout as lay
from edge.decision_contracts import ManualProvenance, PriceQuote

PORTFOLIO_HEADS = [
    "Ledger review", "Native accounting / no market prices", "GBP reporting / manual snapshot",
    "Holding evidence / manual identity", "Ledger timeline", "Realised P&L per FIFO sale",
    "Ledger records",
]
# The open alert in the fixture adds the acknowledgement sub-header inside "Recorded alerts".
MONITORING_HEADS = ["Session schedule", "Rule status board", "Threshold proximity",
                    "Recorded alerts", "Open alerts awaiting acknowledgement"]


def surface(module, root):
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()


def heads(app) -> list[str]:
    order = []
    for item in app.markdown:
        value = str(item.value)
        if value.startswith('<div class="edge-pane-head') and 'edge-pane-title">' in value:
            order.append(html.unescape(value.split('edge-pane-title">', 1)[1].split("<", 1)[0]))
    return order


def popovers(app) -> list:
    # Only the grid's "Panel options" popovers exist on these pages (none in view mode).
    return list(app.get("popover"))


def set_view(app, ws, panel, view):
    specs = app.session_state["edge_layout_registry"][ws]["specs"]
    slots = lay.update(lay.default_slots(specs), specs, panel, view=view)
    app.session_state["edge_layout_draft"] = {ws: slots}
    return app.run()


def hide(app, ws, panel):
    specs = app.session_state["edge_layout_registry"][ws]["specs"]
    slots = lay.update(lay.default_slots(specs), specs, panel, hidden=True)
    app.session_state["edge_layout_draft"] = {ws: slots}
    return app.run()


@pytest.fixture
def book(tmp_path):
    from edge import portfolio as p
    from edge.portfolio_store import PortfolioStore

    root = tmp_path / "data"
    store = PortfolioStore(root)
    at = datetime(2020, 1, 1, tzinfo=UTC)
    gbp = p.Instrument("TEST", "UK", "GBP")
    store.append(p.CashOpening(at, "GBP", Decimal(100), "Statement"))
    store.append(p.CashOpening(at, "USD", Decimal(0), "Statement"))
    store.append(p.OpeningHolding(at, gbp, 10, Decimal(50), date(2019, 1, 1), "Statement"))
    store.append(p.Sell(at + timedelta(days=1), gbp, 4, Decimal(6), "GBP", Decimal(0),
                        "GBP", "Statement"))
    return root, gbp


@pytest.fixture
def watch(tmp_path):
    from edge.monitoring import MonitoringRule
    from edge.monitoring_store import MonitoringStore

    root = tmp_path / "data"
    store = MonitoringStore(root)
    now = datetime.now(UTC)
    proof = ManualProvenance("User", now - timedelta(minutes=5))
    store.append_rule(MonitoringRule("a" * 32, "ACME", "US", "USD", Decimal(90),
                                     Decimal(120), "review", 86400, proof))
    store.append_quote("ACME", "US", PriceQuote(Decimal(100), "USD",
                                                now - timedelta(minutes=10), proof))
    store.check(as_of=now)
    return root, store


def test_portfolio_panes_edit_mode_and_views(book):
    root, _gbp = book
    app = surface("dashboard_portfolio", root)
    assert not app.exception
    assert heads(app) == PORTFOLIO_HEADS
    assert not popovers(app)
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert len(popovers(app)) == 8  # every panel, including the unframed KPI strip
    app.radio(key="edge_lay_portfolio_holdings_view").set_value("table").run()
    assert not app.exception
    app.session_state["edge_layout_edit"] = False
    for panel, view in (("holdings", "charts"), ("holdings", "table"), ("timeline", "steps"),
                        ("timeline", "timeline"), ("realised", "table")):
        set_view(app, "portfolio", panel, view)
        assert not app.exception, (panel, view)
    assert any(df.value.shape[0] == 1 and "Sale event" in df.value.columns for df in app.dataframe)


def test_portfolio_task_header_follows_task_and_values_match(book):
    root, gbp = book
    app = surface("dashboard_portfolio", root)
    now = datetime.now(UTC)
    app.session_state[f"edge_portfolio_observations_{root.resolve()}"] = {
        gbp: PriceQuote(Decimal(7), "GBP", now - timedelta(minutes=1),
                        ManualProvenance("Manual", now - timedelta(seconds=30))),
    }
    for task, head in (("Append record", "Append manual ledger record"),
                       ("Valuation", "Session valuation inputs"),
                       ("Import CSV", "Import user-authored CSV")):
        app.radio(key="edge_portfolio_task").set_value(task).run()
        assert not app.exception
        assert heads(app)[0] == head
    # The audit text is identical whichever task is chosen; panels read one snapshot.
    assert any("equity_total_gbp: 166 GBP" in item.value for item in app.text)
    hide(app, "portfolio", "snapshot")
    assert not app.exception
    assert "GBP reporting / manual snapshot" not in heads(app)


def test_portfolio_append_refreshes_every_panel(tmp_path):
    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Append record").run()
    app.text_input[0].set_value("2020-01-01T00:00:00+00:00")
    app.text_input[1].set_value("Manual statement")
    app.text_input[2].set_value("100")
    app.button[0].click().run()
    assert not app.exception
    assert any("No order was placed" in item.value for item in app.success)
    # The ledger table on the same page already includes the appended record.
    assert any("Cash opening" in list(df.value.get("Type", [])) for df in app.dataframe)


def test_monitoring_panes_edit_mode_and_views(watch):
    root, _store = watch
    app = surface("dashboard_monitoring", root)
    assert not app.exception
    assert heads(app) == MONITORING_HEADS
    assert "THESIS REVIEW" in "\n".join(str(m.value) for m in app.markdown)
    app.session_state["edge_layout_edit"] = True
    app.run()
    assert not app.exception
    assert len(popovers(app)) == 5
    for panel, view in (("track", "track"), ("track", "table"), ("alerts", "timeline"),
                        ("alerts", "steps"), ("alerts", "table")):
        set_view(app, "monitoring", panel, view)
        assert not app.exception, (panel, view)
    assert list(app.dataframe[-1].value["Symbol"]) == ["US:ACME"]


def test_monitoring_forms_keep_display_panels(watch):
    root, store = watch
    app = surface("dashboard_monitoring", root)
    app.radio(key="edge_monitor_task").set_value("New rule").run()
    assert not app.exception
    assert heads(app) == ["New monitoring rule", *MONITORING_HEADS[1:]]
    alerts = len(store.alerts())
    app.radio(key="edge_monitor_task").set_value("Manual quote").run()
    assert heads(app)[0] == "Manual quote observation"
    assert len(store.alerts()) == alerts  # rendering never records alerts


def test_hidden_schedule_panel_stops_the_session_timer(watch):
    root, _store = watch
    app = surface("dashboard_monitoring", root)
    enabled = f"edge_monitor_enabled_{root.resolve()}"
    active = f"edge_monitor_active_{root.resolve()}"
    app.checkbox(key=enabled).check().run()
    assert app.session_state[active] is True
    hide(app, "monitoring", "task")
    assert not app.exception
    assert app.session_state[active] is False
    assert "Session schedule" not in heads(app)
