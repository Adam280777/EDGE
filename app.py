"""Synthetic acceptance, explicit SEC metadata and local notes; no live trading."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from edge import simulation_service
from edge import ui_charts as charts
from edge.desktop_paths import app_data_root
from edge.domain import SimulationConfig
from edge.errors import EdgeError
from edge.fixtures import demo_dataset
from edge.run_store import RunStore
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import esc, html_block, provenance
from edge.ui_theme import BASE_CSS, TERMINAL_CSS

DEFAULT_OUTPUT_DIR = app_data_root(Path(__file__).resolve().parent / "data")
RECENT_RUN_KEY = "edge_recent_run"
READ_ERRORS = (EdgeError, OSError, sqlite3.Error, json.JSONDecodeError)
WORKSPACES = (
    "Command center",
    "Discovery", "Company watchlist", "Research / decisions", "SEC filing research",
    "Research journal", "Research lab",
    "Manual portfolio", "Monitoring",
    "Engine acceptance",
    "Provider settings", "Backup and recovery", "Application recovery",
)
WORKSPACE_GROUPS = {
    "Command center": "Command",
    "Discovery": "Research",
    "Company watchlist": "Research",
    "Research / decisions": "Research",
    "SEC filing research": "Research",
    "Research journal": "Research",
    "Research lab": "Research",
    "Manual portfolio": "Holdings",
    "Monitoring": "Holdings",
    "Engine acceptance": "Evidence",
    "Provider settings": "System",
    "Backup and recovery": "System",
    "Application recovery": "System",
}
# Terminal function codes are visual labels only, never keyboard shortcuts.
WORKSPACE_CODES = {
    "Command center": "HOME",
    "Discovery": "DSC",
    "Company watchlist": "WCH",
    "Research / decisions": "DEC",
    "SEC filing research": "SEC",
    "Research journal": "JRN",
    "Research lab": "LAB",
    "Manual portfolio": "PRT",
    "Monitoring": "MON",
    "Engine acceptance": "ENG",
    "Provider settings": "PRV",
    "Backup and recovery": "BAK",
    "Application recovery": "APP",
}
WORKSPACE_TITLES = {
    "Command center": "Command center",
    "Discovery": "Discovery",
    "Company watchlist": "Watchlist",
    "Research / decisions": "Decisions",
    "SEC filing research": "SEC filings",
    "Research journal": "Journal",
    "Research lab": "Research lab",
    "Manual portfolio": "Portfolio",
    "Monitoring": "Monitoring",
    "Engine acceptance": "Engine",
    "Provider settings": "Providers",
    "Backup and recovery": "Recovery",
    "Application recovery": "Bundle recovery",
}
WORKSPACE_LABELS = {
    "Command center": "HOME · Command center",
    "Discovery": "DSC · Discovery",
    "Company watchlist": "WCH · Watchlist",
    "Research / decisions": "DEC · Decisions",
    "SEC filing research": "SEC · SEC filings",
    "Research journal": "JRN · Journal",
    "Research lab": "LAB · Research lab",
    "Manual portfolio": "PRT · Portfolio",
    "Monitoring": "MON · Monitoring",
    "Engine acceptance": "ENG · Engine",
    "Provider settings": "PRV · Providers",
    "Backup and recovery": "BAK · Recovery",
    "Application recovery": "APP · Bundle recovery",
}
WORKSPACE_PURPOSE = {
    "Command center": "Portfolio, monitoring and research pipeline at a glance",
    "Discovery": "Screen and rank user-declared candidate snapshots",
    "Company watchlist": "Track companies and route to research",
    "Research / decisions": "Risk policy, research, valuation and trade plans",
    "SEC filing research": "Explicit SEC EDGAR filing metadata lookups",
    "Research journal": "Append-only swing and long-term research notes",
    "Research lab": "Synthetic Qlib / skfolio / Kronos experiments",
    "Manual portfolio": "Manual ledger, FIFO lots and GBP reporting",
    "Monitoring": "Stop, target and thesis rules with saved alerts",
    "Engine acceptance": "NautilusTrader synthetic acceptance evidence",
    "Provider settings": "Optional FMP key and display-only prices",
    "Backup and recovery": "Journal backup and verified recovery",
    "Application recovery": "Whole application bundle backup",
}


def _stop_monitoring(output_dir: Path) -> None:
    if st.session_state.get("edge_workspace") != "Research lab":
        from edge.dashboard_research_lab import cancel_on_leave

        cancel_on_leave()
    if st.session_state.get("edge_workspace") != "Monitoring":
        suffix = str(output_dir.resolve())
        st.session_state[f"edge_monitor_enabled_{suffix}"] = False
        st.session_state[f"edge_monitor_active_{suffix}"] = False


def _journal_root(default: Path) -> Path:
    """Session-opened recovery never changes the application storage default."""
    selected = st.session_state.get("edge_open_journal_root")
    return selected if isinstance(selected, Path) and selected.is_absolute() else default


def _apply_navigation_request(journal_root: Path) -> None:
    """Consume a local routing intent before destination widgets are created."""
    request = st.session_state.pop("edge_navigation_request", None)
    if not isinstance(request, dict):
        return
    target, symbol = request.get("target"), request.get("symbol")
    if (
        target not in ("SEC filing research", "Research journal")
        or not isinstance(symbol, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.^:/-]{0,31}", symbol) is None
    ):
        return
    st.session_state["edge_workspace"] = target
    symbol = symbol.upper()
    if target == "SEC filing research":
        st.session_state["edge_sec_ticker"] = symbol
    else:
        horizon = st.session_state.get("edge_journal_horizon", "Swing")
        horizon_value = "long-term" if horizon == "Long-term" else "swing"
        prefix = f"edge_journal_{journal_root.resolve()}_{horizon_value}"
        st.session_state[f"{prefix}_task"] = "New entry"
        st.session_state[f"{prefix}_symbol"] = symbol


def _render_journal(default_root: Path) -> None:
    """Require explicit session opening of a separately recovered journal."""
    from edge.dashboard_journal import render

    pending = st.session_state.get("edge_restored_journal_root")
    selected = _journal_root(default_root)
    if isinstance(pending, Path) and pending.is_absolute() and pending != selected:
        st.info("A recovered journal is available separately. The original journal is unchanged.")
        if st.button("Open restored journal", key="edge_open_recovered_journal"):
            st.session_state["edge_open_journal_root"] = pending
            st.rerun()
    if selected != default_root:
        st.warning("Recovered journal open for this session only. Original storage remains unchanged.")
        if st.button("Return to original journal", key="edge_return_original_journal"):
            st.session_state.pop("edge_open_journal_root", None)
            st.rerun()
    render(selected)


_TERMINAL_CSS = BASE_CSS + TERMINAL_CSS



def _check_record(record: dict) -> None:
    """Reject incomplete evidence rather than turn missing engine facts into zeros."""
    manifest = record.get("manifest")
    if not isinstance(manifest, dict) or not all(
        key in manifest
        for key in ("run_id", "scenario", "source", "retrieved_at", "started_at")
    ):
        raise EdgeError("Recorded manifest is incomplete or unreadable.")
    if record.get("status") not in ("running", "failed", "succeeded"):
        raise EdgeError("Recorded run has an unrecognized status.")
    result = record.get("result")
    if record["status"] == "succeeded":
        outcome = result.get("outcome") if isinstance(result, dict) else None
        if not isinstance(outcome, dict) or not all(
            key in outcome
            for key in (
                "ending_cash",
                "realized_pnl",
                "ending_quantity",
                "orders",
                "fills",
                "positions",
                "rejections",
                "assumptions",
            )
        ):
            raise EdgeError("Recorded engine outcome is incomplete or unreadable.")
        for key in ("orders", "fills", "positions", "rejections"):
            if not isinstance(outcome[key], (list, tuple)) or not all(
                isinstance(row, dict) for row in outcome[key]
            ):
                raise EdgeError(f"Recorded engine {key} evidence is unreadable.")
        if not isinstance(outcome["assumptions"], (list, tuple)):
            raise EdgeError("Recorded engine assumptions are unreadable.")


def _event_table(rows: list | tuple, empty_message: str) -> None:
    if rows:
        st.dataframe(list(rows), hide_index=True, width="stretch")
    else:
        st.info(empty_message)


def _ns_iso(value: object) -> str | None:
    try:
        return datetime.fromtimestamp(int(value) / 1e9, tz=UTC).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _quote_rows(dataset) -> list[dict]:
    return [{"t": _ns_iso(q.timestamp_ns), "bid": q.bid, "ask": q.ask} for q in dataset.quotes]


def _execution_chart(manifest: dict, outcome: dict, *, key: str) -> None:
    """Recorded fills over the fixture quotes; drawn only when the run's dataset checksum
    matches the bundled fixture, so no quote series is ever paired with the wrong run."""
    dataset = demo_dataset()
    if manifest.get("dataset_checksum") != dataset.checksum:
        st.caption("Execution tape unavailable: run dataset differs from the bundled fixture.")
        return
    fills = [
        {"t": _ns_iso(f.get("filled_at_ns")), "price": f.get("price"), "side": f.get("side"),
         "shown": f"{f.get('side', '')} {f.get('quantity', '')} @ {f.get('price', '')} "
                  f"{f.get('currency', '')} · fee {f.get('fee', '')}"}
        for f in outcome.get("fills", []) if isinstance(f, dict)
    ]
    html_block(provenance("synthetic", "engine"))
    charts.show(
        charts.execution_tape(_quote_rows(dataset), fills, unit=dataset.price_unit,
                              title="Execution tape · synthetic bid/ask with recorded fills"),
        alt_text="Step chart of synthetic bid and ask quotes with recorded engine fills marked",
        key=f"{key}-tape",
    )


def _render_run(record: dict, *, key: str) -> None:
    _check_record(record)
    manifest = record["manifest"]
    st.caption(
        f"Run {manifest['run_id']} · {manifest['scenario']} · "
        f"Status: {record['status']} · Started (UTC): {manifest['started_at']}"
    )
    st.caption(
        f"Recorded source: {manifest['source']} · "
        f"Fixture provenance timestamp: {manifest['retrieved_at']} (not a live quote)"
    )
    result = record["result"]
    if record["status"] == "failed":
        message = (
            result.get("error", "Inspect recorded evidence.")
            if isinstance(result, dict)
            else "Inspect recorded evidence."
        )
        st.error(f"Recorded engine run failed: {message}")
    elif record["status"] == "running":
        st.warning(
            "This recorded run has not finalized. No completed engine outcome is available."
        )
    else:
        outcome = result["outcome"]
        cash, pnl, quantity = st.columns(3)
        cash.metric("Ending cash (synthetic USD)", outcome["ending_cash"])
        pnl.metric("Realized P&L (synthetic USD)", outcome["realized_pnl"])
        quantity.metric(
            "Ending quantity (synthetic shares)", outcome["ending_quantity"]
        )
        st.caption(
            "Values above are recorded Nautilus engine outputs, not UI calculations or real investment performance."
        )
        _execution_chart(manifest, outcome, key=key)
        orders, fills, rejections, positions = st.tabs(
            ["Orders", "Fills", "Rejections", "Positions"]
        )
        with orders:
            _event_table(outcome["orders"], "No orders recorded by the engine.")
        with fills:
            _event_table(outcome["fills"], "No fills recorded by the engine.")
        with rejections:
            _event_table(outcome["rejections"], "No rejections recorded by the engine.")
        with positions:
            _event_table(outcome["positions"], "No positions recorded by the engine.")
        with st.expander("Recorded engine assumptions and limitations"):
            for assumption in outcome["assumptions"]:
                st.write(assumption)
            for limitation in manifest.get("limitations", []):
                st.write(limitation)
    with st.expander("Persisted evidence · manifest and result"):
        st.caption(
            f"Local evidence directory: {record.get('artifact_directory', 'not recorded')}"
        )
        st.json({"manifest": manifest, "result": result})
        st.download_button(
            "Download recorded evidence (JSON)",
            data=json.dumps(
                {"manifest": manifest, "result": result},
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ),
            file_name=f"edge-{manifest['run_id']}.json",
            mime="application/json",
            key=f"{key}-evidence",
            on_click="ignore",
        )


def _history(output_dir: Path) -> None:
    st.header("Persisted run history")
    st.caption(
        f"Local storage: {output_dir}. New sessions retain this history; opening a run never executes the engine."
    )
    store = RunStore(output_dir)
    try:
        if not store.database.exists():
            st.info(
                "No persisted runs yet. Run either synthetic acceptance scenario to create evidence."
            )
            return
        rows = store.list_runs()
        if not rows:
            st.info("The run database contains no runs.")
            return
        st.dataframe(rows, hide_index=True, width="stretch")
        selected = st.selectbox(
            "Inspect a persisted run",
            options=[row["run_id"] for row in rows],
            index=None,
            placeholder="Choose a recorded run",
            key="edge_history_selection",
        )
        if selected is not None:
            _render_run(store.get(selected), key="history")
    except READ_ERRORS as error:
        st.error(
            f"Run history unavailable or corrupted: {error}. Inspect the local database and evidence; nothing has been reset."
        )


def _masthead() -> None:
    """Static chrome; the only dynamic text is the render time and fixed labels."""
    from datetime import UTC, datetime

    rendered = datetime.now(UTC).strftime("%H:%M")
    html_block(
        '<div class="edge-mast">'
        '<div class="edge-mast-brand">'
        '<span class="edge-mast-logo" aria-hidden="true">EDGE</span>'
        '<span class="edge-mast-name"><b role="heading" aria-level="1">'
        'EDGE // Research workbench</b>'
        "<span>Equity Discovery &amp; Growth Engine · local decision support</span></span>"
        "</div>"
        '<div class="edge-mast-cells">'
        '<div class="edge-mast-cell"><small>Execution</small>'
        '<span class="edge-off">Live trading disabled</span></div>'
        '<div class="edge-mast-cell"><small>Market feed</small>'
        "<span>None · user-declared data</span></div>"
        '<div class="edge-mast-cell"><small>Build status</small>'
        "<span>Pre-production</span></div>"
        "</div>"
        '<div class="edge-mast-clock"><small>RENDERED UTC</small>'
        f'<span>{esc(rendered)}<i class="edge-cursor" aria-hidden="true"></i></span></div>'
        "</div>"
    )


def _breadcrumb(page: str) -> None:
    html_block(
        '<div class="edge-crumb">'
        f"<span>{esc(WORKSPACE_GROUPS[page])}</span>"
        '<span class="edge-crumb-sep">›</span>'
        f"<b>{esc(WORKSPACE_CODES[page])} {esc(WORKSPACE_TITLES[page])}</b>"
        '<span class="edge-crumb-sep">│</span>'
        f"<span>{esc(WORKSPACE_PURPOSE[page])}</span>"
        '<span class="edge-crumb-sep">│</span>'
        "<span>Production status: unfinished — external integration and "
        "real-market acceptance gates remain open</span>"
        "</div>"
    )


def main(output_dir: Path | None = None) -> None:
    """Dispatch native navigation; rendering alone never runs an engine or lookup."""
    output_dir = (
        DEFAULT_OUTPUT_DIR if output_dir is None else Path(output_dir).resolve()
    )
    _apply_navigation_request(_journal_root(output_dir))
    st.set_page_config(page_title="EDGE · Research workbench", layout="wide")
    st.markdown(f"<style>{_TERMINAL_CSS}</style>", unsafe_allow_html=True)
    if st.session_state.get("edge_workspace") not in WORKSPACES:
        st.session_state.pop("edge_workspace", None)
    _masthead()
    page = st.radio(
        "Function",
        list(WORKSPACES),
        horizontal=True,
        key="edge_workspace",
        format_func=WORKSPACE_LABELS.__getitem__,
        on_change=_stop_monitoring,
        args=(output_dir,),
    )
    _breadcrumb(page)
    from edge import ui_grid

    ui_grid.begin_page()
    ui_grid.load_into_session(output_dir)
    _render_workspace(page, output_dir)
    ui_grid.toolbar(output_dir)


def _render_workspace(page: str, output_dir: Path) -> None:
    if page != "Monitoring":
        st.session_state[f"edge_monitor_enabled_{output_dir.resolve()}"] = False
        st.session_state[f"edge_monitor_active_{output_dir.resolve()}"] = False
    if page != "Research lab":
        from edge.dashboard_research_lab import cancel_on_leave

        cancel_on_leave()
    if page == "SEC filing research":
        from edge.dashboard_sec import render

        render()
    elif page == "Research journal":
        _render_journal(output_dir)
    elif page == "Backup and recovery":
        from edge.dashboard_backup import render

        render(output_dir)
    elif page == "Provider settings":
        from edge.dashboard_provider import render

        render(output_dir)
    elif page == "Company watchlist":
        from edge.dashboard_company import render

        render(output_dir)
    elif page in ("Manual portfolio", "Research / decisions", "Discovery", "Research lab", "Monitoring", "Application recovery"):
        from importlib import import_module

        module = {
            "Manual portfolio": "dashboard_portfolio",
            "Research / decisions": "dashboard_decisions",
            "Discovery": "dashboard_discovery",
            "Research lab": "dashboard_research_lab",
            "Monitoring": "dashboard_monitoring",
            "Application recovery": "dashboard_application_backup",
        }[page]
        import_module(f"edge.{module}").render(output_dir)
    elif page == "Command center":
        from edge.dashboard_home import render

        render(output_dir, _journal_root(output_dir))
    else:
        _workbench(output_dir)


def _workbench(output_dir: Path) -> None:
    """Preserve the four-pane synthetic acceptance workflow."""
    st.warning(
        "SYNTHETIC DATA ONLY — fictional instrument and quotes. This is not stock "
        "discovery, a portfolio, market research, a real-market backtest or investment advice. "
        "No broker or paid connection. Optional SEC metadata research is separate and "
        "cannot be used as simulation input."
    )
    dataset = demo_dataset()
    drawn = {"runs": False}  # panel order is user-configurable

    def _acceptance(ctx: PanelContext) -> None:
        with st.container(border=True, key="edge_acceptance"):
            st.header("Engine acceptance")
            ready = False
            try:
                installed = simulation_service.engine_version()
                from nautilus_trader.backtest import BacktestEngine

                st.success(
                    f"Engine doctor: NautilusTrader {installed} · native {BacktestEngine.__name__} import verified"
                )
                st.caption(
                    "Doctor checks version and import only. Use the fixture buttons below to test engine behavior."
                )
                ready = True
            except (EdgeError, OSError, sqlite3.Error, ImportError) as error:
                st.error(
                    f"Engine doctor failed: {error}. Use the project's isolated simulation environment."
                )

            st.caption(
                "Each action runs one local acceptance scenario and saves its evidence."
            )
            # Saturated terminal keys, but native buttons: no unimplemented F-key claims.
            do_round_trip = st.button(
                "Run synthetic round-trip",
                key="run_round_trip",
                type="primary",
                disabled=not ready,
                width="stretch",
            )
            do_rejection = st.button(
                "Run engine risk-rejection",
                key="run_risk_rejection",
                disabled=not ready,
                width="stretch",
            )
            st.caption("Keyboard: Tab to a control; Enter or Space activates a button.")
            if do_round_trip or do_rejection:
                scenario = "round-trip" if do_round_trip else "risk-rejection"
                try:
                    with st.spinner(
                        f"Running local {scenario} acceptance and persisting evidence…"
                    ):
                        record = simulation_service.run(
                            dataset,
                            SimulationConfig(scenario=scenario),
                            output_dir,
                        )
                        _check_record(record)
                    st.session_state[RECENT_RUN_KEY] = record
                except READ_ERRORS as error:
                    st.error(
                        f"Acceptance attempt failed: {error}. No successful replacement result "
                        "was created. Any previous session result remains displayed below."
                    )
                else:
                    if drawn["runs"]:  # a run pane above this one is now stale
                        st.rerun()

    def _fixture(ctx: PanelContext) -> None:
        with st.container(border=True, key="edge_fixture"):
            st.header("Synthetic fixture / provenance")
            st.write(f"**Instrument:** {dataset.instrument_id} — {dataset.company_name}")
            st.caption(
                f"Source: {dataset.source} · Fixture provenance timestamp: {dataset.retrieved_at} · "
                f"Currency / quote unit: {dataset.currency} / {dataset.price_unit}"
            )
            st.caption(
                "Quote event timestamps are historical fixture inputs, not current prices. "
                "Fixed bid/ask quotes; no market impact, latency or queue-position realism. "
                "Corporate actions: not applicable to this synthetic instrument."
            )
            html_block(provenance("synthetic"))
            charts.show(
                charts.execution_tape(_quote_rows(dataset), unit=dataset.price_unit,
                                      title="Fixture quote stream · bid / ask spread"),
                alt_text="Step chart of the synthetic fixture bid and ask quotes",
                key="edge_fixture_tape",
            )
            with st.expander("Synthetic quote input and fixed acceptance configuration"):
                st.write(f"Dataset checksum: `{dataset.checksum}`")
                st.dataframe(
                    [
                        {
                            "event_timestamp_ns": quote.timestamp_ns,
                            "bid": str(quote.bid),
                            "ask": str(quote.ask),
                            "bid_size": quote.bid_size,
                            "ask_size": quote.ask_size,
                        }
                        for quote in dataset.quotes
                    ],
                    hide_index=True,
                    width="stretch",
                )
                config = SimulationConfig()
                st.write(
                    {
                        "initial_cash_synthetic_USD": str(config.initial_cash),
                        "order_quantity": config.quantity,
                        "commission_per_order_synthetic_USD": str(
                            config.commission_per_order
                        ),
                    }
                )
                st.caption(
                    "Fixed service defaults, not user risk preferences. Risk-rejection uses the engine's deliberately restrictive acceptance policy."
                )
            with st.expander("Planning context · pending"):
                st.info("Future components — pending, not active functionality.")
                st.write(
                    "Discovery, company research, portfolio workflows and validated market-data "
                    "ingestion remain future work."
                )
                st.write(
                    "An opt-in OpenBB v5 adapter can retrieve SEC filing metadata through "
                    "the SEC filing research workspace, `edge sec-filings TICKER` or a Python API call. "
                    "It has not yet been "
                    "verified against a live SEC response. Market-price ingestion and Qlib "
                    "research remain future work."
                )
                st.caption(
                    "No live mode, broker setup, user risk settings, code execution or uploaded strategy controls."
                )

    def _recent(ctx: PanelContext) -> None:
        drawn["runs"] = True
        with st.container(border=True, key="edge_recent"):
            st.header("Most recent run in this session")
            if RECENT_RUN_KEY not in st.session_state:
                st.info(
                    "No run in this session. Choose an acceptance scenario above, or inspect persisted history."
                )
            else:
                try:
                    _render_run(st.session_state[RECENT_RUN_KEY], key="recent")
                except READ_ERRORS as error:
                    st.error(
                        f"Current run evidence unavailable or corrupted: {error}. Inspect persisted evidence."
                    )

    def _history_pane(ctx: PanelContext) -> None:
        drawn["runs"] = True
        with st.container(border=True, key="edge_history"):
            _history(output_dir)

    grid("engine", [
        Panel("accept", "ENG", "Engine acceptance", _acceptance, w=6, h=12, min_w=4,
              fit=True, framed=False),
        Panel("fixture", "FIX", "Synthetic fixture / provenance", _fixture, w=6, h=12,
              min_w=4, fit=True, framed=False),
        Panel("recent", "RUN", "Most recent run in this session", _recent, w=6, h=12,
              min_w=4, fit=True, framed=False),
        Panel("history", "HIS", "Persisted run history", _history_pane, w=6, h=12,
              min_w=4, fit=True, framed=False),
    ])


if __name__ == "__main__":
    main()
