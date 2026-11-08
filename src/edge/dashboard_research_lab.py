"""Explicit, session-local synthetic research experiments."""

import re
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import streamlit as st

from edge.dashboard_manual import readout

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


def _show_result(job: _Job) -> None:
    if job.status != "ok":
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
        return
    response = _accepted_result(job)
    if response is None:
        st.error("Experiment response failed identity or provenance checks; no result accepted.")
        return
    result = response["result"]
    st.success("Synthetic experiment completed. This is not financial evidence or a trading signal.")
    st.text(f"Engine: {job.framework} / fixed fixture: {job.fixture}")
    runtime = response["runtime"]
    st.text(f"Runtime: Python {runtime['python_version']} / {runtime['primary_package']} {runtime['primary_version']}")
    st.text(f"Worker SHA-256: {runtime['worker_sha256']}")
    st.text(f"Request SHA-256: {response['request_sha256']}")
    st.caption("Local hash consistency is not a signed security attestation.")
    if job.framework == "skfolio":
        st.text(f"Engine version: {result['package_version']}")
        st.text(f"Input panel SHA-256: {result['panel_digest']}")
        st.caption("Fixed synthetic panel; worker result is not a proposed trade.")
        for key in ("solver_status", "weights", "estimated_period_variance",
                    "estimated_net_period_return", "holdout", "naive_holdout", "limitations"):
            if key in result:
                st.text(f"{key}: {result[key]}")
    else:
        st.text(f"Engine version: {result['engine_version']}")
        st.text(f"Fixture SHA-256: {result['fixture_sha256']}")
        readout(result)
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

    job = st.session_state.get(_JOB_KEY)
    running = isinstance(job, _Job) and not job.done.is_set()
    cooling = isinstance(job, _Job) and job.done.is_set() and time.monotonic() - job.finished_at < _COOLDOWN
    if st.button("Run selected synthetic experiment", disabled=running or cooling, type="primary"):
        job = _Job(framework, fixture)
        st.session_state[_JOB_KEY] = job
        threading.Thread(target=_run, args=(job,), daemon=True).start()
        st.rerun()
    if running:
        _running(job)
    elif isinstance(job, _Job):
        _show_result(job)
        if cooling:
            _wait_cooldown(job)
    st.subheader("Real-data boundary")
    st.caption(
        "Real-market experiments remain blocked pending independently accepted "
        "data, identity, rights, corporate-action and point-in-time evidence. "
        "A user declaration does not replace those checks."
    )
