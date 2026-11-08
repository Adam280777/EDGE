"""Explicit manual research revisions, confirmed policy and deterministic plans."""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records or SQL.
from datetime import UTC, date, datetime
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
from edge.decision_contracts import (
    FinanceMoney,
    FxRate,
    ManualProvenance,
    PriceQuote,
    RiskPolicy,
)
from edge.decision_store import DecisionStore, RiskPolicyRevision
from edge.research_workflows import (
    CompanyIdentity,
    DatedCatalyst,
    ResearchItem,
    ResearchRevision,
    ValuationScenario,
    evaluate_valuation,
)
from edge.trade_planning import (
    SavedTradePlan,
    SizingInputs,
    TradingCosts,
    calculate_position_size,
)

RISK_ID = "manual-risk"


def _latest_policy(store):
    records = store.list_records("risk", limit=100)
    own = [record for record in records if record.record_id == RISK_ID]
    return max(own, key=lambda item: item.revision) if own else None


def _identity():
    symbol = st.text_input("Ticker / identity unverified", max_chars=32)
    company = st.text_input("Company name / user-declared", max_chars=200)
    exchange = st.text_input("Exchange / user-declared", max_chars=200)
    currency = st.selectbox("Reporting / cash currency", ["GBP", "USD"])
    return symbol, company, exchange, currency


def _revision_fields():
    record_id = st.text_input("Existing record ID / blank creates a new chain", max_chars=100)
    revision = st.number_input("Revision / sequential for an existing chain", 1, 2147483647, 1)
    return record_id, revision


def _risk(store):
    current = _latest_policy(store)
    st.subheader("Long-only whole-share policy / GBP risk reporting")
    if current:
        readout(current)
    else:
        st.info("Sizing disabled. No risk fraction has been configured or confirmed.")
    st.caption(
        "No default risk percentage. A fraction of 0.01 means 1%. Saving a disabled "
        "configuration revokes sizing; confirmation creates a new immutable revision. "
        "Draft changes never silently alter the saved policy."
    )
    with st.form("edge_decision_risk", enter_to_submit=False):
        fraction = st.text_input("Maximum equity risk / fraction, not percent")
        disabled = st.form_submit_button("Save disabled risk configuration")
        confirm = st.form_submit_button("Confirm these exact limits and enable sizing", type="primary")
    if disabled or confirm:
        try:
            now = datetime.now(UTC)
            revision = current.revision + 1 if current else 1
            policy = RiskPolicy(
                enabled=confirm, max_risk_fraction=decimal_input(fraction),
                confirmed_at=now if confirm else None,
                revision_id=f"{RISK_ID}:{revision}" if confirm else None,
            )
            store.append(RiskPolicyRevision(RISK_ID, revision, now, policy))
            st.success("Confirmed risk revision saved." if confirm else "Disabled configuration saved; sizing remains off.")
        except Exception:
            failure("Risk policy save")


def _research(store, horizon):
    from edge.research_templates import (
        assess_research_completeness,
        get_research_template,
    )

    template = get_research_template(horizon)
    st.caption(
        "Facts below are user-claimed facts, not independently verified. "
        "Keep assumptions and opinions separate. Missing sections remain incomplete."
    )
    with st.expander(f"Manual research prompts / {template.title}"):
        st.caption(template.concept_reference)
        for disclaimer in template.disclaimers:
            st.caption(disclaimer)
        for prompt in template.prompts:
            st.text(f"{prompt.label} / {'required for checklist' if prompt.required else 'optional'}")
            st.caption(prompt.question)
        st.caption("Prompts guide your own draft; no fields are generated or required to save.")
    with st.form("edge_decision_research", enter_to_submit=False):
        record_id, revision = _revision_fields()
        identity = _identity()
        source = st.text_input("Evidence source / manual reference", max_chars=200)
        observed = st.text_input("Evidence declared at / ISO timestamp with UTC offset")
        period = st.text_input("Reporting period / explicit label", max_chars=200)
        unit = st.text_input("Evidence unit / explicit label", max_chars=200)
        facts = st.text_area("Claimed reported facts", max_chars=10000)
        assumptions = st.text_area("User assumptions", max_chars=10000)
        opinions = st.text_area("User opinions", max_chars=10000)
        narratives = {
            field: st.text_area(label, max_chars=10000)
            for field, label in (
                ("bull_case", "Bull case"), ("bear_case", "Bear case"),
                ("mispricing_hypothesis", "Mispricing hypothesis"),
                ("invalidation", "Thesis invalidation"), ("conclusion", "Decision conclusion"),
            )
        }
        risks = st.text_area("Risks / one per line", max_chars=10000)
        missing = st.text_area("Missing information / one per line", max_chars=10000)
        catalyst = st.text_input("Dated catalyst / optional", max_chars=200)
        catalyst_date = st.text_input("Catalyst date / YYYY-MM-DD, if supplied")
        attach_scenario = st.checkbox(
            "Attach the last evaluated manual valuation scenario to this research revision",
            value=False, disabled="edge_decision_scenario" not in st.session_state,
        )
        submitted = st.form_submit_button("Save immutable research revision", type="primary")
    if submitted:
        try:
            provenance = ManualProvenance(source, timestamp_input(observed))
            def items(value):
                return (ResearchItem(value, provenance, period, unit),) if value.strip() else ()
            record = ResearchRevision(
                record_id or uuid4().hex, revision, datetime.now(UTC),
                CompanyIdentity(*identity), horizon,
                items(facts), items(assumptions), items(opinions),
                (st.session_state["edge_decision_scenario"],) if attach_scenario else (),
                (DatedCatalyst(catalyst, date.fromisoformat(catalyst_date), provenance),)
                if catalyst else (),
                risks=tuple(line for line in risks.splitlines() if line.strip()),
                missing_information=tuple(line for line in missing.splitlines() if line.strip()),
                **narratives,
            )
            store.append(record)
            st.success("Research revision saved. Workflow completeness is not financial verification.")
            readout(record.readiness)
            st.caption("Manual checklist / presence and source dates only; not a quality score.")
            readout(assess_research_completeness(record))
        except Exception:
            failure("Research save")


