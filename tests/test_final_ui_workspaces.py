"""Isolated native-control workflows; no vault or provider access."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from streamlit.testing.v1 import AppTest

from edge.dashboard_manual import decimal_input, literal, timestamp_input
from edge.decision_contracts import DecisionInputError


def surface(module, root):
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()


@pytest.mark.parametrize("module", [
    "dashboard_portfolio", "dashboard_decisions", "dashboard_discovery", "dashboard_monitoring",
])
def test_new_surface_empty_is_read_only(tmp_path, module):
    root = tmp_path / "data"
    app = surface(module, root)
    assert not app.exception
    assert not root.exists()
    assert app.warning
    assert any("USER-DECLARED" in warning.value for warning in app.warning)


def test_portfolio_explicit_cash_and_incomplete_valuation(tmp_path):
    from edge.portfolio_store import PortfolioStore

    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Append record").run()
    app.text_input[0].set_value("2020-01-01T00:00:00+00:00")
    app.text_input[1].set_value("Manual statement")
    app.text_input[2].set_value("100")
    app.button[0].click().run()
    assert not app.exception
    assert PortfolioStore(root).state().cash["GBP"] == Decimal(100)
    app.radio(key="edge_portfolio_task").set_value("Valuation").run()
    assert not app.exception
    assert any("USD cash opening is missing" in item.value for item in app.warning)
    assert any("equity_total_gbp: Unavailable" in item.value for item in app.text)


def test_risk_disabled_confirmed_then_revoked(tmp_path):
    from edge.decision_store import DecisionStore

    root = tmp_path / "data"
    app = surface("dashboard_decisions", root)
    app.selectbox(key="edge_decision_task").set_value("Trade plan").run()
    assert all(button.disabled for button in app.button)
    app.selectbox(key="edge_decision_task").set_value("Risk policy").run()
    app.text_input[0].set_value("0.01")
    app.button[1].click().run()
    assert not app.exception
    assert DecisionStore(root).get("risk", "manual-risk", 1).policy.enabled
    app.selectbox(key="edge_decision_task").set_value("Trade plan").run()
    assert not any(button.disabled for button in app.button)
    app.selectbox(key="edge_decision_task").set_value("Risk policy").run()
    app.text_input[0].set_value("0.02")
    app.button[0].click().run()
    assert not app.exception
    assert not DecisionStore(root).get("risk", "manual-risk", 2).policy.enabled


def test_monitoring_schedule_off_and_invalid_rule_does_not_create_database(tmp_path):
    root = tmp_path / "data"
    app = surface("dashboard_monitoring", root)
    assert app.checkbox[0].value is False
    app.radio(key="edge_monitor_task").set_value("New rule").run()
    app.button[0].click().run()
    assert not app.exception
    assert app.error
    assert not root.exists()


def test_manual_parsing_and_proxy_rendering():
    from types import MappingProxyType

    assert decimal_input("1.01") == Decimal("1.01")
    assert literal(MappingProxyType({"GBP": Decimal("1.01")})) == {"GBP": "1.01"}
    assert timestamp_input("2020-01-01T01:00:00+01:00").hour == 0
    for value in ("nan", "inf", "", "not money"):
        with pytest.raises(DecisionInputError):
            decimal_input(value)
    for value in ("2020-01-01", (datetime.now(UTC) + timedelta(days=1)).isoformat()):
        with pytest.raises(DecisionInputError):
            timestamp_input(value)


def test_shared_navigation_revokes_timer_latch_and_opt_in(tmp_path):
    from test_dashboard import dashboard

    root = tmp_path / "data"
    app = dashboard(root)
    app.radio(key="edge_workspace").set_value("Monitoring").run()
    suffix = str(root.resolve())
    enabled_key = f"edge_monitor_enabled_{suffix}"
    active_key = f"edge_monitor_active_{suffix}"
    app.checkbox(key=enabled_key).check().run()
    assert app.session_state[active_key] is True
    app.radio(key="edge_workspace").set_value("Manual portfolio").run()
    assert app.session_state[active_key] is False
    assert app.session_state[enabled_key] is False
    app.radio(key="edge_workspace").set_value("Monitoring").run()
    assert app.checkbox(key=enabled_key).value is False
    assert not app.exception
    assert not root.exists()


def test_company_empty_cta_routes_without_write(tmp_path):
    app = surface("dashboard_company", tmp_path / "data")
    workspace_key = f"edge_company_{(tmp_path / 'data').resolve()}_workspace"
    app.radio(key=workspace_key).set_value("Company view").run()
    next(button for button in app.button if button.label == "Go to Watchlist").click().run()
    assert app.radio(key=workspace_key).value == "Watchlist"
    assert not app.exception
    assert not (tmp_path / "data").exists()


def test_research_and_confirmed_plan_save_actual_backend_envelopes(tmp_path):
    from edge.decision_store import DecisionStore

    root = tmp_path / "data"
    app = surface("dashboard_decisions", root)
    app.selectbox(key="edge_decision_task").set_value("New research").run()
    fields = {
        "Ticker / identity unverified": "UITEST",
        "Company name / user-declared": "UI test / not company facts",
        "Exchange / user-declared": "LSE",
        "Evidence source / manual reference": "User authored UI fixture",
        "Evidence declared at / ISO timestamp with UTC offset": datetime.now(UTC).isoformat(),
        "Reporting period / explicit label": "User test period",
        "Evidence unit / explicit label": "text",
    }
    for label, value in fields.items():
        next(item for item in app.text_input if item.label == label).set_value(value)
    for item in app.text_area:
        item.set_value("User-authored test content / not verified.")
    next(item for item in app.button if item.label == "Save immutable research revision").click().run()
    assert not app.exception
    assert not app.error
    research = DecisionStore(root).list_records("research")[0]
    assert len(research.facts) == len(research.assumptions) == len(research.opinions) == 1
    assert research.horizon == "swing"
    app.selectbox(key="edge_decision_task").set_value("Risk policy").run()
    app.text_input[0].set_value("0.01")
    app.button[1].click().run()
    app.selectbox(key="edge_decision_task").set_value("Trade plan").run()
    values = {
        "Ticker / identity unverified": "UITEST",
        "Company name / user-declared": "UI test / not company facts",
        "Exchange / user-declared": "LSE",
        "Price / FX source declaration": "User authored UI fixture",
        "Price / FX observed at / ISO timestamp with UTC offset": datetime.now(UTC).isoformat(),
        "Entry price": "100", "Entry zone low": "99", "Entry zone high": "101",
        "Stop price": "95", "Target price": "110", "Equity / GBP": "10000",
        "Maximum cash risk / GBP": "100", "Available cash / quote cash currency": "10000",
        "Exposure limit / GBP": "10000", "Current exposure / GBP": "0",
        "Entry fee / major cash currency; explicit zero if none": "1",
        "Exit fee / major cash currency; explicit zero if none": "1",
        "Full spread / original per-share price unit": "0",
        "Slippage per side / original price unit": "0",
    }
    for label, value in values.items():
        next(item for item in app.text_input if item.label == label).set_value(value)
    for item in app.text_area:
        item.set_value("User-authored test rationale / not verified.")
    next(item for item in app.button if item.label == "Save immutable plan revision").click().run()
    assert not app.exception
    assert not app.error
    plan = DecisionStore(root).list_records("plan")[0]
    assert plan.sizing.shares == 19
    assert plan.sizing.estimated_loss_gbp.amount == Decimal(97)
    assert plan.policy.revision_id == "manual-risk:1"
