"""Local Streamlit workflows with real synthetic engine outputs and temporary storage."""

import sqlite3
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge import simulation_service
from edge.errors import EdgeError
from edge.run_store import RunStore

APP = Path(__file__).resolve().parents[1] / "app.py"


def dashboard(tmp_path: Path) -> AppTest:
    # Invoke the actual entry point with isolated storage, without adding a path
    # picker, environment override, or strategy/code-upload surface to the UI.
    script = (
        "import runpy\n"
        "from pathlib import Path\n"
        f"namespace = runpy.run_path({str(APP)!r})\n"
        f"namespace['main'](Path({str(tmp_path)!r}))\n"
    )
    return AppTest.from_string(script, default_timeout=60).run()


def text(app: AppTest) -> str:
    return (
        "\n".join(
            str(element.value)
            for kind in (
                "title",
                "header",
                "caption",
                "warning",
                "info",
                "markdown",
                "success",
                "error",
            )
            for element in app.get(kind)
        )
        + "\n"
        + "\n".join(element.label for element in app.expander)
    )


def test_startup_has_no_exception_and_never_runs_engine(tmp_path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Rendering must not invoke the engine")

    monkeypatch.setattr(simulation_service, "run", forbidden)
    app = dashboard(tmp_path)
    assert not app.exception
    assert len(app.button) == 2
    assert not (tmp_path / "edge.sqlite3").exists()
    assert "No run in this session" in text(app)
    assert "SYNTHETIC DATA ONLY" in text(app)
    assert "Live trading disabled" in text(app)
    assert "EDGE // Research workbench" in text(app)
    assert any("--edge-amber" in str(item.value) for item in app.markdown)
    assert any("stAlertContentWarning" in str(item.value) for item in app.markdown)
    assert "Planning context" in text(app)
    assert "pending" in text(app)
    assert "OpenBB v5 adapter" in text(app)
    assert "`edge sec-filings TICKER` or a Python API call" in text(app)
    assert "SEC filing research workspace" in text(app)
    assert "no dashboard control" not in text(app)
    assert "not yet been verified against a live SEC response" in text(app)
    assert "Neither is integrated" not in text(app)
    assert "Engine doctor: NautilusTrader 2.0.0rc8" in text(app)


def test_terminal_theme_and_four_pane_structure(tmp_path) -> None:
    app = dashboard(tmp_path)
    assert not app.exception
    assert [header.value for header in app.header] == [
        "Engine acceptance",
        "Synthetic fixture / provenance",
        "Most recent run in this session",
        "Persisted run history",
    ]
    assert not app.sidebar.children
    assert [button.key for button in app.button] == [
        "run_round_trip",
        "run_risk_rejection",
    ]
    assert any(item.label == "Planning context · pending" for item in app.expander)
    assert "Keyboard: Tab" in text(app)
    theme = tomllib.loads(
        (APP.parent / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    )["theme"]
    assert theme == {
        "base": "dark",
        "primaryColor": "#ffb000",
        "backgroundColor": "#050505",
        "secondaryBackgroundColor": "#10100d",
        "textColor": "#ffc15a",
        "font": "monospace",
    }
    css = next(str(item.value) for item in app.markdown if "<style>" in str(item.value))
    for token, value in (
        ("void", theme["backgroundColor"]),
        ("panel", theme["secondaryBackgroundColor"]),
        ("ink", theme["textColor"]),
        ("amber", theme["primaryColor"]),
        ("cyan", "#65dce8"),
        ("success", "#70e49a"),
        ("alert", "#ff817a"),
    ):
        assert f"--edge-{token}: {value};" in css
    assert "border-radius: 0" in css
    assert ":focus-visible" in css
    assert "@media (max-width: 1050px)" in css
    assert ".st-key-edge_acceptance" in css
    assert ".st-key-edge_fixture" in css
    assert ".st-key-edge_recent" in css
    assert ".st-key-edge_history" in css
    assert ':has(> [data-testid="stColumn"] .st-key-edge_acceptance)' in css
    assert "prefers-reduced-motion" in css
    assert "#08131f" not in css
    assert "#63e6ad" not in css
    assert not app.metric  # Never populate decorative terminal readouts.


@pytest.mark.parametrize(
    ("button_key", "scenario", "fill_count", "rejection_count"),
    [
        ("run_round_trip", "round-trip", 2, 0),
        ("run_risk_rejection", "risk-rejection", 0, 1),
    ],
)
def test_scenario_runs_real_engine_once_and_displays_recorded_values(
    tmp_path,
    monkeypatch,
    button_key,
    scenario,
    fill_count,
    rejection_count,
) -> None:
    app = dashboard(tmp_path)
    app.button(key=button_key).click().run()
    assert not app.exception
    assert not app.error
    rows = RunStore(tmp_path).list_runs()
    assert len(rows) == 1
    assert rows[0]["scenario"] == scenario
    assert rows[0]["status"] == "succeeded"
    record = RunStore(tmp_path).get(rows[0]["run_id"])
    outcome = record["result"]["outcome"]
    assert len(outcome["fills"]) == fill_count
    assert len(outcome["rejections"]) == rejection_count
    assert [metric.value for metric in app.metric] == [
        outcome["ending_cash"],
        outcome["realized_pnl"],
        outcome["ending_quantity"],
    ]
    assert app.session_state["edge_recent_run"] == record
    assert record["manifest"]["source"] in text(app)
    assert record["manifest"]["retrieved_at"] in text(app)
    assert [tab.label for tab in app.tabs] == [
        "Orders",
        "Fills",
        "Rejections",
        "Positions",
    ]
    assert any(
        item.label == "Persisted evidence · manifest and result"
        for item in app.expander
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Rerender/history inspection must not invoke the engine")

    monkeypatch.setattr(simulation_service, "run", forbidden)
    app.run()
    assert not app.exception
    assert len(RunStore(tmp_path).list_runs()) == 1
    assert app.session_state["edge_recent_run"] == record
    new_session = dashboard(tmp_path)
    assert not new_session.exception
    assert not new_session.metric
    assert "No run in this session" in text(new_session)
    new_session.selectbox(key="edge_history_selection").select(rows[0]["run_id"]).run()
    assert not new_session.exception
    assert [metric.value for metric in new_session.metric] == [
        outcome["ending_cash"],
        outcome["realized_pnl"],
        outcome["ending_quantity"],
    ]
    assert len(RunStore(tmp_path).list_runs()) == 1


@pytest.mark.parametrize(
    "error",
    [
        EdgeError("controlled failure"),
        OSError("read-only storage"),
        sqlite3.OperationalError("locked database"),
    ],
)
def test_run_errors_are_visible_without_fabricated_outcome(
    tmp_path, monkeypatch, error
) -> None:
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(simulation_service, "run", fail)
    app = dashboard(tmp_path)
    app.button(key="run_round_trip").click().run()
    assert not app.exception
    assert any("Acceptance attempt failed" in item.value for item in app.error)
    assert str(error) in text(app)
    assert not app.metric
    assert "edge_recent_run" not in app.session_state


def test_corrupt_database_is_visible_and_not_reset(tmp_path) -> None:
    database = tmp_path / "edge.sqlite3"
    database.write_bytes(b"not a sqlite database")
    app = dashboard(tmp_path)
    assert not app.exception
    assert any(
        "Run history unavailable or corrupted" in item.value for item in app.error
    )
    assert database.read_bytes() == b"not a sqlite database"


def test_failed_attempt_preserves_previous_session_evidence(
    tmp_path, monkeypatch
) -> None:
    app = dashboard(tmp_path)
    app.button(key="run_round_trip").click().run()
    previous = app.session_state["edge_recent_run"]
    values = [metric.value for metric in app.metric]

    def fail(*args, **kwargs):
        raise EdgeError("controlled retry failure")

    monkeypatch.setattr(simulation_service, "run", fail)
    app.button(key="run_risk_rejection").click().run()
    assert not app.exception
    assert "Acceptance attempt failed" in text(app)
    assert app.session_state["edge_recent_run"] == previous
    assert [metric.value for metric in app.metric] == values
    assert len(RunStore(tmp_path).list_runs()) == 1


def test_engine_doctor_failure_disables_scenarios(tmp_path, monkeypatch) -> None:
    def fail():
        raise EdgeError("engine missing")

    monkeypatch.setattr(simulation_service, "engine_version", fail)
    app = dashboard(tmp_path)
    assert not app.exception
    assert all(button.disabled for button in app.button)
    assert "Engine doctor failed: engine missing" in text(app)
    assert not app.metric
    assert not (tmp_path / "edge.sqlite3").exists()
