"""Diagnostic root isolation and fixed native UI driver tests; no real profiles."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from edge import desktop, desktop_paths
from edge.desktop_research_check import (
    _click,
    _contains,
    _wait,
    exercise_research_window,
)


def test_diagnostic_root_requires_frozen_owned_temp_runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_paths, "_desktop_data_root", None)
    monkeypatch.setattr(desktop_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    owned = tmp_path / "EDGE-research-check-unit"
    runtime = owned / "runtime" / "session-test"
    runtime.mkdir(parents=True)
    monkeypatch.chdir(runtime)
    before = os.environ.get("LOCALAPPDATA")
    assert (
        desktop_paths.activate_diagnostic_data_root(owned)
        == owned / "application" / "data"
    )
    assert os.environ.get("LOCALAPPDATA") == before
    assert not (owned / "application" / "data").exists()
    with pytest.raises(RuntimeError, match="Invalid"):
        desktop_paths.activate_diagnostic_data_root(tmp_path / "arbitrary")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeError, match="Invalid"):
        desktop_paths.activate_diagnostic_data_root(owned)
    monkeypatch.setattr(desktop_paths.sys, "frozen", False)
    with pytest.raises(RuntimeError, match="Invalid"):
        desktop_paths.activate_diagnostic_data_root(owned)


def test_diagnostic_dispatch_never_accepts_application_path(monkeypatch, tmp_path):
    run = Mock(return_value=0)
    monkeypatch.setattr(desktop, "run_window", run)
    report = tmp_path / "report.json"
    assert desktop.main(["--desktop-research-check", str(report)]) == 0
    run.assert_called_once_with(reduced_motion=True, research_report=report)
    with pytest.raises(SystemExit):
        desktop.main(["--desktop-data-root", str(tmp_path)])


def test_ui_control_unavailable_fails_without_worker_fallback(monkeypatch):
    import edge.desktop_research_check as check

    clock = iter((0, 0, 11))
    monkeypatch.setattr(check.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(check.time, "sleep", lambda _: None)
    window = Mock()
    window.evaluate_js.return_value = False
    with pytest.raises(RuntimeError, match="control"):
        _click(window, "Run selected synthetic experiment")
    assert "disabled" in window.evaluate_js.call_args.args[0]


def test_control_waits_for_real_react_remount(monkeypatch):
    import edge.desktop_research_check as check

    monkeypatch.setattr(check.time, "sleep", lambda _: None)
    window = Mock()
    window.evaluate_js.side_effect = (False, True)
    _click(window, "Run selected synthetic experiment")
    assert window.evaluate_js.call_count == 2


def test_native_uppercase_heading_preserves_exact_semantic_marker():
    assert _contains("COMPANY / WATCHLIST WORKSPACE", "Company / watchlist workspace")
    assert _contains(
        "RESEARCH LAB / EXPERIMENTAL WORKFLOWS", "Research lab / experimental workflows"
    )
    assert not _contains(
        "Active: Research / Watchlist", "Company / watchlist workspace"
    )


def test_ui_wait_is_bounded():
    window = Mock()
    with pytest.raises(TimeoutError):
        _wait(window, lambda _: False, 0)
    window.evaluate_js.assert_not_called()


def test_failed_native_check_closes_and_reports_no_success(tmp_path):
    window = Mock()
    window.evaluate_js.side_effect = RuntimeError("untrusted detail")
    report = tmp_path / "report.json"
    exercise_research_window(window, report, 123)
    data = json.loads(report.read_text())
    assert data["ok"] is False
    assert data["engines"] == []
    assert data["error_type"] == "RuntimeError"
    assert "untrusted detail" not in report.read_text()
    window.destroy.assert_called_once()


def test_owned_server_close_awaits_all_members():
    server = desktop.OwnedServer(Path("not-created"))
    job = Mock()
    server.job = job
    server.process = Mock()
    server.process.poll.return_value = 0
    server.close()
    job.terminate_and_wait.assert_called_once()
    assert server.job is None
