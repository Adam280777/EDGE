"""Actual render tests with vault/provider injection; no real vault or network."""

import socket
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

import edge.dashboard_provider as ui
from edge.data.fmp_provider import API_KEY_ENV_VAR, API_VERSION, ENDPOINTS
from edge.data.market_data import (
    MarketDataAccessError,
    MarketDataDataError,
    MarketDataRateLimitError,
    ProviderInfo,
    ProviderPayload,
)
from edge.provider_settings import ProviderSettings, settings_path
from edge.windows_credentials import CredentialStatus

SECRET = "offline-key-never-session"


class Provider:
    name = "fmp"

    def describe(self):
        return ProviderInfo(self.name, API_VERSION, ENDPOINTS)

    def fetch(self, request, config, acknowledgement):
        now = datetime.now(UTC)
        return ProviderPayload(
            profile={
                "symbol": request.symbol,
                "companyName": "Offline Test Company",
                "exchange": "NASDAQ",
                "country": "US",
                "currency": "USD",
                "isEtf": False,
                "isFund": False,
                "isAdr": False,
                "isActivelyTrading": True,
                "cik": "12345",
                "isin": None,
                "cusip": None,
            },
            bars=(
                {
                    "symbol": request.symbol,
                    "date": request.end_date.isoformat(),
                    "open": Decimal("100.125"),
                    "high": Decimal("102.25"),
                    "low": Decimal("99.1"),
                    "close": Decimal("101.375"),
                    "volume": 200,
                    "vwap": None,
                    "adjClose": None,
                },
            ),
            splits=(),
            dividends=(),
            endpoints=ENDPOINTS,
            api_version=API_VERSION,
            retrieved_at_start=now,
            retrieved_at_end=now,
        )


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Real network/vault access forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    real_connect = socket.socket.connect

    def loopback_only(sock, address):
        if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            blocked()
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(
        "edge.windows_credentials.WindowsCredentialManager._load", blocked
    )
    vault = Mock()
    vault.read.return_value = SECRET
    factory = Mock(return_value=Provider())
    monkeypatch.setattr(ui, "WindowsCredentialManager", Mock(return_value=vault))
    monkeypatch.setattr(ui, "FmpProvider", factory)
    return vault, factory


def app(tmp_path):
    result = AppTest.from_string(
        "from pathlib import Path\n"
        "from edge.dashboard_provider import render\n"
        f"render(Path({str(tmp_path / 'data')!r}))\n",
        default_timeout=15,
    ).run()
    assert not result.exception
    return result


def button(result, label):
    return next(item for item in result.button if item.label == label)


def text(result):
    return "\n".join(
        str(item.value)
        for kind in ("text", "caption", "info", "warning", "error")
        for item in getattr(result, kind)
    )


def submit(result):
    result.checkbox[0].check().run()
    result.checkbox[1].check().run()
    result.text_input[0].set_value("AAPL")
    button(result, "Fetch validated ephemeral prices").click().run()
    assert not result.exception
    return result


def test_render_edits_reruns_no_secret_reads_no_writes(tmp_path, offline):
    vault, factory = offline
    result = app(tmp_path)
    assert all(not item.value for item in result.checkbox)
    assert button(result, "Fetch validated ephemeral prices").disabled
    result.text_input[0].set_value("AAPL").run()
    result.number_input[0].set_value(20).run()
    result.run()
    vault.read.assert_not_called()
    factory.assert_not_called()
    assert not result.get("vega_lite_chart")
    assert list(tmp_path.iterdir()) == []


def test_actual_success_snapshot_decimal_flags_no_exports_no_relabel(tmp_path, offline):
    vault, factory = offline
    result = submit(app(tmp_path))
    assert ui.RESULT_KEY in result.session_state
    snapshot = result.session_state[ui.RESULT_KEY]
    assert snapshot.snapshot.bars[0].close == Decimal("101.375")
    assert snapshot.snapshot.use_policy.research_display_eligible
    assert not snapshot.snapshot.calculation_eligible
    assert not snapshot.snapshot.storage_allowed
    assert "publication_time" in text(result)
    assert "unverified_no_exchange_calendar" in text(result)
    assert "documentary_close_fields_only_not_provider_verified" in text(result)
    assert SECRET not in text(result)
    assert SECRET not in repr(result.session_state)
    assert not result.get("download_button")
    assert result.get("vega_lite_chart")
    assert list(tmp_path.iterdir()) == []
    factory.assert_called_once_with(environ={API_KEY_ENV_VAR: SECRET})
    result.text_input[0].set_value("MSFT").run()
    result.selectbox[1].set_value("premium").run()
    assert result.session_state[ui.RESULT_KEY] == snapshot
    assert "AAPL" in text(result) and "MSFT" not in text(result)
    assert snapshot.settings.plan_label == "unspecified"
    vault.read.assert_called_once()


