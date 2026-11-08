"""Transparent session-local screens over explicit user observations.

Presentation only: every figure drawn here is either a count of the user's own
session records or a value the user declared. Screening and ranking stay in
``edge.discovery.screen_companies``; rank is ordinal priority, never a score.
"""

# ruff: noqa: BLE001 -- fixed UI errors must not leak user records.
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from edge import ui_charts as charts
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
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    chip,
    empty,
    fmt_time,
    html_block,
    kv_grid,
    panel,
    provenance,
    tiles,
)

CLASSES = ["claimed-reported", "user-estimate", "user-derived"]
TASKS = ["Universe", "Screen", "Import CSV"]
ONCE_KEY = "edge_discovery_once"  # one-rerun handoff of form output; never persisted


def _company_label(identity: CompanyIdentity) -> str:
    return f"{identity.exchange}:{identity.symbol}"


def _value_text(value: object, unit: str) -> str:
    return UNAVAILABLE if value is None else f"{value} {unit}".strip()


def _status_strip(companies: tuple[CompanyObservations, ...]) -> None:
    observations = [o for c in companies for o in c.observations]
    stamps = [o.as_of for o in observations]
    blanks = sum(1 for o in observations if o.value is None)
    tiles([
        Tile("Companies", str(len(companies)), "Session universe · user-declared", "amber"),
        Tile("Observations", str(len(observations)), "One per company × metric", "ink"),
        Tile("Distinct metrics", str(len({o.metric for o in observations})), "Exact labels", "cyan"),
        Tile("Declared unavailable", str(blanks), "Blank values stay unavailable",
             "amber" if blanks else "muted"),
        Tile("Newest observation", fmt_time(max(stamps)) if stamps else UNAVAILABLE,
             "User-declared timestamp", "green" if stamps else "muted"),
        Tile("Oldest observation", fmt_time(min(stamps)) if stamps else UNAVAILABLE,
             "Check against screen freshness bound", "ink" if stamps else "muted"),
    ], columns=6)


def _universe_charts(companies: tuple[CompanyObservations, ...], view: str = "all",
                     single_height: int | None = None) -> None:
    """``view`` picks which of the same declared-observation charts to draw."""
    observations = [(c.identity, o) for c in companies for o in c.observations]
    if not observations:
        empty("No observations to chart.", hint="Declare a sourced metric in the form to the left.")
        return
    events = [{"lane": _company_label(identity), "when": o.as_of.isoformat(),
               "label": f"{o.metric} · {_value_text(o.value, o.unit)} · {o.classification}"}
              for identity, o in observations]
    if view in ("all", "timeline"):
        charts.show(charts.timeline(events, title="Observation dates by company (user-declared as-of)"),
                    alt_text="Timeline of declared observation timestamps per company",
                    height=single_height, key="edge_discovery_chart_timeline")
    if view in ("all", "accumulation"):
        charts.show(charts.step_area([{"when": e["when"], "series": e["lane"]} for e in events],
                                     title="Declared observations accumulated by as-of date",
                                     y_title="Observations"),
                    alt_text="Stepped cumulative count of declared observations per company over time",
                    height=single_height or 220, key="edge_discovery_chart_accumulation")
    if view in ("all", "coverage"):
        coverage = [{"company": _company_label(identity), "metric": o.metric,
                     "present": 0 if o.value is None else 1,
                     "shown": "blank" if o.value is None else "✓"}
                    for identity, o in observations]
        charts.show(charts.heat(coverage, x="metric", y="company", value="present", text="shown",
                                title="Coverage · declared value vs declared-unavailable"),
                    alt_text="Heat map of which metrics each company has a declared value for",
                    height=80 + 34 * len(companies), key="edge_discovery_chart_coverage")
        st.caption("Empty cells: metric never declared for that company. Nothing is filled or inferred.")


def _company_cards(companies: tuple[CompanyObservations, ...]) -> None:
    for index, company in enumerate(companies):
        identity = company.identity
        with st.container(key=f"edge_box_discovery_co_{index}"):
            panel(identity.symbol, identity.company_name,
                  meta=f"{identity.exchange} · {identity.reporting_currency}", badges=("manual",))
            rows = []
            for o in company.observations:
                rows.append((f"{o.metric} · {o.reporting_period}",
                             (f"{_value_text(o.value, o.unit)} · {o.classification} · "
                              f"as of {fmt_time(o.as_of)} · source {o.provenance.reference}")))
            kv_grid(rows or [("Observations", "None declared")], columns=1)
            with st.expander("Raw record fields"):
                readout(company)


