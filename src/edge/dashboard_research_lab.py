"""Explicit, session-local synthetic research experiments.

Charts only visualise values the isolated worker returned for a fixed synthetic
fixture; nothing is simulated, back-tested or recomputed here. NautilusTrader
(Engine workspace) remains the sole simulation authority.
"""

import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from edge import ui_charts as charts
from edge.dashboard_manual import readout
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    fmt_number,
    fmt_pct,
    fmt_time,
    status_board,
    status_light,
    tiles,
)

_FIXTURES = {
    "Qlib": ("qlib", "qlib-linear-three-assets-v1"),
    "skfolio": ("skfolio", "minimum-variance-three-assets"),
    "Kronos": ("kronos", "kronos-mini-ohlcv-v1"),
}
_JOB_KEY = "edge_research_lab_job"
_COOLDOWN = 5


@dataclass
class _Job:
    framework: str
    fixture: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    status: str = "running"
    message: str = ""
    response: object = None
    finished_at: float = 0.0
    finished_wall: datetime | None = None
    recorded: bool = False


def _run(job: _Job) -> None:
    """Run outside Streamlit; publish only through the session-owned job."""
    from edge.research_workers import run_synthetic

    try:
        if job.cancel.is_set():
            job.status, job.message = "cancelled", "Cancelled before execution."
        else:
            result = run_synthetic(
                job.framework, job.fixture, timeout_seconds=120, cancel_event=job.cancel,
            )
            if job.cancel.is_set():
                job.status, job.message = "cancelled", "Cancellation requested; result discarded."
            else:
                job.status = result.status
                job.message = result.error or ""
                job.response = result.response
    except Exception:  # noqa: BLE001 -- never expose worker internals or user paths
        job.status, job.message = "error", "Research worker could not complete."
    finally:
        job.finished_at = time.monotonic()
        job.finished_wall = datetime.now(UTC)
        job.done.set()


def cancel_on_leave() -> None:
    """Revoke an in-flight job before navigating to another workspace."""
    job = st.session_state.get(_JOB_KEY)
    if isinstance(job, _Job) and not job.done.is_set():
        job.cancel.set()