def test_settings_save_only_writes_nonsecret_not_fetch(tmp_path, offline):
    vault, factory = offline
    result = app(tmp_path)
    result.selectbox[1].set_value("premium")
    button(result, "Save non-secret settings").click().run()
    assert not result.exception
    assert settings_path(tmp_path / "data").exists()
    assert not (tmp_path / "data").exists()
    assert SECRET not in settings_path(tmp_path / "data").read_text()
    vault.read.assert_not_called()
    factory.assert_not_called()


def test_missing_key_status_throttle_clears_success(tmp_path, offline):
    vault, factory = offline
    result = submit(app(tmp_path))
    button(result, "Fetch validated ephemeral prices").click().run()
    assert ui.RESULT_KEY not in result.session_state
    assert "Local request budget" in text(result)
    assert vault.read.call_count == 1
    factory.assert_called_once()
    result.session_state[ui.THROTTLE_KEY] = (0, None)
    vault.read.return_value = None
    button(result, "Fetch validated ephemeral prices").click().run()
    assert "FMP key missing" in text(result)
    assert not result.get("vega_lite_chart")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error",
    [
        MarketDataAccessError(SECRET),
        MarketDataRateLimitError(SECRET),
        MarketDataDataError(SECRET),
        RuntimeError(SECRET),
    ],
)
def test_errors_double_safe_redacted_and_clear_success(tmp_path, offline, error):
    _vault, factory = offline
    result = submit(app(tmp_path))
    result.session_state[ui.THROTTLE_KEY] = (0, None)
    factory.side_effect = error
    button(result, "Fetch validated ephemeral prices").click().run()
    assert not result.exception
    assert ui.RESULT_KEY not in result.session_state
    assert SECRET not in text(result)
    assert SECRET not in repr(result.session_state)
    assert not result.get("vega_lite_chart")


def test_declarations_gate_before_key_read_and_clear_old_result(tmp_path, offline):
    vault, _factory = offline
    result = submit(app(tmp_path))
    result.checkbox[0].uncheck().run()
    assert vault.read.call_count == 1
    assert ui.RESULT_KEY not in result.session_state
    assert button(result, "Fetch validated ephemeral prices").disabled


def test_forged_server_submit_gates_before_key_read(monkeypatch, offline):
    state = {ui.RESULT_KEY: "old"}
    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state=state))
    ui._fetch(
        ProviderSettings(),
        "AAPL",
        date(2024, 1, 2),
        date(2024, 1, 3),
        False,
        True,
    )
    offline[0].read.assert_not_called()
    offline[1].assert_not_called()
    assert ui.RESULT_KEY not in state
    assert "Confirm both rights" in state[ui.ERROR_KEY]


@pytest.mark.parametrize(
    "personal,entitled", [(False, False), (True, False), (False, True)]
)
def test_all_declaration_combinations_before_vault(
    monkeypatch, offline, personal, entitled
):
    state = {ui.RESULT_KEY: "old"}
    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state=state))
    ui._fetch(
        ProviderSettings(),
        "AAPL",
        date(2024, 1, 2),
        date(2024, 1, 3),
        personal,
        entitled,
    )
    assert ui.RESULT_KEY not in state
    offline[0].read.assert_not_called()
    offline[1].assert_not_called()


@pytest.mark.parametrize(
    "symbol,start,end",
    [
        ("INVALID-TICKER", date(2024, 1, 2), date(2024, 1, 3)),
        ("AAPL", date(2024, 1, 3), date(2024, 1, 2)),
        ("AAPL", date(2020, 1, 2), date(2024, 1, 2)),
        ("AAPL", date(2099, 1, 2), date(2099, 1, 3)),
    ],
)
def test_date_and_ticker_bounds_before_vault(monkeypatch, offline, symbol, start, end):
    state = {ui.RESULT_KEY: "old"}
    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state=state))
    ui._fetch(ProviderSettings(), symbol, start, end, True, True)
    assert ui.RESULT_KEY not in state
    assert "Invalid ticker or date range" in state[ui.ERROR_KEY]
    assert ui.THROTTLE_KEY not in state
    offline[0].read.assert_not_called()
    offline[1].assert_not_called()


