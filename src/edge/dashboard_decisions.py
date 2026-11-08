"""Explicit manual research revisions, confirmed policy and deterministic plans.

Presentation only: every figure shown is a user-declared input or a value
returned by the existing calculation functions (``calculate_position_size``,
``evaluate_valuation``). Charts visualise those values; nothing new is derived.
"""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records or SQL.
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import streamlit as st

from edge import ui_charts as charts
from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    timestamp_input,
)
from edge.decision_contracts import (
    DecisionStatus,
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
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    fmt_money,
    fmt_number,
    fmt_pct,
    fmt_time,
    kv_grid,
    panel,
    section_rule,
    status_board,
    status_light,
    tiles,
    tone_for,
)

RISK_ID = "manual-risk"
PAGE_SIZE = 20
SCAN_BATCH = 100
SCAN_LIMIT = 50  # batches; bounds the read-only horizon scan at 5,000 records.


def _latest_policy(store):
    records = store.list_records("risk", limit=100)
    own = [record for record in records if record.record_id == RISK_ID]
    return max(own, key=lambda item: item.revision) if own else None


# --------------------------------------------------------------------------- display helpers

def _lines(items) -> str:
    return "\n".join(f"• {item}" for item in items) if items else "None recorded"


def _readiness(readiness, *, label: str) -> None:
    state = {DecisionStatus.COMPLETE: "ok", DecisionStatus.INCOMPLETE: "warn"}.get(
        readiness.status, "bad")
    lights = [status_light(label, state, str(readiness.status).upper())]
    lights += [status_light("Reason", "warn" if state != "bad" else "bad", reason)
               for reason in readiness.reasons]
    status_board(lights)


def _policy_tiles(policy, *, revision=None) -> None:
    tiles([
        Tile("Sizing", "ENABLED" if policy.enabled else "DISABLED",
             "Confirmed policy" if policy.enabled else "No sizing permitted",
             "green" if policy.enabled else "red"),
        Tile("Max equity risk", fmt_pct(policy.max_risk_fraction, places=2)
             if policy.max_risk_fraction is not None else UNAVAILABLE,
             "Fraction of declared equity", "amber"),
        Tile("Policy revision", policy.revision_id or (str(revision) if revision else UNAVAILABLE),
             "Immutable chain", "cyan"),
        Tile("Confirmed at", fmt_time(policy.confirmed_at), "UTC", "ink"),
    ], columns=4)
    kv_grid([
        ("Long only", "Yes" if policy.long_only else "No"),
        ("Whole shares", "Yes" if policy.whole_shares else "No"),
        ("Max risk fraction (exact)", policy.max_risk_fraction
         if policy.max_risk_fraction is not None else UNAVAILABLE),
        ("Revision ID", policy.revision_id or UNAVAILABLE),
    ])


def _ladder(inputs, zone, *, key: str) -> None:
    unit = inputs.entry.unit
    levels = [{"label": "STOP", "price": inputs.stop.amount, "kind": "stop"},
              {"label": "ENTRY", "price": inputs.entry.amount, "kind": "entry"}]
    levels += [{"label": f"T{index}", "price": target.amount, "kind": "target"}
               for index, target in enumerate(inputs.targets, 1)]
    if zone:
        levels += [{"label": "ZONE LOW", "price": zone[0], "kind": "info"},
                   {"label": "ZONE HIGH", "price": zone[1], "kind": "info"}]
    drawn = charts.show(
        charts.price_ladder(levels, zone=zone, risk=(inputs.stop.amount, inputs.entry.amount),
                            title=f"Plan price ladder · {unit} per share", unit=unit),
        alt_text="Price ladder: stop, entry zone and targets with risk and reward zones",
        key=f"{key}_ladder",
    )
    if not drawn:
        empty("No plan levels to draw.", hint="Enter entry, stop and target prices.")
    st.caption("Red band: entry→stop risk zone. Amber band: entry zone. Green band: entry→top "
               "target reward zone. Manual prices; no live quote is shown.")


