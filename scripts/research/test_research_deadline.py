"""Deterministic real Windows supervisor stalls, never ML/provider executions."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from edge import research_workers as workers

ROOT = Path(__file__).resolve().parents[2]


def _inject(monkeypatch, code: str) -> None:
    bootstrap = (
        "import sys,time;from pathlib import Path;"
        f"sys.path.insert(0,{str(ROOT / 'src')!r});"
        "from edge import research_workers as w;"
        + code + ";w._verification_child_main()"
    )
    monkeypatch.setattr(
        workers, "_verification_argv",
        lambda _: (workers._desktop_python(), "-I", "-B", "-c", bootstrap),
    )


def _call(timeout=1, cancel_event=None):
    return workers.run_research_worker(
        project_root=ROOT, framework="qlib",
        request={"schema_version": 1, "framework": "qlib", "synthetic": True},
        timeout_seconds=timeout, max_response_bytes=1024, cancel_event=cancel_event,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
@pytest.mark.parametrize("stage", [
    "verification", "nested-launch", "nested-assignment", "worker",
])
def test_real_supervisor_stalls_share_entry_budget(monkeypatch, tmp_path, stage):
    marker = tmp_path / "first-worker-instruction"
    if stage == "verification":
        code = (
            "w._verify_attestation=lambda *a:time.sleep(4);"
            f"w._run_research_worker_local=lambda **k:Path({str(marker)!r}).write_text('bad')"
        )
    elif stage in {"nested-launch", "nested-assignment"}:
        inner_script = f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')"
        patch = (
            "original=j.subprocess.Popen;"
            "j.subprocess.Popen=lambda *a,**k:(time.sleep(4),original(*a,**k))[1]"
            if stage == "nested-launch" else
            "original=j.ServerJob.assign;"
            "j.ServerJob.assign=lambda *a,**k:(time.sleep(4),original(*a,**k))[1]"
        )
        code = (
            "from edge import desktop_research_job as j;"
            "w._verify_attestation=lambda *a:None;" + patch + ";"
            "w._run_research_worker_local=lambda **k:j.run_job_worker("
            f"(sys.executable,'-I','-c',{inner_script!r}),"
            f"cwd=Path({str(ROOT)!r}),env=w._minimal_environment(),payload=b'',"
            "timeout_seconds=3,max_response_bytes=1024,deadline_monotonic=w._deadline.get())"
        )
    else:
        code = (
            "w._verify_attestation=lambda *a:None;"
            f"w._run_research_worker_local=lambda **k:(time.sleep(4),"
            f"Path({str(marker)!r}).write_text('bad'))[1]"
        )
    _inject(monkeypatch, code)
    start = time.monotonic()
    result = _call()
    elapsed = time.monotonic() - start
    assert result.status == "error", (result.error, result.diagnostics)
    assert "timeout" in result.error
    # Requested1s + helper shared2s allowance + test scheduling0.25s.
    assert elapsed < 3.25, (result.error, result.diagnostics)
    assert not marker.exists()
    assert result.response is None
    assert result.diagnostics["total_seconds"] >= 0
    assert result.diagnostics["phase_events"] and result.diagnostics["phase_events"][0]["phase"] == "verification", result.diagnostics


def test_already_cancelled_never_starts_verifier(monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(workers, "_verification_argv", lambda _: pytest.fail("launch"))
    result = _call(cancel_event=event)
    assert result.status == "error" and "cancelled" in result.error


def test_cancelled_readiness_never_starts_verifier(monkeypatch):
    event = threading.Event()
    event.set()
    monkeypatch.setattr(workers, "_verification_argv", lambda _: pytest.fail("launch"))
    result = workers.research_runtime_readiness(
        "qlib", project_root=ROOT, cancel_event=event,
    )
    assert result == ("Research API cancelled; no data accepted.",)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
@pytest.mark.parametrize("stage", ["verification", "worker"])
def test_cancel_during_owned_stages_kills_supervisor(monkeypatch, tmp_path, stage):
    ready = tmp_path / "verifying"
    marker = tmp_path / "worker"
    delayed = (
        f"(Path({str(ready)!r}).write_text('ready'),time.sleep(4),"
        f"Path({str(marker)!r}).write_text('bad'))[-1]"
    )
    code = (
        f"w._verify_attestation=lambda *a:{delayed};"
        f"w._run_research_worker_local=lambda **k:Path({str(marker)!r}).write_text('bad')"
        if stage == "verification" else
        f"w._verify_attestation=lambda *a:None;"
        f"w._run_research_worker_local=lambda **k:{delayed}"
    )
    _inject(monkeypatch, code)
    event = threading.Event()

    def cancel():
        end = time.monotonic() + 2
        while time.monotonic() < end and not ready.exists():
            time.sleep(0.01)
        event.set()

    thread = threading.Thread(target=cancel)
    thread.start()
    start = time.monotonic()
    result = _call(timeout=3, cancel_event=event)
    thread.join(timeout=2)
    assert result.status == "error" and "cancelled" in result.error
    assert time.monotonic() - start < 3.25, (result.error, result.diagnostics)
    assert ready.exists() and not marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
def test_readiness_missing_hash_blocks_without_canonical_execution(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    result = workers.research_runtime_readiness("qlib", project_root=ROOT, timeout_seconds=3)
    assert result == ("Research attestation unavailable or malformed.",)
    assert not (tmp_path / "EDGE" / "research" / "temp").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
def test_real_child_phase_timings_and_generic_envelope_unchanged(monkeypatch):
    _inject(monkeypatch, (
        "w._verify_attestation=lambda *a:None;"
        "w._run_research_worker_local=lambda **k:w.WorkerResult("
        "'ok',{'schema_version':1,'framework':'qlib','status':'ok'},None)"
    ))
    result = _call(timeout=3)
    assert result.status == "ok"
    assert "runtime" not in result.response
    assert result.diagnostics["verification_seconds"] >= 0
    assert result.diagnostics["worker_and_cleanup_seconds"] >= 0
    assert result.diagnostics["child_total_seconds"] >= result.diagnostics["verification_seconds"]


def test_chunked_hash_preserves_digest_and_deadline(tmp_path):
    path = tmp_path / "payload"
    payload = b"x" * 200_000
    path.write_bytes(payload)
    assert workers._sha256(path) == hashlib.sha256(payload).hexdigest()
    token = workers._deadline.set(time.monotonic() - 1)
    try:
        with pytest.raises(TimeoutError):
            workers._sha256(path)
    finally:
        workers._deadline.reset(token)


def test_invalid_public_request_does_not_launch(monkeypatch):
    monkeypatch.setattr(workers, "_bounded_child", lambda *a, **k: pytest.fail("launch"))
    with pytest.raises(ValueError):
        workers.run_research_worker(
            project_root=ROOT, framework="qlib", request={"schema_version": True},
            timeout_seconds=1, max_response_bytes=1024,
        )


def test_deadline_expired_before_exec(monkeypatch):
    monkeypatch.setattr(workers, "_verification_argv", lambda _: pytest.fail("launch"))
    result = workers._bounded_child(
        ROOT, "qlib", None, time.monotonic() - 1, 1024, None,
    )
    assert result.status == "error" and "timeout" in result.error


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
def test_blocking_hash_read_is_killed_not_only_checkpointed(monkeypatch):
    slow_stream = (
        "class SlowStream:\n"
        " def __enter__(self): return self\n"
        " def __exit__(self,*args): pass\n"
        " def read(self,size):\n"
        "  assert size == 65536\n"
        "  time.sleep(4)\n"
        "  return b''\n"
    )
    _inject(monkeypatch, (
        f"exec({slow_stream!r});"
        "w.Path.open=lambda *a,**k:SlowStream();"
        "w._verify_attestation=lambda *a:w._sha256(Path('blocked-hash'))"
    ))
    start = time.monotonic()
    result = _call()
    assert result.status == "error" and "timeout" in result.error
    assert time.monotonic() - start < 3.25, (result.error, result.diagnostics)
    assert result.diagnostics["phase_events"] and result.diagnostics["phase_events"][0]["phase"] == "verification", result.diagnostics


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
def test_cold_fixture_construction_is_inside_supervisor(monkeypatch):
    _inject(monkeypatch, (
        "from edge import portfolio_optimization as p;"
        "w._verify_attestation=lambda *a:None;"
        "p.skfolio_synthetic_request=lambda *a:time.sleep(4)"
    ))
    start = time.monotonic()
    result = workers.run_synthetic(
        "skfolio", "minimum-variance-three-assets", timeout_seconds=1,
    )
    assert result.status == "error" and "timeout" in result.error
    assert time.monotonic() - start < 3.25, (result.error, result.diagnostics)
    assert any(
        event["phase"] == "fixture-construction"
        for event in result.diagnostics["phase_events"]
    ), result.diagnostics


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership is required")
def test_stalled_supervisor_descendant_reaped(monkeypatch, tmp_path):
    pid_file = tmp_path / "pid"
    code = (
        "import subprocess;"
        "w._verify_attestation=lambda *a:None;"
        "w._run_research_worker_local=lambda **k:("
        "subprocess.Popen([sys.executable,'-I','-c',"
        "'import time;time.sleep(30)']),time.sleep(4))"
    )
    # Preserve the exact launched PID for independent process liveness check.
    code = code.replace(
        "subprocess.Popen([sys.executable",
        f"(lambda p:(Path({str(pid_file)!r}).write_text(str(p.pid)),p)[1])("
        "subprocess.Popen([sys.executable",
    ).replace("'import time;time.sleep(30)']),", "'import time;time.sleep(30)'])),")
    _inject(monkeypatch, code)
    result = _call(timeout=2)
    assert result.status == "error" and "timeout" in result.error
    assert pid_file.exists()
    pid = int(pid_file.read_text())
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    handle = api.OpenProcess(0x1000 | 0x00100000, False, pid)
    if handle:
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        try:
            assert api.WaitForSingleObject(handle, 0) == 0
        finally:
            api.CloseHandle(handle)


def test_supervisor_payload_excludes_secrets(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    monkeypatch.setenv("TEST_PROVIDER_KEY", "private")
    job = Mock(return_value=SimpleNamespace(
        status="succeeded", stdout=json.dumps({
            "status": "blocked", "response": None, "error": "blocked",
            "diagnostics": {},
        }).encode(),
    ))
    monkeypatch.setattr("edge.desktop_research_job.run_job_worker", job)
    _call()
    assert "TEST_PROVIDER_KEY" not in job.call_args.kwargs["env"]
    assert b"private" not in job.call_args.kwargs["payload"]
    assert job.call_args.kwargs["deadline_monotonic"] > time.monotonic()
