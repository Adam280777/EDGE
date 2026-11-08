"""Transparent session-local screens over explicit user observations."""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records.
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    readout,
    timestamp_input,
)
from edge.decision_contracts import ManualProvenance
from edge.discovery import (
    CompanyObservations,
    FundamentalObservation,
    RankingRule,
    ScreenFilter,
    screen_companies,
)
from edge.research_workflows import CompanyIdentity


def render(data_root: Path) -> None:
    boundary("Discovery / transparent manual screens")
    st.caption(
        "Session-local user universe. Exact units, reporting periods, dates and "
        "classification must match. Rank is ordinal priority, not a score or expected return. "
        "Qlib and other external integrations remain unfinished; no provider fallback."
    )
    prefix = f"edge_discovery_{data_root.resolve()}"
    companies = st.session_state.get(prefix, ())
    task = st.radio(
        "Discovery task", ["Universe", "Screen", "Import CSV"], horizontal=True, key="edge_discovery_task",
    )
    if task == "Import CSV":
        from edge.dashboard_manual_csv import render_import

        preview = render_import(f"{prefix}_csv", ("Fundamentals",))
        if preview is not None:
            index = st.number_input("Imported fundamental row / explicit selection", 1, len(preview.observations), 1)
            selected = preview.observations[index - 1]
            readout(selected)
            if st.button("Apply selected manual metric to session universe", key="edge_discovery_apply_csv"):
                classes = {"reported": "claimed-reported", "estimated": "user-estimate", "derived": "user-derived"}
                if selected.value_status not in classes:
                    st.error("Classification unspecified. Preview retained; no screening metric inferred or applied.")
                    return
                period = (
                    f"{selected.period_start.isoformat()} through {selected.period_end.isoformat()}"
                    if selected.period_start is not None else "Unknown / not supplied"
                )
                identity = CompanyIdentity(
                    preview.instrument.symbol, preview.company_name, preview.exchange,
                    preview.instrument.currency,
                )
                observation = FundamentalObservation(
                    selected.fact, selected.value, selected.unit, period, selected.as_of,
                    selected.provenance, classes[selected.value_status],
                )
                other = [company for company in companies if company.identity != identity]
                current = next((company for company in companies if company.identity == identity), None)
                observations = tuple(
                    item for item in current.observations if item.metric != observation.metric
                ) if current else ()
                st.session_state[prefix] = (
                    *other, CompanyObservations(identity, (*observations, observation)),
                )
                st.success("Selected user-declared metric applied. No automatic screen or ranking performed.")
        return
    if task == "Universe":
        with st.form("edge_discovery_observation", enter_to_submit=False):
            symbol = st.text_input("Ticker / identity unverified", max_chars=32)
            name = st.text_input("Company name / user-declared", max_chars=200)
            exchange = st.text_input("Exchange / user-declared", max_chars=200)
            currency = st.selectbox("Reporting currency", ["GBP", "USD"])
            metric = st.text_input("Metric name / exact label", max_chars=200)
            value = st.text_input("Observed decimal / blank means unavailable")
            unit = st.text_input("Metric unit / exact label", max_chars=200)
            period = st.text_input("Reporting period / exact label", max_chars=200)
            observed = st.text_input("Observed at / ISO timestamp with UTC offset")
            classification = st.selectbox(
                "Observation classification", ["claimed-reported", "user-estimate", "user-derived"]
            )
            reference = st.text_input("Observation source / manual declaration", max_chars=200)
            submitted = st.form_submit_button("Add or replace this session metric", type="primary")
        if submitted:
            try:
                identity = CompanyIdentity(symbol.strip().upper(), name, exchange, currency)
                observation = FundamentalObservation(
                    metric, decimal_input(value) if value.strip() else None, unit, period,
                    timestamp_input(observed), ManualProvenance(reference, datetime.now(UTC)),
                    classification,
                )
                other = [company for company in companies if company.identity != identity]
                current = next((company for company in companies if company.identity == identity), None)
                observations = tuple(
                    item for item in current.observations if item.metric != metric
                ) if current else ()
                companies = (*other, CompanyObservations(identity, (*observations, observation)))
                st.session_state[prefix] = companies
                st.success("Manual session observation updated. Nothing written to disk or fetched.")
            except Exception:
                failure("Session observation")
        if not companies:
            st.info("No manual universe. Add an explicitly sourced observation before screening.")
        for company in companies:
            readout(company)
        if st.button("Clear session universe", key="edge_discovery_clear"):
            st.session_state.pop(prefix, None)
            st.rerun()
        return
    if not companies:
        st.info("No companies to screen. Choose Universe and enter manual observations.")
        return
    st.caption(
        "One explicit filter and optional ranking priority per screen. Missing, stale, "
        "incomparable or excluded classifications are named, never silently filled."
    )
    with st.form("edge_discovery_screen", enter_to_submit=False):
        metric = st.text_input("Filter metric / exact label")
        unit = st.text_input("Filter unit / exact label")
        period = st.text_input("Required reporting period / exact label")
        minimum = st.text_input("Minimum / optional decimal")
        maximum = st.text_input("Maximum / optional decimal")
        not_before = st.text_input("Oldest allowed observation / ISO timestamp with UTC offset")
        classes = st.multiselect(
            "Allowed user-declared classifications",
            ["claimed-reported", "user-estimate", "user-derived"],
            default=["claimed-reported"],
        )
        rank = st.selectbox("Rank by the same metric", ["No ranking", "higher", "lower"])
        submitted = st.form_submit_button("Run deterministic manual screen", type="primary")
    if submitted:
        try:
            start = timestamp_input(not_before)
            rule = ScreenFilter(
                metric, unit, decimal_input(minimum) if minimum.strip() else None,
                decimal_input(maximum) if maximum.strip() else None, period, start, tuple(classes),
            )
            ranking = () if rank == "No ranking" else (
                RankingRule(metric, unit, rank, period, start, tuple(classes)),
            )
            results = screen_companies(companies, (rule,), ranking, as_of=datetime.now(UTC))
            st.subheader("Submitted screen / inclusion and exclusion evidence")
            for result in results:
                readout(result)
        except Exception:
            failure("Manual screen")