def _sizing(result, inputs, *, key: str) -> None:
    """Visualise a returned PositionSizeResult; no figure is recomputed here."""
    _readiness(result.readiness, label="Sizing estimate")
    cash_currency = result.estimated_cash_required.currency
    rr = " · ".join(f"T{i} {fmt_number(value)}R"
                    for i, value in enumerate(result.target_reward_risk, 1))
    tiles([
        Tile("Shares", str(result.shares) if result.shares else "0 · blocked",
             "Whole shares", "amber" if result.shares else "red"),
        Tile("Risk budget", fmt_money(result.risk_budget_gbp.amount, "GBP"), "GBP", "cyan"),
        Tile("Est. loss at stop", fmt_money(result.estimated_loss_gbp.amount, "GBP"),
             "GBP incl. fees", "red"),
        Tile("Cash required", fmt_money(result.estimated_cash_required.amount, cash_currency),
             cash_currency, "ink"),
        Tile("Added exposure", fmt_money(result.added_exposure_gbp.amount, "GBP"), "GBP", "ink"),
        Tile("Target R:R", rr or UNAVAILABLE, "Net of costs", "green" if rr else "muted"),
    ], columns=6)
    left, right = st.columns(2)
    with left:
        if not charts.show(charts.bullet(
                result.estimated_loss_gbp.amount, result.risk_budget_gbp.amount,
                label="Estimated loss", value_text=fmt_money(result.estimated_loss_gbp.amount, "GBP"),
                limit_text=fmt_money(result.risk_budget_gbp.amount, "GBP"),
                title="Estimated loss vs risk budget · GBP"),
                alt_text="Bullet chart of estimated loss against the risk budget",
                key=f"{key}_loss"):
            empty("Loss vs budget unavailable.", hint="Risk budget is zero or sizing was blocked.")
    with right:
        limit = inputs.exposure_limit_gbp.amount
        for value, name in ((inputs.current_exposure_gbp.amount, "Current exposure"),
                            (result.added_exposure_gbp.amount, "Added exposure")):
            charts.show(charts.bullet(
                value, limit, label=name, value_text=fmt_money(value, "GBP"),
                limit_text=fmt_money(limit, "GBP"), title=f"{name} vs exposure limit · GBP"),
                alt_text=f"Bullet chart of {name.lower()} against the declared exposure limit",
                key=f"{key}_{name.split()[0].lower()}")
    binding = set(result.binding_limits)
    status_board([
        status_light(f"{name.title()} ceiling",
                     "off" if not result.shares else "warn" if name in binding else "ok",
                     "Sizing blocked" if not result.shares
                     else "BINDING" if name in binding else "Not binding")
        for name in ("cash", "risk", "exposure")
    ])
    with st.expander("Sizing assumptions"):
        kv_grid([("Policy revision", result.policy_revision_id or UNAVAILABLE)]
                + [(f"A{i}", text) for i, text in enumerate(result.assumptions, 1)], columns=1)


def _catalysts(catalysts, *, key: str) -> None:
    rows = [{"lane": "Catalyst", "when": item.event_date.isoformat(), "label": item.description}
            for item in catalysts]
    if not charts.show(charts.timeline(rows, title="Dated catalysts"),
                       alt_text="Timeline of user-declared dated catalysts", key=f"{key}_cat"):
        empty("No dated catalysts recorded.")
        return
    kv_grid([(item.event_date.isoformat(), item.description) for item in catalysts], columns=1)


def _plan_card(record, *, key: str) -> None:
    identity = record.identity
    panel("PLN", f"{identity.symbol} · {identity.company_name}",
          meta=f"{identity.exchange} · {record.horizon} · rev {record.revision}",
          badges=("manual",))
    _ladder(record.inputs, (record.entry_zone[0].amount, record.entry_zone[1].amount), key=key)
    section_rule("Thesis")
    kv_grid([("Thesis", record.thesis), ("Entry trigger", record.entry_trigger),
             ("Stop rationale", record.stop_reason), ("Target rationale", record.targets_reason),
             ("Holding period", record.expected_holding_period)], columns=1)
    bull, bear = st.columns(2)
    with bull:
        panel("BUL", "Bull case")
        kv_grid([("Bull", record.bull_case)], columns=1)
    with bear:
        panel("BER", "Bear case")
        kv_grid([("Bear", record.bear_case), ("Invalidation", record.invalidation)], columns=1)
    panel("AVD", "Reasons not to take this trade")
    kv_grid([("Avoid conditions", _lines(record.avoid_conditions)),
             ("Uncertainty", _lines(record.uncertainty))], columns=1)
    _catalysts(record.catalysts, key=key)