def _funnel(results, metric: str, ranked: bool) -> None:
    stages = [
        {"stage": "Session universe", "count": len(results)},
        {"stage": f"Usable '{metric}' observation", "count": sum(1 for r in results if r.filter_trace)},
        {"stage": "Within user bounds", "count": sum(1 for r in results if r.included)},
    ]
    if ranked:
        stages.append({"stage": "Ranked candidates", "count": sum(1 for r in results if r.rank is not None)})
    if not charts.show(charts.funnel(stages, title="Screening funnel · engine counts"),
                       alt_text="Funnel from session universe to included and ranked candidates",
                       key="edge_discovery_chart_funnel"):
        empty("Nothing to draw: the universe is empty.")


def _exclusions(results) -> None:
    reasons = Counter(item.split(": ", 1)[-1] for r in results for item in r.exclusions)
    if not reasons:
        empty("No exclusions recorded by the engine for this screen.")
        return
    rows = [{"label": reason, "value": count, "shown": f"{count} compan{'y' if count == 1 else 'ies'}"}
            for reason, count in reasons.most_common()]
    charts.show(charts.donut(rows, detail="shown", title="Exclusion reasons (named by engine)",
                             center=str(sum(reasons.values()))),
                alt_text="Donut of exclusion reasons returned by the screening engine", height=250,
                key="edge_discovery_chart_exclusions")


def _ranked_values(results, companies, metric: str, unit: str, ranked: bool) -> None:
    """Recorded metric value for each included candidate, in engine order."""
    lookup = {c.identity: {o.metric: o for o in c.observations} for c in companies}
    rows = []
    for r in results:
        if not r.included:
            continue
        observation = lookup.get(r.identity, {}).get(metric)
        if observation is None or observation.value is None:
            continue
        prefix = f"#{r.rank} " if r.rank is not None else ""
        rows.append({"label": f"{prefix}{_company_label(r.identity)}", "value": observation.value,
                     "shown": f"{observation.value} {unit}"})
    title = (f"Recorded {metric} of ranked candidates · rank order is ordinal, not a score"
             if ranked else f"Recorded {metric} of included candidates (no ranking requested)")
    if not charts.show(charts.dots(rows, text="shown", x_title=unit, title=title, tone="green"),
                       alt_text="Dot plot of each included candidate's declared metric value",
                       key="edge_discovery_chart_ranked"):
        empty("No candidate passed the screen.",
              hint="Review the exclusion reasons and evidence cards below.")


def _result_card(index: int, result) -> None:
    identity = result.identity
    with st.container(key=f"edge_box_discovery_result_{index}"):
        status = chip("INCLUDED", "green") if result.included else chip("EXCLUDED", "red")
        rank = chip(f"RANK #{result.rank}", "amber") if result.rank is not None else ""
        html_block(f'<div>{status}{rank}{provenance("manual")}</div>')
        panel(identity.symbol, identity.company_name,
              meta=f"{identity.exchange} · {identity.reporting_currency}")
        st.text(f"included: {result.included}  rank: {result.rank if result.rank is not None else 'none'}")
        kv_grid([
            ("Exclusions", "; ".join(result.exclusions) or "None"),
            ("Filter evidence", " | ".join(result.filter_trace) or "No usable filter observation"),
            ("Ranking evidence", " | ".join(result.ranking_trace) or "Not ranked"),
        ], columns=1)
        with st.expander("Engine trace & limitations"):
            readout(result)


def _handoff(state: dict, **values: object) -> bool:
    """If a panel that shows form output already rendered above the form (user
    layout), hand the output to one immediate rerun so it appears this interaction."""
    if not state["seen"]:
        return False
    st.session_state[ONCE_KEY] = values
    st.rerun()
    return True


def _screen_summary(state: dict, ctx: PanelContext) -> None:
    state["seen"].add("results")
    screen = state.get("screen")
    if screen is None:
        empty("No screen run in this view.",
              hint="Fill the exact metric, unit, period, a bound and freshness, then run the screen.")
        return
    results, companies, metric, unit, ranked, as_of = screen
    try:
        st.caption(f"Run {fmt_time(as_of)} · session only")
        included = sum(1 for r in results if r.included)
        tiles([
            Tile("Screened", str(len(results)), "Session universe", "amber"),
            Tile("Included", str(included), "Passed every check", "green" if included else "muted"),
            Tile("Excluded", str(len(results) - included), "Reasons named below",
                 "red" if len(results) - included else "muted"),
            Tile("Ranked", str(sum(1 for r in results if r.rank is not None)) if ranked else "Not requested",
                 "Ordinal priority only", "cyan"),
        ], columns=4 if ctx.w >= 6 else 2)
        if ctx.w >= 7:
            left, right = st.columns([1.3, 1], gap="small")
            with left:
                _funnel(results, metric, ranked)
            with right:
                _exclusions(results)
        else:
            _funnel(results, metric, ranked)
            _exclusions(results)
        _ranked_values(results, companies, metric, unit, ranked)
        if results:
            st.caption("Engine limitations — " + " ".join(results[0].limitations))
    except Exception:
        failure("Manual screen")


