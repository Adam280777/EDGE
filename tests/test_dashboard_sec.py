"""Offline native-form/navigation tests; synthetic mocks never ship in the app."""

import json
import socket
from dataclasses import asdict
from datetime import UTC, date, datetime
from unittest.mock import Mock

import pytest

st = pytest.importorskip("streamlit")

from test_dashboard import dashboard, text

import edge.data
from edge.data.sec_filings import (
    SecFilingConfigurationError,
    SecFilingDataError,
    SecFilingInputError,
    SecFilingNotFoundError,
    SecFilingProviderError,
    SecProviderInfo,
    SecProviderPayload,
)

UA = "Dashboard Test dashboard@edge-research-tests.org"
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
ACCESSION = "0000320193-24-000123"
BASE = "https://www.sec.gov/Archives/edgar/data/320193/"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("SEC dashboard tests must never request network data")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    real_connect = socket.socket.connect

    def loopback_only(sock, address):
        if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            blocked()
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.delenv("EDGE_SEC_USER_AGENT", raising=False)


class Provider:
    def describe(self):
        return SecProviderInfo(
            "test-provider", "test-version", "test-framework", ("fixture://sec",)
        )

    def lookup(self, sec_ticker, *, user_agent):
        return SecProviderPayload(
            "320193",
            "AAPL",
            (
                {
                    "accession_number": ACCESSION,
                    "report_type": "10-K",
                    "filing_date": date(2024, 11, 1),
                    "report_date": None,
                    "accepted_date": None,
                    "primary_doc": None,
                    "report_url": None,
                    "filing_detail_url": f"{BASE}{ACCESSION}-index.htm",
                },
            ),
            (f"Provider warning {user_agent}",),
        )


def install_lookup(monkeypatch):
    real = edge.data.lookup_sec_filings

    def lookup(ticker, **kwargs):
        return real(
            ticker,
            **kwargs,
            environ={"EDGE_SEC_USER_AGENT": UA},
            provider=Provider(),
            clock=lambda: NOW,
        )

    mock = Mock(side_effect=lookup)
    monkeypatch.setattr(edge.data, "lookup_sec_filings", mock)
    return mock


def research(tmp_path):
    app = dashboard(tmp_path)
    app.radio(key="edge_workspace").set_value("SEC filing research").run()
    assert not app.exception
    return app


def submit(app, ticker="aapl"):
    app.text_input(key="edge_sec_ticker").set_value(ticker)
    next(
        button for button in app.button if button.label == "Look up SEC filings"
    ).click().run()
    assert not app.exception
    return app


def evidence(app):
    return json.loads(app.session_state["edge_sec_evidence"])


def test_navigation_rerender_edits_are_inert_and_no_writes(tmp_path, monkeypatch):
    mock = install_lookup(monkeypatch)
    app = research(tmp_path)
    assert "FILING METADATA ONLY" in text(app)
    assert "SYNTHETIC DATA ONLY" not in text(app)
    assert "No SEC lookup" in text(app)
    app.text_input(key="edge_sec_ticker").set_value("aapl").run()
    app.run()
    app.radio(key="edge_workspace").set_value("Engine acceptance").run()
    assert "SYNTHETIC DATA ONLY" in text(app)
    app.radio(key="edge_workspace").set_value("SEC filing research").run()
    mock.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_success_snapshot_nulls_download_contract_and_navigation(tmp_path, monkeypatch):
    mock = install_lookup(monkeypatch)
    dataframe = Mock(wraps=st.dataframe)
    monkeypatch.setattr(st, "dataframe", dataframe)
    app = submit(research(tmp_path))
    result = evidence(app)
    assert result["identity"]["requested_ticker"] == "AAPL"
    assert result["identity"]["cik"] == "0000320193"
    assert result["filings"][0]["primary_document_url"] is None
    assert result["filings"][0]["report_date"] is None
    assert result["filings"][0]["accepted_at"] is None
    assert result["filings"][0]["filing_index_url"] == f"{BASE}{ACCESSION}-index.htm"
    assert result["provider_record_count"] == 1
    assert result["excluded_record_count"] == 0
    assert result["coverage_may_be_incomplete"] is True
    assert "Coverage is limited" in text(app)
    assert result["provenance"]["provider_version"] == "test-version"
    assert result["provenance"]["framework_version"] == "test-framework"
    assert result["provenance"]["retrieved_at_start"] == NOW.isoformat()
    assert result["provenance"]["retrieved_at_end"] == NOW.isoformat()
    assert result["provenance"]["endpoints"] == ["fixture://sec"]
    assert UA not in app.session_state["edge_sec_evidence"]
    assert "dashboard@edge-research-tests.org" not in text(app)
    assert len(app.get("download_button")) == 1
    assert app.get("download_button")[0].proto.ignore_rerun
    assert not app.get("form")[0].proto.form.enter_to_submit
    assert (
        app.get("download_button")[0].proto.label
        == "Download SEC metadata evidence (JSON)"
    )
    assert '"filing_index_url"' in app.dataframe[0].proto.columns
    assert "SEC URLs are plain text for selection and copying" in text(app)
    assert "Open SEC index" not in text(app)
    column_config = dataframe.call_args.kwargs["column_config"]
    assert {
        name: config["type_config"]["type"]
        for name, config in column_config.items()
    } == {"filing_index_url": "text", "primary_document_url": "text"}

    # Draft edits, rendering, navigation and disclosure never refresh the lookup.
    snapshot = app.session_state["edge_sec_evidence"]
    app.text_input(key="edge_sec_ticker").set_value("msft").run()
    assert "Submitted ticker: AAPL" in text(app)
    app.radio(key="edge_workspace").set_value("Engine acceptance").run()
    app.radio(key="edge_workspace").set_value("SEC filing research").run()
    app.run()
    assert app.session_state["edge_sec_evidence"] == snapshot
    assert mock.call_count == 1
    assert list(tmp_path.iterdir()) == []
    assert "No SEC lookup" in text(research(tmp_path))  # Other sessions are isolated.