def _research_card(record, *, key: str) -> None:
    from edge.research_templates import assess_research_completeness

    identity = record.identity
    panel("RES", f"{identity.symbol} · {identity.company_name}",
          meta=f"{identity.exchange} · {identity.reporting_currency} · {record.horizon}",
          badges=("manual",))
    _readiness(record.readiness, label="Decision readiness")
    st.caption("Manual checklist / presence and source dates only; not a quality score.")
    _completeness(assess_research_completeness(record))
    for title, items in (("Claimed facts", record.facts), ("Assumptions", record.assumptions),
                         ("Opinions", record.opinions)):
        if items:
            section_rule(title)
            kv_grid([(f"{item.reporting_period} · {item.unit}",
                      (f"{item.text}\n[{item.provenance.reference} · "
                       f"{fmt_time(item.provenance.declared_at)}]")) for item in items], columns=1)
    bull, bear = st.columns(2)
    with bull:
        panel("BUL", "Bull case")
        kv_grid([("Bull", record.bull_case),
                 ("Mispricing", record.mispricing_hypothesis)], columns=1)
    with bear:
        panel("BER", "Bear case")
        kv_grid([("Bear", record.bear_case), ("Invalidation", record.invalidation),
                 ("Risks", _lines(record.risks))], columns=1)
    kv_grid([("Conclusion", record.conclusion),
             ("Missing information", _lines(record.missing_information))], columns=1)
    _catalysts(record.catalysts, key=key)
    for index, scenario in enumerate(record.valuation_scenarios):
        section_rule(f"Attached scenario · {scenario.name}")
        _valuation_view(scenario, evaluate_valuation(scenario), key=f"{key}_v{index}")


def _completeness(result) -> None:
    lights = [status_light("Checklist", "ok" if result.complete else "warn",
                           "All prompts present" if result.complete else "Incomplete")]
    lights += [status_light("Missing", "warn", name) for name in result.missing_fields]
    lights += [status_light("Source", "bad", issue) for issue in result.source_issues]
    status_board(lights)
    for limitation in result.limitations:
        st.caption(limitation)


def _valuation_view(scenario, result, *, key: str) -> None:
    """Show returned DCF values; the waterfall uses only returned EV/equity and input cash/debt."""
    cur = result.enterprise_value.currency
    tiles([
        Tile("Enterprise value", fmt_money(result.enterprise_value.amount, cur), cur, "amber"),
        Tile("Equity value", fmt_money(result.equity_value.amount, cur), cur,
             tone_for(result.equity_value.amount)),
        Tile("Value / share", fmt_money(result.value_per_share.amount, cur),
             f"{scenario.diluted_shares:,} diluted shares", tone_for(result.value_per_share.amount)),
        Tile("Terminal share of EV", fmt_pct(result.terminal_value_fraction),
             "Sensitive to terminal inputs", "red" if result.terminal_value_fraction > 0.75 else "ink"),
    ], columns=4)
    left, right = st.columns(2)
    with left:
        charts.show(charts.waterfall([
            {"label": "Enterprise value", "amount": result.enterprise_value.amount, "total": True,
             "shown": fmt_money(result.enterprise_value.amount, cur, places=0)},
            {"label": "+ Cash", "amount": scenario.cash.amount,
             "shown": fmt_money(scenario.cash.amount, cur, places=0)},
            {"label": "− Debt", "amount": -scenario.debt.amount,
             "shown": fmt_money(-scenario.debt.amount, cur, places=0)},
            {"label": "Equity value", "amount": result.equity_value.amount, "total": True,
             "shown": fmt_money(result.equity_value.amount, cur, places=0)},
        ], title=f"Enterprise → equity bridge · {cur}", y_title=cur),
            alt_text="Waterfall from enterprise value through cash and debt to equity value",
            key=f"{key}_bridge")
    with right:
        rows = [{"year": year, "flow": flow.amount}
                for year, flow in enumerate(result.projected_cash_flows, 1)]
        charts.show(charts.lines(rows, x="year", y="flow", y_title=cur,
                                 title=f"Projected cash flow by year · {cur}"),
                    alt_text="Line chart of projected cash flows per explicit year",
                    key=f"{key}_flows")
    with st.expander("Scenario inputs"):
        kv_grid([
            ("Name", scenario.name), ("Source", scenario.provenance.reference),
            ("Base revenue", fmt_money(scenario.base_revenue.amount, cur)),
            ("Growth", fmt_pct(scenario.growth_fraction, places=2)),
            ("Operating margin", fmt_pct(scenario.operating_margin_fraction, places=2)),
            ("Cash conversion", fmt_pct(scenario.cash_conversion_fraction, places=2)),
            ("Discount rate", fmt_pct(scenario.discount_rate_fraction, places=2)),
            ("Terminal growth", fmt_pct(scenario.terminal_growth_fraction, places=2)),
            ("Years", scenario.years), ("Diluted shares", f"{scenario.diluted_shares:,}"),
            ("Cash", fmt_money(scenario.cash.amount, cur)),
            ("Debt", fmt_money(scenario.debt.amount, cur)),
        ])
    for limitation in result.limitations:
        st.caption(limitation)