def _screen_evidence(state: dict) -> None:
    state["seen"].add("evidence")
    screen = state.get("screen")
    if screen is None:
        empty("No per-company evidence yet.",
              hint="Run the screen to see each company's inclusion and exclusion evidence.")
        return
    try:
        for index, result in enumerate(screen[0]):
            _result_card(index, result)
    except Exception:
        failure("Manual screen")


def _upsert(companies, identity, observation, metric: str):
    other = [company for company in companies if company.identity != identity]
    current = next((company for company in companies if company.identity == identity), None)
    observations = tuple(
        item for item in current.observations if item.metric != metric
    ) if current else ()
    return (*other, CompanyObservations(identity, (*observations, observation)))


def _task_radio() -> str:
    return st.radio(
        "Discovery task", TASKS, horizontal=True, key="edge_discovery_task",
    )


def _import(prefix: str, state: dict) -> None:
    from edge.dashboard_manual_csv import render_import

    _task_radio()
    preview = render_import(f"{prefix}_csv", ("Fundamentals",))
    if preview is None:
        return
    with st.container(key="edge_box_discovery_csv_select"):
        panel("SEL", "Selected imported row", badges=("manual",))
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
            state["companies"] = _upsert(state["companies"], identity, observation, observation.metric)
            st.session_state[prefix] = state["companies"]
            st.success("Selected user-declared metric applied. No automatic screen or ranking performed.")


def _declare(prefix: str, state: dict) -> None:
    _task_radio()
    with st.form("edge_discovery_observation", enter_to_submit=False):
        a, b = st.columns([1, 2])
        symbol = a.text_input("Ticker / identity unverified", max_chars=32)
        name = b.text_input("Company name / user-declared", max_chars=200)
        a, b = st.columns([2, 1])
        exchange = a.text_input("Exchange / user-declared", max_chars=200)
        currency = b.selectbox("Reporting currency", ["GBP", "USD"])
        metric = st.text_input("Metric name / exact label", max_chars=200)
        a, b = st.columns(2)
        value = a.text_input("Observed decimal / blank means unavailable")
        unit = b.text_input("Metric unit / exact label", max_chars=200)
        period = st.text_input("Reporting period / exact label", max_chars=200)
        observed = st.text_input("Observed at / ISO timestamp with UTC offset")
        classification = st.selectbox("Observation classification", CLASSES)
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
            state["companies"] = _upsert(state["companies"], identity, observation, metric)
            st.session_state[prefix] = state["companies"]
            notice = "Manual session observation updated. Nothing written to disk or fetched."
            if not _handoff(state, notice=notice):
                st.success(notice)
        except Exception:
            failure("Session observation")
    elif state.get("notice"):
        st.success(state["notice"])
    if st.button("Clear session universe", key="edge_discovery_clear"):
        st.session_state.pop(prefix, None)
        st.rerun()


def _universe_map(state: dict, ctx: PanelContext) -> None:
    state["seen"].add("universe")
    companies = state["companies"]
    if not companies:
        st.info("No manual universe. Add an explicitly sourced observation before screening.")
        empty("Nothing declared yet.",
              hint="Enter ticker, metric, unit, period, timestamp and source, then add it.")
        return
    single = None if ctx.fit or ctx.view == "all" else ctx.chart_height()
    _universe_charts(companies, ctx.view, single)


def _declared_companies(state: dict) -> None:
    state["seen"].add("companies")
    if not state["companies"]:
        empty("No companies declared in this session.",
              hint="Declared observations appear here grouped by company.")
        return
    _company_cards(state["companies"])