def test_session_budget_before_vault_and_draft_edit_no_sideeffects(tmp_path, offline):
    result = submit(app(tmp_path))
    result.session_state[ui.THROTTLE_KEY] = (3, None)
    button(result, "Fetch validated ephemeral prices").click().run()
    assert ui.RESULT_KEY not in result.session_state
    assert "Local request budget" in text(result)
    offline[0].read.assert_called_once()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("status", list(CredentialStatus) + [SECRET])
def test_native_all_statuses_are_fixed_safe_no_writes(
    tmp_path, offline, monkeypatch, status
):
    dialog = Mock(return_value=status)
    monkeypatch.setattr(ui, "credential_dialog", dialog)
    result = app(tmp_path)
    button(result, "Save or replace FMP credential").click().run()
    assert not result.exception
    assert SECRET not in text(result)
    assert SECRET not in repr(result.session_state)
    offline[0].read.assert_not_called()
    offline[1].assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_runtime_mapping_never_mutates_environment(tmp_path, offline, monkeypatch):
    import os

    monkeypatch.setenv(API_KEY_ENV_VAR, "offline-environment-must-not-be-used")
    before = dict(os.environ)
    result = submit(app(tmp_path))
    assert ui.RESULT_KEY in result.session_state
    assert dict(os.environ) == before
    offline[1].assert_called_once_with(environ={API_KEY_ENV_VAR: SECRET})


def test_clear_result_is_inert_and_no_new_chart(tmp_path, offline):
    result = submit(app(tmp_path))
    button(result, "Clear ephemeral result").click().run()
    assert ui.RESULT_KEY not in result.session_state
    assert not result.get("vega_lite_chart")
    offline[0].read.assert_called_once()
    offline[1].assert_called_once()
    assert list(tmp_path.iterdir()) == []


def test_settings_submit_does_not_relabel_or_refetch_old_snapshot(tmp_path, offline):
    result = submit(app(tmp_path))
    original = result.session_state[ui.RESULT_KEY]
    result.selectbox[1].set_value("premium")
    result.number_input[1].set_value(2)
    button(result, "Save non-secret settings").click().run()
    assert result.session_state[ui.RESULT_KEY] == original
    assert original.settings.plan_label == "unspecified"
    assert original.settings.timeout_seconds == 10
    assert result.session_state[ui.SETTINGS_KEY].plan_label == "premium"
    assert result.session_state[ui.SETTINGS_KEY].timeout_seconds == 2
    offline[0].read.assert_called_once()
    offline[1].assert_called_once()


def test_action_provider_text_is_literal_not_markdown_or_download(tmp_path, offline):
    class Actions(Provider):
        def fetch(self, request, config, acknowledgement):
            payload = super().fetch(request, config, acknowledgement)
            return replace(
                payload,
                splits=(
                    {
                        "symbol": request.symbol,
                        "date": request.end_date.isoformat(),
                        "numerator": 2,
                        "denominator": 1,
                        "splitType": "[untrusted](https://invalid.example/)",
                    },
                ),
            )

    offline[1].return_value = Actions()
    result = submit(app(tmp_path))
    assert ui.RESULT_KEY in result.session_state
    assert "[untrusted](https://invalid.example/)" in text(result)
    assert not result.get("download_button")
    assert list(tmp_path.iterdir()) == []


def test_native_dialog_status_only_cancel_remove_no_vault_probe(
    tmp_path, offline, monkeypatch
):
    vault, factory = offline
    dialog = Mock(return_value=CredentialStatus.CANCELLED)
    monkeypatch.setattr(ui, "credential_dialog", dialog)
    result = app(tmp_path)
    button(result, "Save or replace FMP credential").click().run()
    assert "cancelled" in text(result)
    button(result, "Remove FMP credential").click().run()
    assert dialog.call_args.args == ("remove",)
    vault.read.assert_not_called()
    factory.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_corrupt_settings_fail_closed_without_reads(tmp_path, offline):
    path = settings_path(tmp_path / "data")
    path.parent.mkdir()
    path.write_text('{"api_key":"offline-key-never-session"}')
    result = app(tmp_path)
    assert "No fallback or fetch" in text(result)
    assert SECRET not in text(result)
    assert len(result.button) == 0
    offline[0].read.assert_not_called()


def test_secret_reflected_in_provider_data_rejected_before_session(tmp_path, offline):
    class Reflected(Provider):
        def fetch(self, *args):
            payload = super().fetch(*args)
            return replace(payload, profile={**payload.profile, "companyName": SECRET})

    offline[1].return_value = Reflected()
    result = submit(app(tmp_path))
    assert ui.RESULT_KEY not in result.session_state
    assert SECRET not in text(result)
    assert SECRET not in repr(result.session_state)
    assert list(tmp_path.iterdir()) == []