# --------------------------------------------------------------------------- forms

def _identity():
    left, mid, right, cur = st.columns([1, 2, 1, 1])
    symbol = left.text_input("Ticker / identity unverified", max_chars=32)
    company = mid.text_input("Company name / user-declared", max_chars=200)
    exchange = right.text_input("Exchange / user-declared", max_chars=200)
    currency = cur.selectbox("Reporting / cash currency", ["GBP", "USD"])
    return symbol, company, exchange, currency


def _revision_fields():
    left, right = st.columns([3, 1])
    record_id = left.text_input("Existing record ID / blank creates a new chain", max_chars=100)
    revision = right.number_input("Revision / sequential for an existing chain", 1, 2147483647, 1)
    return record_id, revision


def _risk(store):
    current = _latest_policy(store)
    with st.container(key="edge_box_dec_risk_current"):
        panel("RSK", "Long-only whole-share policy / GBP risk reporting",
              meta=f"revision {current.revision}" if current else "not configured",
              badges=("manual",))
        if current:
            _policy_tiles(current.policy, revision=current.revision)
        else:
            st.info("Sizing disabled. No risk fraction has been configured or confirmed.")
        st.caption(
            "No default risk percentage. A fraction of 0.01 means 1%. Saving a disabled "
            "configuration revokes sizing; confirmation creates a new immutable revision. "
            "Draft changes never silently alter the saved policy."
        )
    with st.container(key="edge_box_dec_risk_form"):
        panel("SET", "Change policy", meta="appends a new revision")
        with st.form("edge_decision_risk", enter_to_submit=False):
            fraction = st.text_input("Maximum equity risk / fraction, not percent")
            disabled = st.form_submit_button("Save disabled risk configuration")
            confirm = st.form_submit_button("Confirm these exact limits and enable sizing", type="primary")
        st.caption("Blank fraction + Save disabled revokes sizing without recording a limit.")
    if disabled or confirm:
        try:
            now = datetime.now(UTC)
            revision = current.revision + 1 if current else 1
            # A disabled revision may legitimately carry no limit (RiskPolicy allows None),
            # so a blank field must not block revoking. Confirmation still requires a value.
            limit = decimal_input(fraction) if confirm or fraction.strip() else None
            policy = RiskPolicy(
                enabled=confirm, max_risk_fraction=limit,
                confirmed_at=now if confirm else None,
                revision_id=f"{RISK_ID}:{revision}" if confirm else None,
            )
            store.append(RiskPolicyRevision(RISK_ID, revision, now, policy))
            st.success("Confirmed risk revision saved." if confirm else "Disabled configuration saved; sizing remains off.")
        except Exception:
            failure("Risk policy save")
    _risk_history(store)


