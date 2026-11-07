"""Explicit, ephemeral FMP research display for the local desktop workspace."""

# ruff: noqa: BLE001
# Defense-in-depth presentation boundaries suppress arbitrary exception text.

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import streamlit as st

from edge.data.fmp_provider import API_KEY_ENV_VAR, FmpProvider
from edge.data.market_data import (
    DataUseAcknowledgement,
    MarketDataAccessError,
    MarketDataDataError,
    MarketDataInputError,
    MarketDataNotFoundError,
    MarketDataRateLimitError,
    MarketDataSnapshot,
    assess_acceptance,
    fetch_daily_snapshot,
    require_acknowledgement,
    validate_request,
)
from edge.provider_settings import (
    PLAN_LABELS,
    PROVIDERS,
    ProviderSettings,
    load_settings,
    save_settings,
)
from edge.windows_credentials import (
    CredentialStatus,
    WindowsCredentialManager,
    credential_dialog,
)

RESULT_KEY = "edge_provider_snapshot"
ERROR_KEY = "edge_provider_error"
SETTINGS_KEY = "edge_provider_settings"
THROTTLE_KEY = "edge_provider_budget"
STATUS_KEY = "edge_provider_credential_status"
ROOT_KEY = "edge_provider_root"
_MESSAGES = {
    CredentialStatus.SAVED: "FMP credential saved in Windows Credential Manager.",
    CredentialStatus.REMOVED: "FMP credential removed from Windows Credential Manager.",
    CredentialStatus.CANCELLED: "Credential dialog cancelled; no change made.",
    CredentialStatus.BUSY: "Another native credential dialog is open. Close it and retry.",
    CredentialStatus.UNAVAILABLE: "Native Windows credential entry is unavailable.",
    CredentialStatus.FAILED: (
        "Credential operation failed or timed out. A previously confirmed native "
        "change may have completed; retry save or removal explicitly."
    ),
}


@dataclass(frozen=True)
class SubmittedSnapshot:
    settings: ProviderSettings
    snapshot: MarketDataSnapshot


def _fetch(
    settings: ProviderSettings,
    symbol: str,
    start: date,
    end: date,
    personal: bool,
    entitled: bool,
) -> None:
    """Only explicit fetch calls read a key; every failure erases prior success."""
    st.session_state.pop(RESULT_KEY, None)
    st.session_state.pop(ERROR_KEY, None)
    secret = None
    provider = None
    try:
        settings.validate()
        acknowledgement = DataUseAcknowledgement(personal, entitled)
        require_acknowledgement(acknowledgement)  # Before vault access.
        validate_request(
            symbol, start, end, config=settings.config(), today=datetime.now(UTC).date()
        )
        now = time.monotonic()
        count, previous = st.session_state.get(THROTTLE_KEY, (0, None))
        if count >= settings.max_snapshots_per_session or (
            previous is not None and now - previous < settings.cooldown_seconds
        ):
            st.session_state[ERROR_KEY] = (
                "Local request budget reached. Wait for the cooldown or restart "
                "the session after its limit; no credential was read or request made."
            )
            return
        # Attempts consume the independent local budget, including missing keys.
        st.session_state[THROTTLE_KEY] = (count + 1, now)
        secret = WindowsCredentialManager().read()
        if not secret:
            st.session_state[ERROR_KEY] = (
                "FMP key missing. Save it using the native Windows credential dialog, "
                "then explicitly fetch again after the cooldown."
            )
            return
        provider = FmpProvider(environ={API_KEY_ENV_VAR: secret})
        with st.spinner("Fetching and validating four FMP endpoints…"):
            snapshot = fetch_daily_snapshot(
                symbol,
                start,
                end,
                acknowledgement=acknowledgement,
                config=settings.config(),
                provider=provider,
            )
            # Defense beyond adapter sanitation, including all provider text.
            if not isinstance(snapshot, MarketDataSnapshot) or secret in repr(snapshot):
                raise MarketDataDataError("Unsafe output.")
            if not snapshot.use_policy.research_display_eligible:
                raise MarketDataDataError("Display not eligible.")
            report = assess_acceptance(snapshot)
            if (
                "private_ephemeral_display" not in report.eligible_intended_uses
                or secret in repr(report)
            ):
                raise MarketDataDataError("Display acceptance not eligible.")
            submitted = SubmittedSnapshot(settings, snapshot)
            if secret in repr(submitted):
                raise MarketDataDataError("Unsafe output.")
            st.session_state[RESULT_KEY] = submitted
    except MarketDataRateLimitError:
        st.session_state[ERROR_KEY] = (
            "FMP rate or quota limit reached. No result retained; explicitly retry later."
        )
    except MarketDataAccessError:
        st.session_state[ERROR_KEY] = (
            "FMP denied credential or endpoint access. Check your account and native "
            "credential; a plan label never grants access."
        )
    except MarketDataInputError:
        st.session_state[ERROR_KEY] = (
            "Invalid ticker or date range. Use a US ticker, ordered historical dates "
            "and a range within the submitted ceiling."
        )
    except (MarketDataDataError, MarketDataNotFoundError):
        st.session_state[ERROR_KEY] = (
            "FMP identity or price evidence failed validation or was not found. "
            "No previous result is retained."
        )
    except Exception:
        # Fixed vocabulary only: never raw exception, payload or request headers.
        st.session_state[ERROR_KEY] = (
            "Fetch unavailable. Confirm both rights declarations, settings, Windows "
            "credential and connectivity before explicitly retrying. No result retained."
        )
    finally:
        # Neither adapter nor runtime credential mapping is retained in session.
        secret = None
        provider = None