def _valid_hash(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{64}", value) is not None


def _accepted_result(job: _Job) -> Mapping | None:
    response = job.response
    if not isinstance(response, Mapping):
        return None
    if (type(response.get("schema_version")) is not int or response.get("schema_version") != 1
            or response.get("framework") != job.framework or response.get("status") != "ok"
            or response.get("synthetic") is not True or response.get("fixture_id") != job.fixture
            or not _valid_hash(response.get("request_sha256"))
            or not isinstance(response.get("limitations"), list)
            or not all(isinstance(item, str) for item in response["limitations"])):
        return None
    runtime = response.get("runtime")
    if not (isinstance(runtime, Mapping)
            and runtime.get("local_consistency_verified") is True
            and runtime.get("signed_security_attestation") is False
            and all(isinstance(runtime.get(key), str) and runtime[key] for key in
                    ("python_version", "primary_package", "primary_version"))
            and _valid_hash(runtime.get("worker_sha256"))):
        return None
    result = response.get("result")
    if not isinstance(result, Mapping):
        return None
    if job.framework == "skfolio":
        if not (isinstance(result.get("package_version"), str)
                and _valid_hash(result.get("panel_digest"))
                and isinstance(result.get("limitations"), list)
                and all(isinstance(item, str) for item in result["limitations"])):
            return None
    elif not (result.get("framework_executed") is True
              and isinstance(result.get("engine_version"), str)
              and _valid_hash(result.get("fixture_sha256"))):
        return None
    return response


_HISTORY_KEY = "edge_research_lab_history"
_KRONOS_COLUMNS = ("open", "high", "low", "close", "volume", "amount")


def _num_text(value: object, places: int = 6) -> str:
    return fmt_number(value, places=places) if charts.num(value) is not None else UNAVAILABLE


def _str_list(value: object) -> list[str] | None:
    if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
        return value
    return None


def _record(job: _Job) -> None:
    """Append one session-only history row per finished job; nothing is persisted."""
    if job.recorded or not job.done.is_set():
        return
    job.recorded = True
    accepted = job.status == "ok" and _accepted_result(job) is not None
    digest = job.response.get("request_sha256") if isinstance(job.response, Mapping) else None
    history = st.session_state.setdefault(_HISTORY_KEY, [])
    history.append({
        "Finished (UTC)": fmt_time(job.finished_wall),
        "_when": job.finished_wall.isoformat() if job.finished_wall else None,
        "Engine": job.framework,
        "Fixed fixture": job.fixture,
        "Outcome": job.status,
        "Result accepted": "yes" if accepted else "no",
        "Request digest prefix": f"{digest[:12]}…" if accepted and _valid_hash(digest) else "—",
    })


def _qlib(result: Mapping) -> None:
    mse, baseline = result.get("synthetic_test_mse"), result.get("constant_training_mean_baseline_mse")
    tiles([
        Tile("Test MSE", _num_text(mse), "Synthetic holdout", "violet"),
        Tile("Baseline MSE", _num_text(baseline), "Constant training mean", "ink"),
        Tile("Train / valid / test rows",
             " / ".join(str(result.get(k, "?")) for k in ("train_rows", "validation_rows", "test_rows")),
             f"Input rows {result.get('input_rows', UNAVAILABLE)}", "cyan"),
        Tile("Holdout independent", "yes" if result.get("holdout_training_independent") is True else "not shown",
             "Perturbing holdout left coefficients unchanged", "green"
             if result.get("holdout_training_independent") is True else "muted"),
    ], columns=4)
    charts.show(charts.bullet(mse, baseline, label="Synthetic test MSE", value_text=_num_text(mse),
                              limit_text=_num_text(baseline),
                              title="Model test MSE vs constant-mean baseline · lower is better"),
                alt_text="Bullet comparing synthetic test MSE with the baseline MSE",
                key="edge_lab_chart_qlib_mse")
    predictions = _str_list(result.get("predictions"))
    rows = [{"row": index, "prediction": value} for index, value in enumerate(predictions or (), 1)]
    if not charts.show(charts.lines(rows, x="row", y="prediction", y_title="Model output",
                                    title="Test-segment predictions in worker output order (synthetic)"),
                       alt_text="Line of synthetic Qlib test-segment predictions", height=240,
                       key="edge_lab_chart_qlib_pred"):
        empty("Worker returned no plottable predictions.")
    if isinstance(result.get("interpretation"), str):
        st.caption(result["interpretation"])


def _kronos(result: Mapping) -> None:
    tiles([
        Tile("Input rows", str(result.get("input_rows", UNAVAILABLE)), "Manufactured OHLCV", "violet"),
        Tile("Prediction steps", str(result.get("prediction_rows", UNAVAILABLE)), "Model output rows", "ink"),
        Tile("Device", str(result.get("device", UNAVAILABLE)), f"torch {result.get('torch_version', '?')}", "cyan"),
        Tile("Models verified", str(sum(1 for m in result.get("models", ()) if isinstance(m, Mapping)
                                        and m.get("payload_verified") is True)),
             "Local weight hashes", "green"),
    ], columns=4)
    raw = result.get("predictions")
    rows = []
    if isinstance(raw, list) and all(isinstance(r, list) and len(r) == len(_KRONOS_COLUMNS)
                                     and all(isinstance(v, str) for v in r) for r in raw):
        rows = [{"step": f"t+{index}", **dict(zip(_KRONOS_COLUMNS, r, strict=True))}
                for index, r in enumerate(raw, 1)]
    if not charts.show(charts.lines(rows, x="step", y="close", band=("low", "high"), x_type="O",
                                    y_title="Synthetic price units",
                                    title="Predicted close with predicted low–high range (synthetic)"),
                       alt_text="Line of synthetic Kronos predicted close with low-high band",
                       height=240, key="edge_lab_chart_kronos"):
        empty("Worker returned no plottable prediction rows.")
    if rows:
        st.dataframe(rows, hide_index=True, width="stretch")
        st.caption("Column order as emitted by the worker (open, high, low, close, volume, amount). "
                   "No OHLC validity is checked or implied.")
    if isinstance(result.get("interpretation"), str):
        st.caption(result["interpretation"])


def _skfolio(result: Mapping) -> None:
    weights = _str_list(result.get("weights")) or []
    naive = _str_list(result.get("naive_weights")) or []
    expected = _str_list(result.get("expected_asset_returns")) or []
    tiles([
        Tile("Solver status", str(result.get("solver_status", UNAVAILABLE)),
             str(result.get("solver", "")), "green" if result.get("solver_status") == "optimal" else "amber"),
        Tile("Est. period variance", _num_text(result.get("estimated_period_variance")), "Estimate", "violet"),
        Tile("Est. net period return", fmt_pct(result.get("estimated_net_period_return"), places=3),
             "Estimate, not promised", "violet"),
        Tile("Est. turnover", fmt_pct(result.get("estimated_turnover"), places=2), "vs previous weights", "ink"),
        Tile("Est. one-off cost", fmt_pct(result.get("estimated_one_off_cost_fraction"), places=3),
             "Fraction of NAV", "ink"),
        Tile("Train / holdout", f"{result.get('train_count', '?')} / {result.get('holdout_count', '?')}",
             f"Holdout from {result.get('holdout_start', UNAVAILABLE)}", "cyan"),
    ], columns=6)
    left, right = st.columns(2, gap="small")
    with left:
        donut = [{"label": f"Asset {i}", "value": w, "shown": fmt_pct(w, places=2)}
                 for i, w in enumerate(weights, 1)]
        if not charts.show(charts.donut(donut, detail="shown", title="Solver weights · fixture assets",
                                        center="WEIGHTS"),
                           alt_text="Donut of synthetic minimum-variance weights", height=250,
                           key="edge_lab_chart_sk_weights"):
            empty("No positive weights returned to draw.")
        if any((charts.num(w) or 0) < 0 for w in weights):
            st.caption("Negative weights are listed in the table but cannot be drawn in a donut.")
    with right:
        cov = result.get("covariance")
        cells = []
        if isinstance(cov, list):
            for i, row in enumerate(cov, 1):
                for j, value in enumerate(row if isinstance(row, list) else (), 1):
                    cells.append({"a": f"Asset {i}", "b": f"Asset {j}", "v": value,
                                  "shown": _num_text(value, 6)})
        if not charts.show(charts.heat(cells, x="a", y="b", value="v", text="shown",
                                       title="Estimated covariance (training window)"),
                           alt_text="Heat map of the estimated asset covariance matrix", height=250,
                           key="edge_lab_chart_sk_cov"):
            empty("Covariance matrix not returned.")
    table = [{"Asset": f"Asset {i}", "Solver weight": fmt_pct(w, places=2),
              "Equal-weight benchmark": fmt_pct(naive[i - 1], places=2) if i <= len(naive) else UNAVAILABLE,
              "Est. period return": fmt_pct(expected[i - 1], places=3) if i <= len(expected) else UNAVAILABLE}
             for i, w in enumerate(weights, 1)]
    if table:
        st.dataframe(table, hide_index=True, width="stretch")
    holdout, bench = result.get("holdout"), result.get("naive_holdout")
    if isinstance(holdout, Mapping):
        keys = [k for k in holdout if isinstance(holdout[k], str)]
        st.dataframe([{"Holdout statistic": k.replace("_", " "),
                       "Solver weights": _num_text(holdout[k]),
                       "Equal weight": _num_text(bench.get(k)) if isinstance(bench, Mapping) else UNAVAILABLE}
                      for k in keys], hide_index=True, width="stretch")
        st.caption("Holdout summary statistics only; the worker returns no NAV path, so no equity "
                   "curve is drawn. Simulations require NautilusTrader (Engine workspace).")
    st.caption("Fixed synthetic panel; worker result is not a proposed trade.")
    with st.expander("Raw worker fields"):
        for key in ("solver_status", "weights", "estimated_period_variance",
                    "estimated_net_period_return", "holdout", "naive_holdout", "limitations"):
            if key in result:
                st.text(f"{key}: {result[key]}")


def _outcome(job: _Job) -> None:
    """Refused, failed or cancelled run, or a response that failed acceptance."""
    if job.status == "ok":
        st.error("Experiment response failed identity or provenance checks; no result accepted.")
        return
    st.warning(f"Experiment {job.status}. No research result accepted.")
    if job.message:
        st.text(job.message)
    elif isinstance(job.response, Mapping):
        reason = job.response.get("reason", job.response.get("reasons"))
        if isinstance(reason, str):
            st.text(reason)
        elif isinstance(reason, list):
            for item in reason:
                if isinstance(item, str):
                    st.text(item)


def _result(job: _Job, response: Mapping) -> None:
    result = response["result"]
    st.success("Synthetic experiment completed. This is not financial evidence or a trading signal.")
    if job.framework == "skfolio":
        _skfolio(result)
    elif job.framework == "qlib":
        _qlib(result)
    else:
        _kronos(result)
    if job.framework != "skfolio":
        with st.expander("Raw worker fields"):
            readout(result)


def _provenance(job: _Job, response: Mapping) -> None:
    result = response["result"]
    st.text(f"Engine: {job.framework} / fixed fixture: {job.fixture}")
    runtime = response["runtime"]
    st.text(f"Runtime: Python {runtime['python_version']} / {runtime['primary_package']} {runtime['primary_version']}")
    st.text(f"Worker SHA-256: {runtime['worker_sha256']}")
    st.text(f"Request SHA-256: {response['request_sha256']}")
    if job.framework == "skfolio":
        st.text(f"Engine version: {result['package_version']}")
        st.text(f"Input panel SHA-256: {result['panel_digest']}")
    else:
        st.text(f"Engine version: {result['engine_version']}")
        st.text(f"Fixture SHA-256: {result['fixture_sha256']}")
    st.caption("Local hash consistency is not a signed security attestation.")


def _limits(response: Mapping) -> None:
    st.caption("Limitations")
    for limitation in response["limitations"]:
        st.text(limitation)


@st.fragment(run_every=1)
def _running(job: _Job) -> None:
    if job.done.is_set():
        st.rerun()
    st.info("Checking local runtime and running the selected fixture; no percentage estimate is available.")
    if st.button("Cancel synthetic experiment", key="edge_research_cancel"):
        job.cancel.set()
        st.warning("Cancellation requested. Waiting for the owned worker to stop.")


@st.fragment(run_every=1)
def _wait_cooldown(job: _Job) -> None:
    if time.monotonic() - job.finished_at >= _COOLDOWN:
        st.rerun()
    st.caption("Brief cooldown before another experiment.")


def _history(history: list, ctx: PanelContext) -> None:
    if not history:
        empty("No experiments run in this session.",
              hint="Choose an engine above and press Run selected synthetic experiment.")
        return
    if ctx.view in ("both", "chart"):
        height = 200 if ctx.fit or ctx.view == "both" else ctx.chart_height()
        charts.show(charts.step_area([{"when": row["_when"], "series": row["Engine"]} for row in history],
                                     title="Runs this session · cumulative by engine", y_title="Runs"),
                    alt_text="Stepped cumulative count of synthetic experiment runs per engine",
                    height=height, key="edge_lab_chart_history")
    if ctx.view in ("both", "table"):
        st.dataframe([{k: v for k, v in row.items() if not k.startswith("_")} for row in reversed(history)],
                     hide_index=True, width="stretch")


def _status(job: object, running: bool, history: list, ctx: PanelContext) -> None:
    last = history[-1] if history else None
    tiles([
        Tile("Job state", "running" if running else job.status if isinstance(job, _Job) else "idle",
             f"{job.framework}" if isinstance(job, _Job) else "No experiment started",
             "amber" if running else "violet"),
        Tile("Runs this session", str(len(history)), "Session memory only", "ink"),
        Tile("Accepted results", str(sum(1 for r in history if r["Result accepted"] == "yes")),
             "Passed identity / digest checks", "green"),
        Tile("Last finished", last["Finished (UTC)"] if last else UNAVAILABLE,
             last["Engine"] if last else "—", "cyan" if last else "muted"),
    ], columns=4 if ctx.w >= 6 else 2)
    status_board([
        status_light("Synthetic worker", "warn" if running else "ok" if history else "off",
                     "Running" if running else "Idle"),
        status_light("Market data", "off", "None — fixed synthetic fixtures only"),
        status_light("Simulation authority", "off", "NautilusTrader only (Engine workspace)"),
    ])


def _control(job: object, running: bool, cooling: bool) -> None:
    engine = st.selectbox("Synthetic experiment", tuple(_FIXTURES), key="edge_research_engine")
    framework, fixture = _FIXTURES[engine]
    st.text(f"Fixed synthetic input: {fixture}")
    st.caption(
        "No user data, arbitrary scripts or provider calls. Runs use an isolated worker "
        "with a 120-second limit; explicit cancellation or leaving this workspace requests a stop."
    )
    st.caption(
        "In a source browser session, closing the tab does not guarantee immediate cancellation; "
        "use Cancel or leave the workspace. The worker timeout remains in force."
    )
    if st.button("Check selected runtime consistency"):
        from edge.research_workers import research_runtime_readiness

        try:
            blockers = research_runtime_readiness(framework)
        except (OSError, KeyError, ValueError):
            st.error("Runtime consistency could not be checked. Review the local installation and retry.")
        else:
            if blockers:
                st.warning("Runtime consistency check blocked.")
                for reason in blockers:
                    st.text(reason)
            else:
                st.info("Local runtime/resource hashes match. This does not verify execution or financial data.")
    st.caption("The explicit consistency check reads local runtime, worker and model hashes; it may take time.")
    if st.button("Run selected synthetic experiment", disabled=running or cooling, type="primary"):
        job = _Job(framework, fixture)
        st.session_state[_JOB_KEY] = job
        threading.Thread(target=_run, args=(job,), daemon=True).start()
        st.rerun()
    if running:
        _running(job)
    elif cooling:
        _wait_cooldown(job)


def _boundary() -> None:
    st.caption(
        "Real-market experiments remain blocked pending independently accepted "
        "data, identity, rights, corporate-action and point-in-time evidence. "
        "A user declaration does not replace those checks."
    )


def panels(job: object, running: bool, cooling: bool, history: list) -> list[Panel]:
    """Lab panels. Job state and history are read once before layout; result
    panels exist only for a finished job, so no panel implies a result that is absent."""
    items = [
        Panel("status", "LAB", "Synthetic research lab", lambda c: _status(job, running, history, c),
              w=12, h=5, min_w=4, fit=True, meta="Fixed fixtures · isolated workers",
              badges=("synthetic",)),
        Panel("control", "RUN", "Experiment control", lambda c: _control(job, running, cooling),
              w=12, h=9, min_w=4, fit=True, badges=("synthetic",)),
    ]
    if not running and isinstance(job, _Job):
        response = _accepted_result(job) if job.status == "ok" else None
        if response is None:
            items.append(Panel("outcome", "OUT", "Experiment outcome", lambda c: _outcome(job),
                               w=12, h=4, min_w=3, fit=True, meta=f"{job.framework} · {job.fixture}",
                               badges=("synthetic",)))
        else:
            items += [
                Panel("result", "RES", f"{job.framework} synthetic result",
                      lambda c: _result(job, response), w=12, h=18, min_w=4, fit=True,
                      meta=f"Finished {fmt_time(job.finished_wall)}", badges=("synthetic",)),
                Panel("provenance", "PRV", "Provenance & digests", lambda c: _provenance(job, response),
                      w=7, h=8, min_w=4, fit=True, badges=("synthetic",)),
                Panel("limits", "LIM", "Limitations", lambda c: _limits(response), w=5, h=8, min_w=3,
                      fit=True, badges=("synthetic",)),
            ]
    items += [
        Panel("history", "HST", "Run history", lambda c: _history(history, c), w=12, h=10, min_w=3,
              fit=True, meta="This session only · not saved", badges=("synthetic",),
              views=(("both", "Chart + table"), ("chart", "Cumulative chart"), ("table", "Table"))),
        Panel("boundary", "BND", "Real-data boundary", lambda c: _boundary(), w=12, h=3, min_w=3,
              fit=True),
    ]
    return items


def render(data_root: Path) -> None:
    """Render without starting workers or opening user data."""
    st.header("Research lab / experimental workflows")
    st.warning(
        "EXPERIMENTAL ONLY — synthetic fixture results are not market data, "
        "financial truth, a forecast, investment advice or permission to trade."
    )
    st.caption(
        "Desktop research requires separately installed local research runtimes; "
        "this is not an all-in-one portable research installation."
    )
    job = st.session_state.get(_JOB_KEY)
    running = isinstance(job, _Job) and not job.done.is_set()
    if isinstance(job, _Job):
        _record(job)
    history = st.session_state.get(_HISTORY_KEY, [])
    cooling = isinstance(job, _Job) and job.done.is_set() and time.monotonic() - job.finished_at < _COOLDOWN
    grid("lab", panels(job, running, cooling, history))