def _risk_history(store) -> None:
    records = sorted((r for r in store.list_records("risk", limit=100) if r.record_id == RISK_ID),
                     key=lambda r: r.revision)
    with st.container(key="edge_box_dec_risk_history"):
        panel("HST", "Policy revision history", meta=f"{len(records)} revision(s)")
        if not records:
            empty("No risk revisions recorded.", hint="Confirm a limit above to enable sizing.")
            return
        points = [{"when": r.created_at.isoformat(), "fraction": r.policy.max_risk_fraction}
                  for r in records if r.policy.max_risk_fraction is not None]
        charts.show(charts.lines(points, x="when", y="fraction", x_type="T",
                                 y_title="Fraction", title="Recorded max risk fraction over time"),
                    alt_text="Line chart of recorded maximum risk fraction per revision",
                    height=200, key="edge_dec_risk_hist_chart")
        st.dataframe([{
            "Revision": r.revision, "Saved (UTC)": fmt_time(r.created_at),
            "Sizing": "ENABLED" if r.policy.enabled else "DISABLED",
            "Max risk fraction": str(r.policy.max_risk_fraction)
            if r.policy.max_risk_fraction is not None else UNAVAILABLE,
            "Confirmed at": fmt_time(r.policy.confirmed_at),
            "Revision ID": r.policy.revision_id or UNAVAILABLE,
        } for r in reversed(records)], hide_index=True, width="stretch")


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
        with st.container(key="edge_box_dec_res_identity"):
            panel("IDN", "Identity & revision", badges=("manual",))
            record_id, revision = _revision_fields()
            identity = _identity()
        with st.container(key="edge_box_dec_res_evidence"):
            panel("EVD", "Evidence provenance")
            left, right = st.columns(2)
            source = left.text_input("Evidence source / manual reference", max_chars=200)
            observed = right.text_input("Evidence declared at / ISO timestamp with UTC offset")
            period = left.text_input("Reporting period / explicit label", max_chars=200)
            unit = right.text_input("Evidence unit / explicit label", max_chars=200)
            facts = st.text_area("Claimed reported facts", max_chars=10000)
            left, right = st.columns(2)
            assumptions = left.text_area("User assumptions", max_chars=10000)
            opinions = right.text_area("User opinions", max_chars=10000)
        with st.container(key="edge_box_dec_res_thesis"):
            panel("THS", "Thesis — bull vs bear")
            bull, bear = st.columns(2)
            narratives = {
                "bull_case": bull.text_area("Bull case", max_chars=10000),
                "bear_case": bear.text_area("Bear case", max_chars=10000),
                "mispricing_hypothesis": bull.text_area("Mispricing hypothesis", max_chars=10000),
                "invalidation": bear.text_area("Thesis invalidation", max_chars=10000),
                "conclusion": st.text_area("Decision conclusion", max_chars=10000),
            }
            left, right = st.columns(2)
            risks = left.text_area("Risks / one per line", max_chars=10000)
            missing = right.text_area("Missing information / one per line", max_chars=10000)
            left, right = st.columns([3, 1])
            catalyst = left.text_input("Dated catalyst / optional", max_chars=200)
            catalyst_date = right.text_input("Catalyst date / YYYY-MM-DD, if supplied")
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
            _readiness(record.readiness, label="Decision readiness")
            st.caption("Manual checklist / presence and source dates only; not a quality score.")
            _completeness(assess_research_completeness(record))
        except Exception:
            failure("Research save")