def _literal(value):
    """Presentation only: exact Decimal strings, UTC timestamps, no finance math."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _literal(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_literal(item) for item in value]
    return value


def _readout(value) -> None:
    """Compact, wrapping literal-text rows; no provider Markdown or link parsing."""
    fields = _literal(asdict(value))
    st.text("\n".join(f"{name}: {item}" for name, item in fields.items()))


def _results(submitted: SubmittedSnapshot) -> None:
    snapshot = submitted.snapshot
    st.subheader("Submitted snapshot · historical daily prices")
    st.caption(
        "This immutable result belongs to its submitted settings and dates, not "
        "draft edits. It is not current, realtime or point-in-time verified."
    )
    _readout(submitted.settings)
    st.subheader("Validated security / currency and units")
    _readout(snapshot.identity)
    st.subheader("Source / UTC retrieval and publication uncertainty")
    _readout(snapshot.provenance)
    st.caption(
        "Bar dates are exchange session-date labels, not UTC observation timestamps. "
        "UTC retrieval is not provider publication time or a freshness guarantee."
    )
    st.warning(
        "Main market-data acceptance remains unresolved. This is private, local, "
        "ephemeral research only. No calculations, backtests, Nautilus, storage, "
        "exports, caching or redistribution are enabled."
    )
    _readout(snapshot.use_policy)
    st.text(
        f"Snapshot calculation_eligible: {snapshot.calculation_eligible}; "
        f"storage_allowed: {snapshot.storage_allowed}"
    )
    st.subheader("Acceptance evidence / outstanding gates")
    report = assess_acceptance(snapshot)
    st.text(f"Eligible intended uses: {report.eligible_intended_uses}")
    st.text(f"Outstanding gate IDs: {report.blockers}")
    with st.expander("Complete acceptance gate evidence"):
        _readout(report)
    st.subheader("Adjustment / actions / exchange-calendar limitations")
    for warning in snapshot.warnings:
        st.text(warning)  # Provider text is literal, never Markdown/HTML.
    _readout(snapshot.session_evidence)
    for evidence in snapshot.action_evidence:
        _readout(evidence)
    if snapshot.actions:
        with st.expander("Returned in-window corporate actions"):
            for action in snapshot.actions:
                _readout(action)  # splitType is provider text; never table Markdown.
    else:
        st.info(
            "No in-window actions returned; this does not prove action completeness."
        )
    if snapshot.bars and snapshot.use_policy.research_display_eligible:
        st.subheader("Validated closes / unknown adjustment basis")
        # Exact reported values are supplied as decimal strings; chart positioning
        # is visual only. No returns, averages, signals or adjusted finance derived.
        st.vega_lite_chart(
            {
                "data": {
                    "values": [
                        {
                            "session": bar.session_date.isoformat(),
                            "close": str(bar.close),
                        }
                        for bar in snapshot.bars
                    ]
                },
                "mark": {"type": "line", "color": "#ffc15a", "point": True},
                "encoding": {
                    "x": {
                        "field": "session",
                        "type": "ordinal",
                        "title": "Session date",
                    },
                    "y": {
                        "field": "close",
                        "type": "quantitative",
                        "title": f"Close · {snapshot.identity.currency} · {snapshot.identity.price_unit}",
                        "scale": {"zero": False},
                    },
                },
                "usermeta": {"embedOptions": {"actions": False}},
            },
            width="stretch",
        )
        # st.table has no CSV download toolbar, unlike st.dataframe.
        st.table([_literal(asdict(bar)) for bar in snapshot.bars])


def render(data_root: Path) -> None:
    st.header("Provider settings / ephemeral prices")
    st.caption(
        "FMP is the only implemented provider. No automatic requests occur on "
        "navigation, edits, settings save or rerun. Keys never enter browser fields."
    )
    root = str(data_root.absolute())
    if st.session_state.get(ROOT_KEY) != root:
        for key in (RESULT_KEY, ERROR_KEY, SETTINGS_KEY, THROTTLE_KEY, STATUS_KEY):
            st.session_state.pop(key, None)
        st.session_state[ROOT_KEY] = root
    try:
        if SETTINGS_KEY not in st.session_state:
            st.session_state[SETTINGS_KEY] = load_settings(data_root)
        settings = st.session_state[SETTINGS_KEY]
        settings.validate()
    except Exception:
        st.error(
            "Provider settings are invalid or unreadable. No fallback or fetch is "
            "enabled. Repair or remove the non-secret provider-v1.json in the "
            "sibling settings directory, then restart."
        )
        return
    st.subheader("Non-secret request settings")
    st.caption(
        "First-run defaults apply only when no file exists. Saved settings live "
        "beside the data folder, never in a data database. Plan is descriptive only."
    )
    with st.form("edge_provider_settings_form", enter_to_submit=False):
        selected = st.selectbox("Implemented provider", PROVIDERS)
        plan = st.selectbox(
            "Descriptive plan label · does not grant endpoint access",
            PLAN_LABELS,
            index=PLAN_LABELS.index(settings.plan_label),
        )
        days = st.number_input(
            "Maximum history range (calendar days)", 1, 366, settings.max_history_days
        )
        timeout = st.number_input(
            "Per-request timeout (seconds)", 1, 10, settings.timeout_seconds
        )
        size = st.number_input(
            "Maximum response bytes per request",
            1024,
            4194304,
            settings.max_response_bytes,
        )
        cooldown = st.number_input(
            "Minimum interval between attempts (seconds)",
            30,
            3600,
            settings.cooldown_seconds,
        )
        budget = st.number_input(
            "Maximum attempts per session", 1, 10, settings.max_snapshots_per_session
        )
        st.caption("Every snapshot requires exactly four requests; no retries.")
        saved = st.form_submit_button("Save non-secret settings")
    if saved:
        try:
            submitted_settings = ProviderSettings(
                provider=selected,
                plan_label=plan,
                max_history_days=days,
                timeout_seconds=timeout,
                max_response_bytes=size,
                cooldown_seconds=cooldown,
                max_snapshots_per_session=budget,
            )
            save_settings(data_root, submitted_settings)
            st.session_state[SETTINGS_KEY] = settings = submitted_settings
            st.success("Non-secret settings saved. No credential read or request made.")
        except Exception:
            st.error("Settings were not saved. Check local permissions and limits.")
    st.subheader("Windows credential / native entry only")
    st.caption(
        "Vault presence is not checked here. Save opens a masked native Tk field "
        "with paste support; save and removal require native confirmation."
    )
    if st.button("Save or replace FMP credential", key="edge_provider_save_key"):
        status = credential_dialog("save")
        st.session_state[STATUS_KEY] = _MESSAGES.get(
            status, _MESSAGES[CredentialStatus.FAILED]
        )
    if st.button("Remove FMP credential", key="edge_provider_remove_key"):
        status = credential_dialog("remove")
        st.session_state[STATUS_KEY] = _MESSAGES.get(
            status, _MESSAGES[CredentialStatus.FAILED]
        )
        st.session_state.pop(RESULT_KEY, None)
    if STATUS_KEY in st.session_state:
        st.info(st.session_state[STATUS_KEY])
    st.subheader("Explicit authorized fetch")
    st.caption(
        "Confirm rights for profile, historical-price-eod/full, splits and "
        "dividends. Declarations are not independent legal or endpoint clearance."
    )
    personal = st.checkbox("I have rights for private personal research", value=False)
    entitled = st.checkbox(
        "My FMP account is entitled to all four requested endpoints", value=False
    )
    if not (personal and entitled):
        st.session_state.pop(RESULT_KEY, None)
    with st.form("edge_provider_fetch_form", enter_to_submit=False):
        symbol = st.text_input("US-listed ticker", max_chars=9)
        today = datetime.now(UTC).date()
        start = st.date_input(
            "Historical start / session date", today - timedelta(days=30)
        )
        end = st.date_input("Historical end / session date", today - timedelta(days=1))
        fetch = st.form_submit_button(
            "Fetch validated ephemeral prices", disabled=not (personal and entitled)
        )
    if fetch:
        _fetch(settings, symbol, start, end, personal, entitled)
    if ERROR_KEY in st.session_state:
        st.error(st.session_state[ERROR_KEY])
    if st.button("Clear ephemeral result", key="edge_provider_clear"):
        st.session_state.pop(RESULT_KEY, None)
        st.session_state.pop(ERROR_KEY, None)
    if RESULT_KEY in st.session_state:
        try:
            _results(st.session_state[RESULT_KEY])
        except Exception:
            st.session_state.pop(RESULT_KEY, None)
            st.error("Result presentation unavailable; ephemeral evidence cleared.")
    else:
        st.info(
            "No validated price snapshot. Nothing is fetched, charted or persisted."
        )
