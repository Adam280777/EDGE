"""Research / decisions presentation: pure-UI fixes and visual result panes."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from streamlit.testing.v1 import AppTest


def surface(root):
    return AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_decisions import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()


def _confirm(app, fraction="0.01"):
    app.selectbox(key="edge_decision_task").set_value("Risk policy").run()
    app.text_input[0].set_value(fraction)
    app.button[1].click().run()


def test_blank_fraction_revokes_confirmed_policy(tmp_path):
    from edge.decision_store import DecisionStore

    root = tmp_path / "data"
    app = surface(root)
    _confirm(app)
    assert DecisionStore(root).get("risk", "manual-risk", 1).policy.enabled
    app.text_input[0].set_value("")
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    revoked = DecisionStore(root).get("risk", "manual-risk", 2).policy
    assert not revoked.enabled and revoked.max_risk_fraction is None
    # Confirmation without a limit still fails validation.
    app.text_input[0].set_value("")
    app.button[1].click().run()
    assert app.error
    assert len(DecisionStore(root).list_records("risk")) == 2


def test_risk_history_table_lists_revisions(tmp_path):
    root = tmp_path / "data"
    app = surface(root)
    _confirm(app, "0.01")
    _confirm(app, "0.02")
    assert not app.exception
    table = app.dataframe[-1].value
    assert list(table["Revision"]) == [2, 1]
    assert list(table["Max risk fraction"]) == ["0.02", "0.01"]


def test_dcf_has_no_silent_year_or_share_defaults(tmp_path):
    root = tmp_path / "data"
    app = surface(root)
    app.selectbox(key="edge_decision_task").set_value("Valuation scenario").run()
    assert all(item.value is None for item in app.number_input)
    values = {
        "Scenario name / user assumption": "Case", "Scenario source / user declaration": "Me",
        "Base revenue / major cash currency": "1000", "Annual growth / fraction": "0.05",
        "Operating margin / fraction": "0.2", "Cash conversion / fraction": "0.8",
        "Discount rate / fraction": "0.1", "Terminal growth / fraction": "0.02",
        "Company cash / major cash currency": "50", "Company debt / major cash currency": "20",
    }
    for label, value in values.items():
        next(item for item in app.text_input if item.label == label).set_value(value)
    app.button[0].click().run()
    assert any("no defaults are assumed" in item.value for item in app.error)
    assert "edge_decision_scenario" not in app.session_state
    app.number_input[0].set_value(5)
    app.number_input[1].set_value(100)
    app.button[0].click().run()
    assert not app.exception
    assert not app.error
    assert app.session_state["edge_decision_scenario"].years == 5
    assert not root.exists()


def test_saved_pages_filter_horizon_before_paginating(tmp_path):
    from edge.dashboard_decisions import _horizon_page

    class Rec:
        def __init__(self, i, horizon):
            self.record_id, self.horizon = str(i), horizon

    rows = [Rec(i, "swing" if i % 5 else "longterm") for i in range(250)]

    class Store:
        def list_records(self, kind, *, limit, offset):
            return tuple(rows[offset:offset + limit])

    longterm = [r for r in rows if r.horizon == "longterm"]
    page1, more, truncated = _horizon_page(Store(), "plan", "longterm", 1)
    assert [r.record_id for r in page1] == [r.record_id for r in longterm[:20]]
    assert more and not truncated
    page3, more, _ = _horizon_page(Store(), "plan", "longterm", 3)
    assert [r.record_id for r in page3] == [r.record_id for r in longterm[40:50]]
    assert not more


def test_saved_plan_renders_ladder_sizing_and_cards(tmp_path):
    from edge.decision_store import DecisionStore

    root = tmp_path / "data"
    app = surface(root)
    _confirm(app)
    app.selectbox(key="edge_decision_task").set_value("Trade plan").run()
    values = {
        "Ticker / identity unverified": "UITEST", "Company name / user-declared": "UI",
        "Exchange / user-declared": "LSE", "Price / FX source declaration": "Fixture",
        "Price / FX observed at / ISO timestamp with UTC offset":
            (datetime.now(UTC) - timedelta(seconds=5)).isoformat(),
        "Entry price": "100", "Entry zone low": "99", "Entry zone high": "101",
        "Stop price": "95", "Target price": "110", "Equity / GBP": "10000",
        "Maximum cash risk / GBP": "100", "Available cash / quote cash currency": "10000",
        "Exposure limit / GBP": "10000", "Current exposure / GBP": "0",
        "Entry fee / major cash currency; explicit zero if none": "1",
        "Exit fee / major cash currency; explicit zero if none": "1",
        "Full spread / original per-share price unit": "0",
        "Slippage per side / original price unit": "0",
        "Plan catalyst / optional dated event": "Results",
        "Plan catalyst date / YYYY-MM-DD, if supplied": "2030-01-01",
    }
    for label, value in values.items():
        next(item for item in app.text_input if item.label == label).set_value(value)
    for item in app.text_area:
        item.set_value("User-authored rationale.")
    next(b for b in app.button if b.label == "Save immutable plan revision").click().run()
    assert not app.exception and not app.error
    plan = DecisionStore(root).list_records("plan")[0]
    assert plan.sizing.estimated_loss_gbp.amount == Decimal(97)
    app.selectbox(key="edge_decision_task").set_value("Saved revisions").run()
    app.radio[1].set_value("plan").run()
    assert not app.exception and not app.error
    assert any("Historical plan estimate" in item.value for item in app.caption)
    markup = " ".join(item.value for item in app.markdown)
    assert "Est. loss at stop" in markup and "£97.00" in markup
    assert "Reasons not to take this trade" in markup