def _valuation():
    st.caption(
        "Hypothetical enterprise DCF from explicit manual assumptions. Rates are "
        "fractions. No forecast, fetched financial statement or investment recommendation."
    )
    with st.form("edge_decision_valuation", enter_to_submit=False):
        with st.container(key="edge_box_dec_dcf_scenario"):
            panel("DCF", "Scenario", badges=("manual",))
            left, mid, right = st.columns([2, 2, 1])
            name = left.text_input("Scenario name / user assumption", max_chars=200)
            source = mid.text_input("Scenario source / user declaration", max_chars=200)
            currency = right.selectbox("Scenario cash currency", ["GBP", "USD"])
        values = {}
        with st.container(key="edge_box_dec_dcf_operations"):
            panel("OPS", "Operating assumptions", meta="rates are fractions")
            left, right = st.columns(2)
            values["base_revenue"] = left.text_input("Base revenue / major cash currency")
            values["growth_fraction"] = right.text_input("Annual growth / fraction")
            values["operating_margin_fraction"] = left.text_input("Operating margin / fraction")
            values["cash_conversion_fraction"] = right.text_input("Cash conversion / fraction")
        with st.container(key="edge_box_dec_dcf_discount"):
            panel("DSC", "Discounting & capital structure")
            left, right = st.columns(2)
            values["discount_rate_fraction"] = left.text_input("Discount rate / fraction")
            values["terminal_growth_fraction"] = right.text_input("Terminal growth / fraction")
            values["cash"] = left.text_input("Company cash / major cash currency")
            values["debt"] = right.text_input("Company debt / major cash currency")
            # No pre-filled horizon or share count: a silent "1" would produce a
            # plausible-looking but fabricated per-share value if left untouched.
            years = left.number_input("Explicit projection years", 1, 30, None)
            shares = right.number_input("Diluted shares / whole shares", min_value=1, value=None)
        submitted = st.form_submit_button("Evaluate hypothetical scenario")
    if submitted:
        if years is None or shares is None:
            st.error("Enter explicit projection years and diluted shares; no defaults are assumed.")
            return
        try:
            data = {key: decimal_input(value) for key, value in values.items()}
            for key in ("base_revenue", "cash", "debt"):
                data[key] = FinanceMoney(data[key], currency)
            scenario = ValuationScenario(
                name, ManualProvenance(source, datetime.now(UTC)),
                years=int(years), diluted_shares=int(shares), **data,
            )
            result = evaluate_valuation(scenario)
            with st.container(key="edge_box_dec_dcf_result"):
                panel("VAL", f"Hypothetical valuation · {scenario.name}",
                      meta="session only", badges=("manual",))
                _valuation_view(scenario, result, key="edge_dec_dcf")
            st.session_state["edge_decision_scenario"] = scenario
            st.caption("Session scenario. New research can explicitly attach it to a saved revision.")
        except Exception:
            failure("Scenario evaluation")


def _plan(store, horizon):
    saved_policy = _latest_policy(store)
    policy = saved_policy.policy if saved_policy else RiskPolicy()
    with st.container(key="edge_box_dec_plan_policy"):
        panel("RSK", "Saved policy used for sizing", badges=("manual",))
        st.caption(
            "Sizing uses only the saved confirmed policy shown below, explicit user cash/"
            "equity/exposure and exact costs. Draft inputs are not a broker order."
        )
        _policy_tiles(policy)
        if not policy.enabled:
            st.warning("Sizing disabled until Risk policy is explicitly configured and confirmed.")
    with st.form("edge_decision_plan", enter_to_submit=False):
        numbers = {}
        with st.container(key="edge_box_dec_plan_identity"):
            panel("IDN", "Identity & price provenance", badges=("manual",))
            record_id, revision = _revision_fields()
            identity = _identity()
            left, mid, right = st.columns([2, 2, 1])
            source = left.text_input("Price / FX source declaration", max_chars=200)
            observed = mid.text_input("Price / FX observed at / ISO timestamp with UTC offset")
            unit = right.selectbox("Plan price unit / per share", ["GBP", "GBp", "USD"])
        with st.container(key="edge_box_dec_plan_levels"):
            panel("LVL", "Price levels", meta="per share, plan unit")
            cols = st.columns(5)
            for col, (key, label) in zip(cols, (
                    ("stop", "Stop price"), ("low", "Entry zone low"), ("entry", "Entry price"),
                    ("high", "Entry zone high"), ("target", "Target price"))):
                numbers[key] = col.text_input(label)
            other_targets = st.text_input("Additional targets / optional ascending decimals separated by commas")
        with st.container(key="edge_box_dec_plan_capital"):
            panel("CAP", "Capital & limits", meta="GBP unless stated")
            cols = st.columns(5)
            for col, (key, label) in zip(cols, (
                    ("equity_gbp", "Equity / GBP"), ("max_cash_risk_gbp", "Maximum cash risk / GBP"),
                    ("available_cash", "Available cash / quote cash currency"),
                    ("exposure_limit_gbp", "Exposure limit / GBP"),
                    ("current_exposure_gbp", "Current exposure / GBP"))):
                numbers[key] = col.text_input(label)
        with st.container(key="edge_box_dec_plan_costs"):
            panel("CST", "Costs & FX")
            left, right = st.columns(2)
            numbers["entry_fee"] = left.text_input("Entry fee / major cash currency; explicit zero if none")
            numbers["exit_fee"] = right.text_input("Exit fee / major cash currency; explicit zero if none")
            numbers["spread_per_share"] = left.text_input("Full spread / original per-share price unit")
            numbers["slippage_per_side"] = right.text_input("Slippage per side / original price unit")
            numbers["fx"] = left.text_input("GBP per USD / required for USD, blank for GBP or GBp")
            max_age = right.number_input(
                "Maximum age of manual plan prices / FX (seconds)", 1, 2592000, 86400,
            )
        with st.container(key="edge_box_dec_plan_thesis"):
            panel("THS", "Thesis, cases & reasons not to take")
            narratives = {"thesis": st.text_area("Thesis", max_chars=10000)}
            left, right = st.columns(2)
            narratives["entry_trigger"] = left.text_area("Entry trigger", max_chars=10000)
            narratives["expected_holding_period"] = right.text_area("Expected holding period", max_chars=10000)
            narratives["stop_reason"] = left.text_area("Stop rationale", max_chars=10000)
            narratives["targets_reason"] = right.text_area("Target rationale", max_chars=10000)
            narratives["bull_case"] = left.text_area("Bull case", max_chars=10000)
            narratives["bear_case"] = right.text_area("Bear case", max_chars=10000)
            narratives["invalidation"] = st.text_area("Invalidation", max_chars=10000)
            narratives["avoid_conditions"] = left.text_area("Avoid conditions / one per line", max_chars=10000)
            narratives["uncertainty"] = right.text_area("Uncertainty / one per line", max_chars=10000)
            left, right = st.columns([3, 1])
            catalyst = left.text_input("Plan catalyst / optional dated event", max_chars=200)
            catalyst_date = right.text_input("Plan catalyst date / YYYY-MM-DD, if supplied")
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
            with st.container(key="edge_box_dec_plan_result"):
                panel("SIZ", "Manual plan estimate", meta=f"prices as of {fmt_time(at)}",
                      badges=("manual",))
                _ladder(inputs, (data["low"], data["high"]), key="edge_dec_plan")
                _sizing(result, inputs, key="edge_dec_plan")
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


