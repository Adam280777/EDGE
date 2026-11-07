"""Synthetic acceptance, explicit SEC metadata and local notes; no live trading."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import streamlit as st

from edge import simulation_service
from edge.desktop_paths import app_data_root
from edge.domain import SimulationConfig
from edge.errors import EdgeError
from edge.fixtures import demo_dataset
from edge.run_store import RunStore

DEFAULT_OUTPUT_DIR = app_data_root(Path(__file__).resolve().parent / "data")
RECENT_RUN_KEY = "edge_recent_run"
READ_ERRORS = (EdgeError, OSError, sqlite3.Error, json.JSONDecodeError)
WORKSPACES = (
    "Engine acceptance", "SEC filing research", "Research journal",
    "Backup and recovery", "Provider settings", "Company watchlist",
)


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


_TERMINAL_CSS = """
:root {
  color-scheme: dark;
  --edge-void: #050505;
  --edge-panel: #10100d;
  --edge-rule: #6e6045;
  --edge-ink: #ffc15a;
  --edge-muted: #c6ad83;
  --edge-success: #70e49a;
  --edge-amber: #ffb000;
  --edge-cyan: #65dce8;
  --edge-alert: #ff817a;
  --edge-mono: "Cascadia Mono", "Cascadia Code", "Consolas", "Courier New", monospace;
}

