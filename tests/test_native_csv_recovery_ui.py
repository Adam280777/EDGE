"""Real parser/store flows with controlled native selections and temporary files."""

import csv
import io
from datetime import UTC, datetime

from streamlit.testing.v1 import AppTest

from edge.data.manual_observations import (
    FUNDAMENTAL_CSV_HEADERS,
    FX_CSV_HEADERS,
    PRICE_CSV_HEADERS,
)


def surface(module, root):
    return AppTest.from_string(
        "from pathlib import Path\n"
        f"from edge.{module} import render\nrender(Path({str(root)!r}))",
        default_timeout=30,
    ).run()


def csv_file(path, headers, values):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    writer.writerow(values)
    path.write_bytes(stream.getvalue().encode("utf-8"))
    return path


def text_input(app, label, value):
    next(item for item in app.text_input if item.label == label).set_value(value)


def button(app, label):
    return next(item for item in app.button if item.label == label)


def declaration(app):
    app.checkbox[0].check()
    app.checkbox[1].check()


def test_price_native_selection_parse_preview_explicit_apply_and_cancel(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    source = csv_file(tmp_path / "manual.csv", PRICE_CSV_HEADERS, {
        "symbol": "TEST", "market": "UK", "currency": "GBP", "exchange": "LSE", "session_date": now[:10],
        "as_of": now, "declared_at": now, "source": "User authored", "source_origin": "user-authored",
        "price_unit": "GBp", "open": "100", "high": "110", "low": "90", "close": "105",
        "volume": "1", "volume_unit": "shares",
    })
    import edge.dashboard_manual_csv as ui
    monkeypatch.setattr(ui, "choose", lambda operation: source)
    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Import CSV").run()
    for label, value in (
        ("CSV ticker / user-declared", "TEST"), ("CSV exchange / exact declared label", "LSE"),
        ("CSV company name / user-declared", "User company"),
    ):
        text_input(app, label, value)
    declaration(app)
    button(app, "Choose native CSV and validate all rows…").click().run()
    assert not app.exception
    assert not app.error, [item.value for item in app.error]
    key = f"edge_portfolio_observations_{root.resolve()}"
    assert key not in app.session_state
    button(app, "Apply selected observation to session valuation").click().run()
    assert not app.exception
    quote = next(iter(app.session_state[key].values()))
    assert quote.unit == "GBp"
    assert str(quote.per_share_money().amount) == "1.05"
    assert not root.exists()
    previous = app.session_state[key]
    monkeypatch.setattr(ui, "choose", lambda operation: None)
    button(app, "Choose native CSV and validate all rows…").click().run()
    assert not app.error, [item.value for item in app.error]
    assert app.session_state[key] == previous
    assert not root.exists()


def test_fx_preview_apply_requires_explicit_direction(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    source = csv_file(tmp_path / "fx.csv", FX_CSV_HEADERS, {
        "from_currency": "USD", "to_currency": "GBP", "rate": "0.8", "as_of": now, "declared_at": now,
        "source": "User authored", "source_origin": "user-authored", "purpose": "cash",
    })
    import edge.dashboard_manual_csv as ui
    monkeypatch.setattr(ui, "choose", lambda operation: source)
    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Import CSV").run()
    app.selectbox[0].set_value("FX").run()
    declaration(app)
    button(app, "Choose native CSV and validate all rows…").click().run()
    assert not app.error, [item.value for item in app.error]
    button(app, "Apply selected observation to session valuation").click().run()
    assert not app.exception
    key = f"edge_portfolio_observations_{root.resolve()}_fx"
    assert str(app.session_state[key][0].rate) == "0.8"
    assert not root.exists()


def test_fundamental_unknown_classification_not_inferred_and_valid_screen(tmp_path, monkeypatch):
    now = datetime.now(UTC).isoformat()
    values = {
        "symbol": "TEST", "market": "UK", "currency": "GBP", "exchange": "LSE", "fact": "margin",
        "value": "0.2", "unit": "fraction", "period_start": "2020-01-01", "period_end": "2020-12-31",
        "value_status": "unspecified", "as_of": now, "declared_at": now,
        "source": "User authored", "source_origin": "user-authored",
    }
    source = csv_file(tmp_path / "fact.csv", FUNDAMENTAL_CSV_HEADERS, values)
    import edge.dashboard_manual_csv as ui
    monkeypatch.setattr(ui, "choose", lambda operation: source)
    root = tmp_path / "data"
    app = surface("dashboard_discovery", root)
    app.radio(key="edge_discovery_task").set_value("Import CSV").run()
    for label, value in (
        ("CSV ticker / user-declared", "TEST"), ("CSV exchange / exact declared label", "LSE"),
        ("CSV company name / user-declared", "User company"),
    ):
        text_input(app, label, value)
    declaration(app)
    button(app, "Choose native CSV and validate all rows…").click().run()
    button(app, "Apply selected manual metric to session universe").click().run()
    assert any("Classification unspecified" in item.value for item in app.error)
    prefix = f"edge_discovery_{root.resolve()}"
    assert prefix not in app.session_state
    values["value_status"] = "reported"
    csv_file(source, FUNDAMENTAL_CSV_HEADERS, values)
    button(app, "Choose native CSV and validate all rows…").click().run()
    button(app, "Apply selected manual metric to session universe").click().run()
    assert app.session_state[prefix][0].observations[0].classification == "claimed-reported"
    app.radio(key="edge_discovery_task").set_value("Screen").run()
    for label, value in (
        ("Filter metric / exact label", "margin"), ("Filter unit / exact label", "fraction"),
        ("Required reporting period / exact label", "2020-01-01 through 2020-12-31"),
        ("Minimum / optional decimal", "0.1"),
        ("Oldest allowed observation / ISO timestamp with UTC offset", "2020-01-01T00:00:00Z"),
    ):
        text_input(app, label, value)
    button(app, "Run deterministic manual screen").click().run()
    assert not app.exception
    assert any("included: True" in item.value for item in app.text)
    assert not root.exists()


def test_rejected_partial_csv_does_not_replace_preview(tmp_path, monkeypatch):
    import edge.dashboard_manual_csv as ui
    source = tmp_path / "invalid.csv"
    source.write_text("wrong,headers\npartial,row\n", encoding="utf-8")
    monkeypatch.setattr(ui, "choose", lambda operation: source)
    root = tmp_path / "data"
    app = surface("dashboard_portfolio", root)
    app.radio(key="edge_portfolio_task").set_value("Import CSV").run()
    app.selectbox[0].set_value("FX").run()
    declaration(app)
    button(app, "Choose native CSV and validate all rows…").click().run()
    assert not app.exception
    assert any("headers" in item.value for item in app.error)
    assert f"edge_portfolio_csv_{root.resolve()}_preview" not in app.session_state
    assert not root.exists()


def test_application_backup_restore_real_store_and_native_cancel(tmp_path, monkeypatch):
    from test_manual_monitoring import rule

    from edge import dashboard_application_backup as ui
    from edge.monitoring_store import MonitoringStore

    app_root = tmp_path / "original"
    app_root.mkdir()
    root = app_root / "data"
    MonitoringStore(root).append_rule(rule())
    archive = tmp_path / "bundle.zip"
    monkeypatch.setattr(ui, "choose", lambda operation: archive)
    app = surface("dashboard_application_backup", root)
    app.button(key="edge_application_backup").click().run()
    assert not app.exception
    assert archive.exists()
    choices = {"bundle-open": archive, "restore-parent": tmp_path}
    monkeypatch.setattr(ui, "choose", choices.__getitem__)
    app.text_input(key="edge_application_restore_leaf").set_value("restored")
    app.button(key="edge_application_restore").click().run()
    assert not app.exception
    assert MonitoringStore(tmp_path / "restored" / "data").rules() == MonitoringStore(root).rules()
    assert (root / "manual-monitoring.sqlite3").exists()
    monkeypatch.setattr(ui, "choose", lambda operation: None)
    before = (root / "manual-monitoring.sqlite3").read_bytes()
    app.button(key="edge_application_backup").click().run()
    assert (root / "manual-monitoring.sqlite3").read_bytes() == before