# --------------------------------------------------------------------------- saved revisions

def _horizon_page(store, kind, horizon, page):
    """Filter by horizon BEFORE paginating (read-only bounded scan).

    The store paginates all horizons together; filtering a fixed 20-row page
    afterwards left pages short or empty while matching records sat on later
    pages. Order is preserved exactly as the store returns it.
    """
    wanted, found, offset, truncated = page * PAGE_SIZE + 1, [], 0, True
    for _ in range(SCAN_LIMIT):
        rows = store.list_records(kind, limit=SCAN_BATCH, offset=offset)
        found += [r for r in rows if kind == "risk" or r.horizon == horizon]
        if len(rows) < SCAN_BATCH or len(found) >= wanted:
            truncated = False
            break
        offset += SCAN_BATCH
    start = (page - 1) * PAGE_SIZE
    return found[start:start + PAGE_SIZE], len(found) > start + PAGE_SIZE, truncated


def _saved(store, horizon) -> None:
    left, right = st.columns([3, 1])
    with left:
        kind = st.radio("Saved revision kind", ["research", "plan", "risk"], horizontal=True)
    with right:
        page = st.number_input("Revision page / 20 per page", min_value=1, value=1)
    visible, more, truncated = _horizon_page(store, kind, horizon, int(page))
    scope = "all horizons" if kind == "risk" else horizon
    with st.container(key="edge_box_dec_saved"):
        panel("REV", f"Saved {kind} revisions · {scope}", meta=f"page {int(page)}",
              badges=("manual", "local"))
        if not visible:
            st.info("No saved revisions on this page for the selected horizon and kind.")
            return
        stamped = sorted(visible, key=lambda r: r.created_at)
        series = [{"when": r.created_at.isoformat(), "count": i}
                  for i, r in enumerate(stamped, 1)]
        charts.show(charts.lines(series, x="when", y="count", x_type="T", y_title="Revisions",
                                 title="Cumulative revisions on this page by save time"),
                    alt_text="Line chart of cumulative saved revisions over save time",
                    height=180, key="edge_dec_saved_curve")
        st.caption("Newest first. "
                   + ("More revisions on the next page." if more else "End of matching revisions.")
                   + (" Scan limit reached; later records not shown." if truncated else ""))
    for record in visible:
        name = getattr(getattr(record, "identity", None), "symbol", "")
        with st.expander(f"Record {record.record_id} / revision {record.revision}"
                         + (f" · {name}" if name else "")):
            key = f"edge_dec_saved_{kind}_{record.record_id}_{record.revision}"
            st.caption(f"Saved {fmt_time(record.created_at)}")
            if kind == "research":
                _research_card(record, key=key)
            elif kind == "plan":
                _plan_card(record, key=key)
                st.caption("Historical plan estimate, not current permission to size or execute.")
                _sizing(record.sizing, record.inputs, key=key)
            else:
                _policy_tiles(record.policy, revision=record.revision)