html, body {
  font-family: var(--edge-mono);
}
[data-testid="stAppViewContainer"] {
  color: var(--edge-ink);
  background-color: var(--edge-void);
}
[data-testid="stHeader"] { background: transparent; }
[data-testid="stMainBlockContainer"] {
  max-width: 1600px;
  padding: 2.5rem 1.5rem 1.5rem;
}
[data-testid="stVerticalBlock"], [data-testid="stHorizontalBlock"] {
  gap: 0.65rem;
}
[data-testid="stColumn"] { min-width: 0; }
.st-key-edge_acceptance, .st-key-edge_fixture,
.st-key-edge_recent, .st-key-edge_history,
[data-testid="stForm"],
[data-testid="stExpander"] details {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
  box-shadow: none;
  background: var(--edge-void);
}
.stApp h1, .stApp h2, .stApp h3,
.stApp button, .stApp input, .stApp textarea, .stApp select,
.stApp p, .stApp label, .stApp li {
  font-family: var(--edge-mono);
}
.stApp h1, .stApp h2, .stApp h3 {
  color: var(--edge-ink);
  font-variant-numeric: tabular-nums;
}
.stApp h1 {
  font-size: clamp(1.25rem, 2vw, 1.7rem);
  letter-spacing: 0.035em;
  text-transform: uppercase;
  padding-block-end: 0.4rem;
  border-block-end: 1px solid var(--edge-rule);
  overflow-wrap: normal;
  word-break: normal;
}
.stApp h2 {
  margin: 0;
  padding: 0 0 0.45rem;
  border-block-end: 1px solid var(--edge-rule);
  color: var(--edge-cyan);
  font-size: 0.95rem;
  letter-spacing: 0.055em;
  text-transform: uppercase;
}
.stApp p, .stApp label, .stApp li, [data-testid="stCaptionContainer"] {
  color: var(--edge-ink);
  font-size: 0.875rem;
  line-height: 1.4;
}
[data-testid="stCaptionContainer"],
[data-testid="stCaptionContainer"] p {
  color: var(--edge-muted);
  opacity: 1;
}
[data-testid="stCaptionContainer"] p { font-size: 0.8rem; }
[data-testid="stMetric"] {
  padding: 0.35rem 0;
  border-block-end: 1px solid var(--edge-rule);
}
[data-testid="stMetricLabel"] { color: var(--edge-muted); }
[data-testid="stMetricLabel"] p { white-space: normal; }
[data-testid="stMetricValue"] {
  color: var(--edge-ink);
  font-size: 1.4rem;
  font-family: var(--edge-mono);
  font-variant-numeric: tabular-nums;
}
[data-testid="stDataFrame"] {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
}
[data-testid="stDataFrame"] [role="columnheader"] {
  color: var(--edge-cyan);
  font-family: var(--edge-mono);
}
[data-testid="stAlert"] {
  border: 1px solid var(--edge-rule);
  border-radius: 0;
}
[data-testid="stAlertContainer"] {
  color: var(--edge-ink) !important;
  background: var(--edge-panel) !important;
  border-radius: 0 !important;
  border: 0 !important;
  padding: 0.55rem 0.65rem;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentInfo"]) {
  border-color: var(--edge-cyan) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentSuccess"]) {
  border-color: var(--edge-success) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentWarning"]) {
  border-color: var(--edge-amber) !important;
}
[data-testid="stAlert"]:has([data-testid="stAlertContentError"]) {
  border-color: var(--edge-alert) !important;
}
[data-testid="stAlertContentInfo"] { color: var(--edge-cyan) !important; }
[data-testid="stAlertContentSuccess"] { color: var(--edge-success) !important; }
[data-testid="stAlertContentWarning"] { color: var(--edge-amber) !important; }
[data-testid="stAlertContentError"] { color: var(--edge-alert) !important; }
[data-testid="stAlertContentInfo"] p { color: var(--edge-cyan); }
[data-testid="stAlertContentSuccess"] p { color: var(--edge-success); }
[data-testid="stAlertContentWarning"] p { color: var(--edge-ink); }
[data-testid="stAlertContentError"] p { color: var(--edge-alert); }
.stButton > button, [data-testid="stDownloadButton"] > button,
[data-testid="stFormSubmitButton"] > button {
  min-height: 2.25rem;
  color: var(--edge-void);
  background: var(--edge-cyan);
  border: 1px solid var(--edge-cyan);
  border-radius: 0;
  font-family: var(--edge-mono);
}
.stButton > button p, [data-testid="stDownloadButton"] > button p,
[data-testid="stFormSubmitButton"] > button p { color: inherit; }
.stButton > button:hover, [data-testid="stDownloadButton"] > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
  color: var(--edge-void);
  background: var(--edge-ink);
  border-color: var(--edge-ink);
}
.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"] {
  color: var(--edge-void);
  background: var(--edge-amber);
  border-color: var(--edge-amber);
}
.stButton > button[kind="primary"]:hover,
[data-testid="stFormSubmitButton"] > button[kind="primaryFormSubmit"]:hover {
  background: var(--edge-ink);
  border-color: var(--edge-ink);
}
.stButton > button:disabled,
[data-testid="stFormSubmitButton"] > button:disabled {
  color: var(--edge-muted);
  background: var(--edge-panel);
  border-color: var(--edge-rule);
}
:focus-visible {
  outline: 2px solid var(--edge-cyan) !important;
  outline-offset: 3px;
}
[data-testid="stTabs"] button[aria-selected="true"] {
  color: var(--edge-amber);
  border-block-end-color: var(--edge-amber);
}
[data-baseweb="tab-list"] { gap: 1rem; }
[data-baseweb="tab"] { min-height: 2rem; padding-inline: 0.25rem; }
input, textarea, [data-baseweb="select"] > div {
  color: var(--edge-ink) !important;
  background-color: var(--edge-panel) !important;
  border-color: var(--edge-rule) !important;
  border-radius: 0 !important;
  caret-color: var(--edge-amber);
}
a { color: var(--edge-cyan); text-underline-offset: 0.2em; }
a:hover { color: var(--edge-amber); }
::selection { color: var(--edge-void); background: var(--edge-amber); }
::-webkit-scrollbar { width: 12px; height: 12px; }
::-webkit-scrollbar-track { background: var(--edge-void); }
::-webkit-scrollbar-thumb {
  background: var(--edge-rule);
  border: 3px solid var(--edge-void);
  border-radius: 0;
}
/* Native columns retain reading order; stack panes before their copy gets cramped. */
@media (max-width: 1050px) {
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_acceptance),
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_recent) {
    flex-direction: column;
  }
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_acceptance) > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] .st-key-edge_recent) > [data-testid="stColumn"] {
    width: 100%;
    flex: 1 1 100%;
  }
}
@media (max-width: 700px) {
  [data-testid="stMainBlockContainer"] { padding: 1rem 1rem 2rem; }
  .stApp h1 { font-size: 1.25rem; }
  .stButton > button, [data-testid="stDownloadButton"] > button,
  [data-testid="stFormSubmitButton"] > button { min-height: 2.75rem; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition-duration: 0.01ms !important; }
}
"""


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


def main(output_dir: Path | None = None) -> None:
    """Dispatch native navigation; rendering alone never runs an engine or lookup."""
    output_dir = (
        DEFAULT_OUTPUT_DIR if output_dir is None else Path(output_dir).resolve()
    )
    _apply_navigation_request(_journal_root(output_dir))
    st.set_page_config(page_title="EDGE · Research workbench", layout="wide")
    st.markdown(f"<style>{_TERMINAL_CSS}</style>", unsafe_allow_html=True)
    st.title("EDGE // Research workbench")
    st.caption(
        "Local synthetic engine acceptance / Optional external SEC metadata / User-authored journal · Live trading disabled"
    )
    page = st.radio(
        "Workspace",
        list(WORKSPACES),
        horizontal=True,
        key="edge_workspace",
    )
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
    acceptance, fixture = st.columns(2, gap="small")
    with acceptance, st.container(border=True, key="edge_acceptance"):
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

    with fixture, st.container(border=True, key="edge_fixture"):
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

    recent, history = st.columns(2, gap="small")
    with recent, st.container(border=True, key="edge_recent"):
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
    with history, st.container(border=True, key="edge_history"):
        _history(output_dir)


if __name__ == "__main__":
    main()