def _valuation():
    st.caption(
        "Hypothetical enterprise DCF from explicit manual assumptions. Rates are "
        "fractions. No forecast, fetched financial statement or investment recommendation."
    )
    with st.form("edge_decision_valuation", enter_to_submit=False):
        name = st.text_input("Scenario name / user assumption", max_chars=200)
        source = st.text_input("Scenario source / user declaration", max_chars=200)
        currency = st.selectbox("Scenario cash currency", ["GBP", "USD"])
        values = {
            key: st.text_input(label)
            for key, label in (
                ("base_revenue", "Base revenue / major cash currency"),
                ("growth_fraction", "Annual growth / fraction"),
                ("operating_margin_fraction", "Operating margin / fraction"),
                ("cash_conversion_fraction", "Cash conversion / fraction"),
                ("discount_rate_fraction", "Discount rate / fraction"),
                ("terminal_growth_fraction", "Terminal growth / fraction"),
                ("cash", "Company cash / major cash currency"),
                ("debt", "Company debt / major cash currency"),
            )
        }
        years = st.number_input("Explicit projection years", 1, 30, 1)
        shares = st.number_input("Diluted shares / whole shares", min_value=1, value=1)
        submitted = st.form_submit_button("Evaluate hypothetical scenario")
    if submitted:
        try:
            data = {key: decimal_input(value) for key, value in values.items()}
            for key in ("base_revenue", "cash", "debt"):
                data[key] = FinanceMoney(data[key], currency)
            scenario = ValuationScenario(
                name, ManualProvenance(source, datetime.now(UTC)),
                years=years, diluted_shares=shares, **data,
            )
            readout(scenario)
            readout(evaluate_valuation(scenario))
            st.session_state["edge_decision_scenario"] = scenario
            st.caption("Session scenario. New research can explicitly attach it to a saved revision.")
        except Exception:
            failure("Scenario evaluation")