def _load_summary(store):
    """Read the summary records once, before layout; None means the read failed."""
    try:
        return (_latest_policy(store), store.list_records("research", limit=1),
                store.list_records("plan", limit=1))
    except Exception:
        return None


def _summary_tiles(latest, research, plans, horizon) -> list[Tile]:
    policy = latest.policy if latest else None
    return [
        Tile("Risk sizing", "ENABLED" if policy and policy.enabled else "DISABLED",
             f"Policy rev {latest.revision}" if latest else "Not configured",
             "green" if policy and policy.enabled else "red"),
        Tile("Max equity risk", fmt_pct(policy.max_risk_fraction, places=2)
             if policy and policy.max_risk_fraction is not None else UNAVAILABLE,
             "Confirmed fraction", "amber"),
        Tile("Latest research", fmt_time(research[0].created_at) if research else UNAVAILABLE,
             research[0].identity.symbol if research else "None saved", "cyan"),
        Tile("Latest plan", fmt_time(plans[0].created_at) if plans else UNAVAILABLE,
             plans[0].identity.symbol if plans else "None saved", "cyan"),
        Tile("Horizon", horizon.upper(), "Swing and longterm kept separate", "ink"),
    ]


def _summary(loaded, horizon, ctx: PanelContext) -> None:
    if loaded is None:
        failure("Decision summary read")
        return
    items = _summary_tiles(*loaded, horizon)
    if ctx.view == "list":
        kv_grid([(t.label, f"{t.value} — {t.note}") for t in items],
                columns=2 if ctx.w >= 8 else 1)
    else:
        tiles(items, columns=5 if ctx.w >= 10 else max(1, min(5, ctx.w // 2)))


def _task(store, horizon, task) -> None:
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
            _saved(store, horizon)
    except Exception:
        failure("Decision read")


def panels(store, horizon, task) -> list[Panel]:
    """Decision workspace panels. The horizon/task selectors stay above the grid
    (they choose what every panel shows, so they cannot be hidden or reordered);
    summary records are read once before layout."""
    loaded = _load_summary(store)
    return [
        Panel("summary", "DEC", "Decision summary", lambda c: _summary(loaded, horizon, c),
              w=12, h=4, min_w=3, min_h=3, fit=True, meta="Latest saved local records",
              badges=("manual", "local"), views=(("tiles", "Tiles"), ("list", "List"))),
        # The task body carries its own sub-pane headers and a single st.form, so it
        # stays one unframed, grow-to-fit cell.
        Panel("task", "TSK", task, lambda c: _task(store, horizon, task), w=12, h=20,
              min_w=6, min_h=6, fit=True, framed=False),
    ]


def render(data_root: Path):
    boundary("Research / decisions")
    store = DecisionStore(data_root)
    left, right = st.columns([1, 2])
    with left:
        horizon = st.radio("Decision horizon", ["swing", "longterm"], horizontal=True,
                           key="edge_decision_horizon")
    with right:
        task = st.selectbox(
            "Decision task", ["Saved revisions", "New research", "Risk policy",
                              "Trade plan", "Valuation scenario"], key="edge_decision_task",
        )
    grid("decisions", panels(store, horizon, task))
