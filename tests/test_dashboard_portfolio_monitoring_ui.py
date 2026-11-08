"""Terminal-redesign workflows for Portfolio / Monitoring / shared readout."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from streamlit.testing.v1 import AppTest

from edge.dashboard_manual import readout_rows
from edge.decision_contracts import ManualProvenance, PriceQuote


def surface(module, root):
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\n"
        f"render(Path({str(root)!r}))\n",
        default_timeout=30,
    ).run()


def markup(app) -> str:
    return "\n".join(str(item.value) for item in app.markdown)


def test_readout_rows_flatten_and_fold_newlines():
    @dataclass(frozen=True)
    class Inner:
        note: str
        amount: Decimal

    @dataclass(frozen=True)
    class Outer:
        name: str
        inner: Inner
        tags: tuple
        missing: object = None

    rows = dict(readout_rows(Outer("a\n\n**x**", Inner("n", Decimal("1.50")), ("p", "q"))))
    assert rows["inner › note"] == "n"
    assert rows["inner › amount"] == "1.50"
    assert rows["tags"] == "p; q"
    assert rows["missing"] == "Not recorded"
    assert "\n" not in rows["name"]


def test_append_resubmit_is_not_duplicated(tmp_path):
    from edge.portfolio_store import PortfolioStore

    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Append record").run()
    app.text_input[0].set_value("2020-01-01T00:00:00+00:00")
    app.text_input[1].set_value("Manual statement")
    app.text_input[2].set_value("100")
    app.button[0].click().run()
    app.button[0].click().run()  # Form keeps its values; a second click must not append again.
    assert not app.exception
    assert len(PortfolioStore(root).list_entries()) == 1
    assert any("not duplicated" in item.value for item in app.info)
    app.selectbox(key="edge_portfolio_action").set_value("Cash deposit").run()
    app.text_input[0].set_value("2020-01-02T00:00:00+00:00")
    app.text_input[1].set_value("Manual statement")
    app.text_input[2].set_value("50")
    app.button[0].click().run()
    assert not app.error
    assert len(PortfolioStore(root).list_entries()) == 2


def test_ledger_and_valued_snapshot_render_tables_and_charts(tmp_path):
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
    app = surface("dashboard_portfolio", root)
    assert not app.exception
    assert app.dataframe  # lots + ledger tables
    assert "Realised" in markup(app)
    now = datetime.now(UTC)
    app.session_state[f"edge_portfolio_observations_{root.resolve()}"] = {
        gbp: PriceQuote(Decimal(7), "GBP", now - timedelta(minutes=1),
                        ManualProvenance("Manual", now - timedelta(seconds=30))),
    }
    app.radio(key="edge_portfolio_task").set_value("Valuation").run()
    assert not app.exception
    assert not app.warning or all("Incomplete valuation" not in w.value for w in app.warning)
    assert any("equity_total_gbp: 166 GBP" in item.value for item in app.text)
    assert "£166.00" in markup(app)


def test_monitoring_board_track_and_alerts_with_symbol(tmp_path):
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
    app = surface("dashboard_monitoring", root)
    assert not app.exception
    assert "THESIS REVIEW" in markup(app)
    assert not store.alerts()  # rendering never records alerts
    next(b for b in app.button if b.label == "Check saved manual evidence now").click().run()
    assert not app.exception
    alerts = store.alerts()
    assert [a.reason for a in alerts] == ["thesis_review_user_declared"]
    table = app.dataframe[-1].value
    assert list(table["Symbol"]) == ["US:ACME"]
    assert list(table["Kind"]) == ["Thesis review"]
    next(b for b in app.button if b.label == "Acknowledge alert").click().run()
    assert not app.exception
    assert store.alerts()[0].acknowledged_at is not None