def _plan(store, horizon):
    saved_policy = _latest_policy(store)
    policy = saved_policy.policy if saved_policy else RiskPolicy()
    st.caption(
        "Sizing uses only the saved confirmed policy shown below, explicit user cash/"
        "equity/exposure and exact costs. Draft inputs are not a broker order."
    )
    readout(policy)
    if not policy.enabled:
        st.warning("Sizing disabled until Risk policy is explicitly configured and confirmed.")
    with st.form("edge_decision_plan", enter_to_submit=False):
        record_id, revision = _revision_fields()
        identity = _identity()
        source = st.text_input("Price / FX source declaration", max_chars=200)
        observed = st.text_input("Price / FX observed at / ISO timestamp with UTC offset")
        unit = st.selectbox("Plan price unit / per share", ["GBP", "GBp", "USD"])
        numbers = {
            key: st.text_input(label)
            for key, label in (
                ("entry", "Entry price"), ("low", "Entry zone low"), ("high", "Entry zone high"),
                ("stop", "Stop price"), ("target", "Target price"),
                ("equity_gbp", "Equity / GBP"), ("max_cash_risk_gbp", "Maximum cash risk / GBP"),
                ("available_cash", "Available cash / quote cash currency"),
                ("exposure_limit_gbp", "Exposure limit / GBP"),
                ("current_exposure_gbp", "Current exposure / GBP"),
                ("entry_fee", "Entry fee / major cash currency; explicit zero if none"),
                ("exit_fee", "Exit fee / major cash currency; explicit zero if none"),
                ("spread_per_share", "Full spread / original per-share price unit"),
                ("slippage_per_side", "Slippage per side / original price unit"),
                ("fx", "GBP per USD / required for USD, blank for GBP or GBp"),
            )
        }
        other_targets = st.text_input("Additional targets / optional ascending decimals separated by commas")
        max_age = st.number_input(
            "Maximum age of manual plan prices / FX (seconds)", 1, 2592000, 86400,
        )
        narratives = {
            key: st.text_area(label, max_chars=10000)
            for key, label in (
                ("thesis", "Thesis"), ("entry_trigger", "Entry trigger"),
                ("stop_reason", "Stop rationale"), ("targets_reason", "Target rationale"),
                ("expected_holding_period", "Expected holding period"),
                ("bull_case", "Bull case"), ("bear_case", "Bear case"),
                ("invalidation", "Invalidation"), ("avoid_conditions", "Avoid conditions / one per line"),
                ("uncertainty", "Uncertainty / one per line"),
            )
        }
        catalyst = st.text_input("Plan catalyst / optional dated event", max_chars=200)
        catalyst_date = st.text_input("Plan catalyst date / YYYY-MM-DD, if supplied")
        preview = st.form_submit_button("Calculate manual plan estimate", disabled=not policy.enabled)
        save = st.form_submit_button("Save immutable plan revision", disabled=not policy.enabled, type="primary")
    if preview or save:
        try:
            data = {key: decimal_input(value) for key, value in numbers.items() if key != "fx"}
            provenance = ManualProvenance(source, datetime.now(UTC))
            at = timestamp_input(observed)
            from datetime import timedelta

            if datetime.now(UTC) - at > timedelta(seconds=max_age):
                st.warning("Manual plan price / FX is stale under your supplied age limit. No sizing or save performed.")
                return
            def quote(key):
                return PriceQuote(data[key], unit, at, provenance)
            cash_currency = "USD" if unit == "USD" else "GBP"
            inputs = SizingInputs(
                *(FinanceMoney(data[key], "GBP") for key in ("equity_gbp", "max_cash_risk_gbp")),
                FinanceMoney(data["available_cash"], cash_currency),
                *(FinanceMoney(data[key], "GBP") for key in ("exposure_limit_gbp", "current_exposure_gbp")),
                quote("entry"), quote("stop"),
                (quote("target"), *(
                    PriceQuote(decimal_input(value), unit, at, provenance)
                    for value in other_targets.split(",") if value.strip()
                )),
                TradingCosts(
                    FinanceMoney(data["entry_fee"], cash_currency),
                    FinanceMoney(data["exit_fee"], cash_currency),
                    data["spread_per_share"], data["slippage_per_side"],
                ),
                FxRate("USD", "GBP", decimal_input(numbers["fx"]), at, provenance)
                if unit == "USD" else None,
            )
            result = calculate_position_size(policy, inputs)
            readout(result)
            if save:
                for key in ("avoid_conditions", "uncertainty"):
                    narratives[key] = tuple(
                        line for line in narratives[key].splitlines() if line.strip()
                    )
                record = SavedTradePlan(
                    record_id or uuid4().hex, revision, datetime.now(UTC),
                    CompanyIdentity(*identity), horizon,
                    entry_zone=(quote("low"), quote("high")),
                    catalysts=(DatedCatalyst(catalyst, date.fromisoformat(catalyst_date), provenance),)
                    if catalyst else (),
                    inputs=inputs, policy=policy, **narratives,
                )
                store.append(record)
                st.success("Manual plan revision saved. No order, stop or target changed.")
        except Exception:
            failure("Plan calculation or save")


def render(data_root: Path):
    boundary("Research / decisions")
    store = DecisionStore(data_root)
    horizon = st.radio("Decision horizon", ["swing", "longterm"], horizontal=True,
                       key="edge_decision_horizon")
    task = st.selectbox(
        "Decision task", ["Saved revisions", "New research", "Risk policy",
                          "Trade plan", "Valuation scenario"], key="edge_decision_task",
    )
    try:
        if task == "Risk policy":
            _risk(store)
        elif task == "New research":
            _research(store, horizon)
        elif task == "Trade plan":
            _plan(store, horizon)
        elif task == "Valuation scenario":
            _valuation()
        else:
            kind = st.radio("Saved revision kind", ["research", "plan", "risk"], horizontal=True)
            page = st.number_input("Revision page / 20 per page", min_value=1, value=1)
            records = store.list_records(kind, limit=20, offset=(page - 1) * 20)
            visible = [record for record in records if kind == "risk" or record.horizon == horizon]
            if not visible:
                st.info("No saved revisions on this page for the selected horizon and kind.")
            for record in visible:
                with st.expander(f"Record {record.record_id} / revision {record.revision}"):
                    readout(record)
                    if kind == "research":
                        from edge.research_templates import assess_research_completeness

                        readout(record.readiness)
                        st.caption("Manual checklist / presence and source dates only; not a quality score.")
                        readout(assess_research_completeness(record))
                    elif kind == "plan":
                        st.caption("Historical plan estimate, not current permission to size or execute.")
                        readout(record.sizing)
    except Exception:
        failure("Decision read")
