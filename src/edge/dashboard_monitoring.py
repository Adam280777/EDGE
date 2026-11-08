"""Session-opt-in timer checks saved manual evidence, never remote services."""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records or SQL.
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    readout,
    timestamp_input,
)
from edge.decision_contracts import ManualProvenance, PriceQuote
from edge.monitoring import MonitoringRule
from edge.monitoring_store import MonitoringStore


def render(data_root: Path) -> None:
    boundary("Monitoring / local manual alerts")
    st.caption(
        "Checks run only in this open Monitoring workspace. No detached process, "
        "market-hours claim, provider retrieval, order, or stop/target changes. "
        "Closing EDGE or leaving this workspace stops scheduled checks; no catch-up."
    )
    store = MonitoringStore(data_root)
    task = st.radio(
        "Monitoring task", ["Alerts", "New rule", "Manual quote"],
        horizontal=True, key="edge_monitor_task",
    )
    if task != "Alerts":
        st.session_state[f"edge_monitor_enabled_{data_root.resolve()}"] = False
        st.session_state[f"edge_monitor_active_{data_root.resolve()}"] = False
    if task == "New rule":
        with st.form("edge_monitor_rule", enter_to_submit=False):
            symbol = st.text_input("Ticker / user-declared", max_chars=32)
            market = st.selectbox("Declared market", ["US", "UK"])
            unit = st.selectbox("Threshold unit / per share", ["USD", "GBP", "GBp"])
            stop = st.text_input("Stop / optional exact decimal")
            target = st.text_input("Target / optional exact decimal")
            thesis = st.selectbox(
                "Thesis status / user-declared", ["unchanged", "review", "invalidated"]
            )
            age = st.number_input(
                "Maximum manual quote age (seconds)", min_value=1,
                max_value=2592000, value=86400,
            )
            reference = st.text_input("Rule source / user declaration", max_chars=200)
            submitted = st.form_submit_button("Save immutable monitoring rule", type="primary")
        if submitted:
            try:
                rule = MonitoringRule(
                    uuid4().hex, symbol.strip().upper(), market, unit,
                    decimal_input(stop) if stop.strip() else None,
                    decimal_input(target) if target.strip() else None,
                    thesis, age, ManualProvenance(reference, datetime.now(UTC)),
                )
                store.append_rule(rule)
                st.success("Rule saved locally. No check or trade performed.")
            except Exception:
                failure("Rule save")
        return
    if task == "Manual quote":
        st.caption("Manual observations are append-only, not a live feed or validated market data.")
        with st.form("edge_monitor_quote", enter_to_submit=False):
            symbol = st.text_input("Ticker / user-declared", max_chars=32)
            market = st.selectbox("Declared market", ["US", "UK"])
            amount = st.text_input("Manual price / exact decimal")
            unit = st.selectbox("Price unit / per share", ["USD", "GBP", "GBp"])
            observed = st.text_input("Observed at / ISO timestamp with UTC offset")
            reference = st.text_input("Observation source / user declaration", max_chars=200)
            submitted = st.form_submit_button("Save manual observation", type="primary")
        if submitted:
            try:
                quote = PriceQuote(
                    decimal_input(amount), unit, timestamp_input(observed),
                    ManualProvenance(reference, datetime.now(UTC)),
                )
                store.append_quote(symbol.strip().upper(), market, quote)
                st.success("Manual observation saved. No automatic threshold check performed.")
            except Exception:
                failure("Observation save")
        return
    enabled_key = f"edge_monitor_enabled_{data_root.resolve()}"
    active_key = f"edge_monitor_active_{data_root.resolve()}"
    if not st.session_state.get(active_key, False):
        st.session_state[enabled_key] = False

    def set_schedule():
        st.session_state[active_key] = bool(st.session_state.get(enabled_key, False))

    st.checkbox(
        "Enable scheduled local checks for this open workspace / session only",
        value=False, key=enabled_key, on_change=set_schedule,
    )
    enabled = bool(st.session_state.get(active_key, False))
    interval = st.selectbox(
        "Check interval (seconds)", [30, 60, 300], index=1, key="edge_monitor_interval",
    )
    st.caption(
        f"Schedule: {'enabled' if enabled else 'off'} · "
        "Stale/missing/unit-mismatched observations produce reasons, never price alerts."
    )

    @st.fragment(run_every=interval if enabled else None)
    def panel():
        try:
            manual_check = st.button("Check saved manual evidence now", key="edge_monitor_check")
            if enabled or manual_check:
                findings = store.check(as_of=datetime.now(UTC))
                st.caption(f"Local check at {datetime.now(UTC).isoformat()} · {len(findings)} findings")
            rules = store.rules()
            if not rules:
                st.info("No saved rules. Choose New rule; schedules cannot invent rules or prices.")
            with st.expander("Saved rules / immutable user declarations"):
                for rule in rules:
                    readout(rule)
            alerts = store.alerts()
            if not alerts:
                st.info("No recorded alerts. This does not establish that a position is safe.")
            for alert in alerts:
                readout(alert)
                if alert.acknowledged_at is None and st.button(
                    "Acknowledge alert", key=f"edge_monitor_ack_{alert.alert_id}"
                ):
                    store.acknowledge(alert.alert_id, as_of=datetime.now(UTC))
                    st.rerun(scope="fragment")
        except Exception:
            failure("Monitoring read or check")

    panel()
