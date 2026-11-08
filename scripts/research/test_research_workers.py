"""Offline transport validation only; actual frameworks have separate smoke evidence."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge import research_workers as workers


@pytest.fixture
def transport(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("TEST_API_SECRET", "never-forward")
    root = tmp_path / "project"
    resources = root / "scripts" / "research"
    resources.mkdir(parents=True)
    (resources / "qlib_worker.py").write_text("# fixture")
    executable = tmp_path / "local" / "EDGE" / "research" / "envs" / "qlib" / "Scripts" / "python.exe"
    executable.parent.mkdir(parents=True)
    executable.write_text("fixture executable never launched")
    monkeypatch.setattr(workers, "_verify_attestation", lambda *_: None)
    job = Mock(return_value=SimpleNamespace(
        status="succeeded", returncode=0, stderr=b"",
        stdout=b'{"schema_version":1,"framework":"qlib","status":"ok","result":{}}',
    ))
    monkeypatch.setattr("edge.desktop_research_job.run_job_worker", job)
    return root, job


def call(root, **changes):
    kwargs = {
        "project_root": root, "framework": "qlib",
        "request": {"schema_version": 1, "framework": "qlib", "synthetic": True},
        "timeout_seconds": 10, "max_response_bytes": 1024,
    }
    kwargs.update(changes)
    return workers._run_research_worker_local(**kwargs)


def test_fixed_job_argv_minimal_environment_and_cancel(transport):
    root, job = transport
    event = threading.Event()
    result = call(root, cancel_event=event)
    assert result.status == "ok"
    argv = job.call_args.args[0]
    assert argv[1:3] == ("-I", "-B")
    assert Path(argv[0]).parent.parent.name == "qlib"
    assert "TEST_API_SECRET" not in job.call_args.kwargs["env"]
    assert "PYTHONPATH" not in job.call_args.kwargs["env"]
    assert job.call_args.kwargs["cancel_event"] is event
    assert len(result.response["request_sha256"]) == 64


@pytest.mark.parametrize("field,value", [
    ("timeout_seconds", True), ("timeout_seconds", 0),
    ("max_response_bytes", True), ("max_response_bytes", 1023),
    ("framework", "unknown"),
    ("request", {"schema_version": True, "framework": "qlib"}),
    ("request", {"schema_version": 1, "framework": "kronos"}),
    ("request", {"schema_version": 1, "framework": "qlib", "value": float("nan")}),
])
def test_invalid_request_never_launches(transport, field, value):
    root, job = transport
    with pytest.raises(ValueError):
        call(root, **{field: value})
    job.assert_not_called()


@pytest.mark.parametrize("output", [
    b'{"schema_version":1,"schema_version":1,"framework":"qlib","status":"ok"}',
    b'{"schema_version":true,"framework":"qlib","status":"ok"}',
    b'{"schema_version":1,"framework":"qlib","status":"accepted-real-data"}',
    b'{"schema_version":1,"framework":"qlib","status":"ok","value":NaN}',
    b'{"schema_version":1,"framework":"qlib","status":"ok","request_sha256":"wrong"}',
    b"not JSON",
])
def test_invalid_envelope_fails_closed(transport, output):
    root, job = transport
    job.return_value.stdout = output
    assert call(root).status == "error"


@pytest.mark.parametrize("status", ["cancelled", "timeout", "output-limit", "failed"])
def test_job_failure_never_accepts_response(transport, status):
    root, job = transport
    job.return_value.status = status
    result = call(root)
    assert result.status == "error"
    assert result.response is None


def test_missing_attestation_readiness_owned_verifier_only(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    job = Mock(return_value=SimpleNamespace(
        status="succeeded", stdout=json.dumps({
            "status": "blocked", "response": None,
            "error": "Research attestation unavailable or malformed.",
            "diagnostics": {},
        }).encode(),
    ))
    monkeypatch.setattr("edge.desktop_research_job.run_job_worker", job)
    assert workers.research_runtime_readiness("qlib", project_root=tmp_path)
    job.assert_called_once()
    assert "-c" in job.call_args.args[0]
    assert json.loads(job.call_args.kwargs["payload"])["request"] is None


def test_frozen_override_and_unlisted_fixture_block_without_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(workers.sys, "frozen", True, raising=False)
    result = workers.run_synthetic("qlib", "qlib-linear-three-assets-v1", project_root=tmp_path)
    assert result.status == "blocked"
    with pytest.raises(ValueError):
        workers.run_synthetic("kronos", "user-code")


def test_unique_json_object():
    with pytest.raises(ValueError):
        json.loads('{"x":1,"x":2}', object_pairs_hook=workers._unique_object)