def _screen_form(state: dict) -> None:
    _task_radio()
    companies = state["companies"]
    if not companies:
        st.info("No companies to screen. Choose Universe and enter manual observations.")
        empty("The screen needs a session universe.",
              hint="Switch the task to Universe or Import CSV and declare observations first.")
        return
    st.caption(
        "One explicit filter and optional ranking priority per screen. Missing, stale, "
        "incomparable or excluded classifications are named, never silently filled."
    )
    with st.form("edge_discovery_screen", enter_to_submit=False):
        a, b, c = st.columns(3)
        metric = a.text_input("Filter metric / exact label")
        unit = b.text_input("Filter unit / exact label")
        period = c.text_input("Required reporting period / exact label")
        a, b, c = st.columns(3)
        minimum = a.text_input("Minimum / optional decimal")
        maximum = b.text_input("Maximum / optional decimal")
        not_before = c.text_input("Oldest allowed observation / ISO timestamp with UTC offset")
        a, b = st.columns([2, 1])
        classes = a.multiselect(
            "Allowed user-declared classifications", CLASSES, default=["claimed-reported"],
        )
        rank = b.selectbox("Rank by the same metric", ["No ranking", "higher", "lower"])
        submitted = st.form_submit_button("Run deterministic manual screen", type="primary")
    if not submitted:
        return
    try:
        start = timestamp_input(not_before)
        rule = ScreenFilter(
            metric, unit, decimal_input(minimum) if minimum.strip() else None,
            decimal_input(maximum) if maximum.strip() else None, period, start, tuple(classes),
        )
        ranking = () if rank == "No ranking" else (
            RankingRule(metric, unit, rank, period, start, tuple(classes)),
        )
        as_of = datetime.now(UTC)
        results = screen_companies(companies, (rule,), ranking, as_of=as_of)
    except Exception:
        failure("Manual screen")
        return
    screen = (results, companies, metric, unit, bool(ranking), as_of)
    if not _handoff(state, screen=screen):
        state["screen"] = screen


def panels(prefix: str, companies: tuple[CompanyObservations, ...], task: str) -> list[Panel]:
    """Discovery panels for the current task. The task switcher and its form share
    one panel; read-only displays read the shared session state the form updates."""
    once = st.session_state.pop(ONCE_KEY, None)
    once = once if isinstance(once, dict) else {}
    state: dict = {"companies": companies, "screen": once.get("screen"), "notice": once.get("notice"),
                   "seen": set()}
    items = [
        Panel("status", "DSC", "Discovery universe", lambda c: _status_strip(companies), w=12, h=5,
              min_w=4, fit=True, meta="Session memory only · no provider data", badges=("manual",)),
    ]
    if task == "Import CSV":
        items.append(Panel("import", "CSV", "Import fundamentals CSV", lambda c: _import(prefix, state),
                           w=12, h=14, min_w=4, fit=True, meta="Preview first · explicit apply",
                           badges=("manual",)))
    elif task == "Universe":
        items += [
            Panel("declare", "ADD", "Declare an observation", lambda c: _declare(prefix, state),
                  w=5, h=20, min_w=4, fit=True, meta="Session only · nothing saved", badges=("manual",)),
            Panel("universe", "UNV", "Universe map", lambda c: _universe_map(state, c), w=7, h=20,
                  min_w=3, fit=True, meta="Dates and coverage as declared", badges=("manual",),
                  views=(("all", "All charts"), ("timeline", "Date timeline"),
                         ("accumulation", "Accumulation"), ("coverage", "Coverage map"))),
            Panel("companies", "COS", "Declared companies", lambda c: _declared_companies(state),
                  w=12, h=8, min_w=3, fit=True, meta="Session universe · user-declared",
                  badges=("manual",)),
        ]
    else:
        items.append(Panel("screen", "FLT", "Screen definition", lambda c: _screen_form(state),
                           w=12, h=10, min_w=4, fit=True,
                           meta="One filter · optional ranking on the same metric", badges=("manual",)))
        if companies:
            items += [
                Panel("results", "SCR", "Submitted screen / inclusion and exclusion evidence",
                      lambda c: _screen_summary(state, c), w=12, h=16, min_w=4, fit=True,
                      meta="Session only", badges=("manual",)),
                Panel("evidence", "EVD", "Per-company screen evidence", lambda c: _screen_evidence(state),
                      w=12, h=10, min_w=3, fit=True, meta="Engine trace per company"),
            ]
    return items


def render(data_root: Path) -> None:
    boundary("Discovery / transparent manual screens")
    st.caption(
        "Session-local user universe. Exact units, reporting periods, dates and "
        "classification must match. Rank is ordinal priority, not a score or expected return. "
        "Qlib and other external integrations remain unfinished; no provider fallback."
    )
    prefix = f"edge_discovery_{data_root.resolve()}"
    companies = st.session_state.get(prefix, ())
    task = st.session_state.get("edge_discovery_task", TASKS[0])
    grid("discovery", panels(prefix, companies, task if task in TASKS else TASKS[0]))