def test_sec_workspace_never_uses_simulation_store(tmp_path, monkeypatch):
    app = dashboard(tmp_path)
    mock = install_lookup(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("SEC research must not touch the simulation engine or store")

    monkeypatch.setattr("edge.run_store.RunStore", forbidden)
    monkeypatch.setattr("edge.simulation_service.run", forbidden)
    app.radio(key="edge_workspace").set_value("SEC filing research").run()
    submit(app)
    app.run()
    assert not app.exception
    assert mock.call_count == 1
    assert list(tmp_path.iterdir()) == []


def test_filter_forwarding_and_empty_state(tmp_path, monkeypatch):
    mock = install_lookup(monkeypatch)
    app = research(tmp_path)
    app.multiselect(key="edge_sec_forms").set_value(["10-Q", "8-K/A"])
    app.text_input(key="edge_sec_start").set_value("2024-01-01")
    app.text_input(key="edge_sec_end").set_value("2024-12-31")
    submit(app, " brk.b ")
    mock.assert_called_once_with(
        " brk.b ",
        form_types=["10-Q", "8-K/A"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
    )
    result = evidence(app)
    assert result["identity"]["requested_ticker"] == "BRK.B"
    assert result["identity"]["sec_ticker"] == "BRK-B"
    assert result["filings"] == []
    assert result["requested_form_types"] == ["10-Q", "8-K/A"]
    assert result["start_date"] == "2024-01-01"
    assert result["end_date"] == "2024-12-31"
    assert result["excluded_record_count"] == 1
    assert "No filings match these submitted forms" in text(app)
    assert not app.dataframe


def test_missing_configuration_checks_without_provider_import(tmp_path, monkeypatch):
    import builtins

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert "openbb" not in name
        assert name != "edge.data.openbb_sec_provider"
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    app = submit(research(tmp_path))
    assert "Set a valid EDGE_SEC_USER_AGENT" in text(app)
    assert not app.exception
    assert not app.get("download_button")
    assert "edge_sec_evidence" not in app.session_state
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (SecFilingConfigurationError, "configuration unavailable"),
        (SecFilingInputError, "Invalid lookup inputs"),
        (SecFilingNotFoundError, "not found"),
        (SecFilingDataError, "failed validation"),
        (SecFilingProviderError, "request failed or timed out"),
        (ImportError, "provider unavailable"),
        (OSError, "provider unavailable"),
        (RuntimeError, "safe metadata evidence"),
    ],
)
def test_failed_attempt_clears_previous_success_and_redacts(
    tmp_path, monkeypatch, error_type, expected
):
    mock = install_lookup(monkeypatch)
    app = submit(research(tmp_path))
    monkeypatch.setenv("EDGE_SEC_USER_AGENT", f"  {UA}  ")
    mock.side_effect = error_type(f"Headers: {UA}, secret query, other@private.org")
    submit(app, "msft")
    assert expected in text(app)
    assert UA not in text(app)
    assert "other@private.org" not in text(app)
    assert "secret query" not in text(app)
    assert "edge_sec_evidence" not in app.session_state
    assert not app.dataframe
    assert not app.get("download_button")
    assert "latest attempt failed" in text(app)
    assert mock.call_count == 2
    app.run()
    assert mock.call_count == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "value", ["20241101", "2024-W44-5", "2024-02-30", "contact@private.org"]
)
def test_invalid_date_is_local_and_contact_safe(tmp_path, monkeypatch, value):
    mock = install_lookup(monkeypatch)
    app = research(tmp_path)
    app.text_input(key="edge_sec_start").set_value(value)
    submit(app)
    mock.assert_not_called()
    assert "Use ISO calendar dates" in text(app)
    assert value not in text(app)


def test_json_date_contract_matches_cli_without_import_in_app():
    from edge.cli import _json_default
    from edge.dashboard_sec import _json_date

    result = edge.data.lookup_sec_filings(
        "aapl",
        environ={"EDGE_SEC_USER_AGENT": UA},
        provider=Provider(),
        clock=lambda: NOW,
    )
    assert json.dumps(asdict(result), default=_json_date) == json.dumps(
        asdict(result), default=_json_default
    )
    for unsupported in (object(), datetime(2024, 1, 1)):
        with pytest.raises((TypeError, ValueError)):
            _json_date(unsupported)
