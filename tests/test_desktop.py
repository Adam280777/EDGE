"""Desktop lifecycle/security boundary tests, no native window or provider calls."""

import ast
import json
import os
import socket
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge import desktop, desktop_paths
from edge.desktop_webview import (
    _guard_download_starting,
    allowed_download,
    allowed_resource,
    configure_window_settings,
)


def test_source_data_default_ignores_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop_paths, "_desktop_data_root", None)
    monkeypatch.setenv("EDGE_DATA_ROOT", str(tmp_path / "untrusted"))
    assert desktop_paths.app_data_root(tmp_path / "data") == tmp_path / "data"
    with pytest.raises(RuntimeError, match="bundled"):
        desktop_paths.activate_packaged_data_root()


@pytest.mark.parametrize("url,allowed", [
    ("http://127.0.0.1:12345/capability/", True),
    ("ws://127.0.0.1:12345/capability/_stcore/stream", True),
    ("http://127.0.0.1:8501/", False),
    ("https://example.com/telemetry", False),
    ("file:///C:/Users/data", False),
    ("data:image/png;base64,abc", True),
])
def test_native_renderer_blocks_remote_resources(url, allowed):
    assert allowed_resource(url, "http://127.0.0.1:12345/capability/") is allowed


@pytest.mark.parametrize(
    "url,allowed",
    [
        ("http://127.0.0.1:12345/capability/_stcore/media/download.json", True),
        ("blob:http://127.0.0.1:12345/7d7a", True),
        ("http://127.0.0.1:8501/_stcore/media/download.json", False),
        ("https://example.com/statement.pdf", False),
        ("blob:https://example.com/statement.pdf", False),
        ("file:///C:/Users/data/export.json", False),
        ("data:application/json,{}", False),
        ("http://user@127.0.0.1:12345/export.json", False),
        ("http://127.0.0.1:bad/export.json", False),
    ],
)
def test_native_downloads_are_limited_to_owned_local_origin(url, allowed):
    assert allowed_download(url, "http://127.0.0.1:12345/capability/") is allowed


def test_download_guard_cancels_remote_sources_before_save_dialog():
    calls = []

    def save_dialog(_self, _sender, _args):
        calls.append("save dialog")

    guarded = _guard_download_starting(
        save_dialog, "http://127.0.0.1:12345/capability/"
    )

    class Operation:
        Uri = "https://example.com/file.json"

    class Args:
        DownloadOperation = Operation()
        Cancel = False

    args = Args()
    guarded(object(), None, args)
    assert args.Cancel is True
    assert calls == []

    args.DownloadOperation.Uri = (
        "http://127.0.0.1:12345/capability/_stcore/media/export.json"
    )
    args.Cancel = False
    guarded(object(), None, args)
    assert args.Cancel is False
    assert calls == ["save dialog"]


def test_native_window_keeps_local_navigation_and_user_confirmed_downloads():
    settings = {}
    configure_window_settings(settings)
    assert settings == {
        "OPEN_EXTERNAL_LINKS_IN_BROWSER": False,
        "ALLOW_DOWNLOADS": True,
        "ALLOW_FILE_URLS": False,
        "REMOTE_DEBUGGING_PORT": None,
    }


def test_project_exposes_cli_but_not_unsupported_desktop_launcher():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    scripts = project["project"]["scripts"]
    assert scripts["edge"] == "edge.cli:main"
    assert "edge-desktop" not in scripts
    spec = Path("EDGE.spec").read_text(encoding="utf-8")
    assert '"scripts"' in spec and '"desktop_entry.py"' in spec


def test_packaged_data_is_per_user_and_not_created(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(desktop_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop_paths, "_desktop_data_root", None)
    assert desktop_paths.activate_packaged_data_root() == tmp_path / "EDGE" / "data"
    assert desktop_paths.app_data_root(Path("wrong")) == tmp_path / "EDGE" / "data"
    assert not (tmp_path / "EDGE").exists()


def test_missing_local_appdata_fails_closed(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    with pytest.raises(RuntimeError, match="LOCALAPPDATA"):
        desktop_paths.local_app_root()


def test_server_flags_are_loopback_no_telemetry_and_no_file_watcher():
    flags = desktop.streamlit_flags("nonce")
    assert flags["server.address"] == "127.0.0.1"
    assert flags["server.port"] == 0
    assert flags["server.baseUrlPath"] == "nonce"
    assert flags["browser.gatherUsageStats"] is False
    assert flags["server.enableCORS"] is True
    assert flags["server.enableXsrfProtection"] is True
    assert flags["server.enableStaticServing"] is False
    assert flags["server.fileWatcherType"] == "none"


def test_no_virtualenv_launcher():
    with pytest.raises(RuntimeError, match="never launches a virtualenv"):
        desktop._bundled_app()


def test_frozen_runner_is_generated_only_in_private_child_cwd(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, "frozen", True, raising=False)
    monkeypatch.setattr(desktop.importlib.util, "find_spec", lambda name: object())
    monkeypatch.chdir(tmp_path)
    runner = tmp_path / "desktop_app.py"
    assert desktop._bundled_app() == runner
    assert not runner.exists()
    assert desktop._bundled_app(create_runner=True) == runner
    assert runner.read_text() == "from desktop_dashboard import main\nmain()\n"


def test_private_server_mode_rejects_untrusted_invocation(monkeypatch):
    monkeypatch.delenv(desktop.BOOT_ENV, raising=False)
    assert desktop._internal_server() == 2


def test_ready_requires_live_owned_child(tmp_path):
    server = desktop.OwnedServer(tmp_path)
    with pytest.raises(RuntimeError, match="exited"):
        server.poll_ready()
    server.close()
    server.close()


def test_ready_rejects_foreign_identity(tmp_path):
    server = desktop.OwnedServer(tmp_path)
    server.process = Mock()
    server.process.poll.return_value = None
    server.channel = Mock()
    server.channel.recv.return_value = b"wrong-identity\n"
    with pytest.raises(RuntimeError, match="identity"):
        server.poll_ready()


def test_ready_requires_bounded_handshake(tmp_path):
    server = desktop.OwnedServer(tmp_path)
    server.process = Mock()
    server.process.poll.return_value = None
    server.channel = Mock()
    server.channel.recv.return_value = b"x" * 4097
    with pytest.raises(RuntimeError, match="Invalid"):
        server.poll_ready()


def test_own_job_closed_and_only_own_process_waited(tmp_path):
    server = desktop.OwnedServer(tmp_path)
    server.process = Mock()
    server.process.poll.return_value = None
    job = server.job = Mock()
    server.close()
    job.terminate_and_wait.assert_called_once()
    server.process.wait.assert_called_once_with(timeout=8)
    server.process.kill.assert_not_called()


def test_smoke_report_contains_only_explicit_lifecycle_facts(tmp_path):
    report = tmp_path / "smoke.json"
    desktop._write_status(report, health=True, native_window=True)
    assert json.loads(report.read_text()) == {"health": True, "native_window": True}


@pytest.mark.skipif(desktop.os.name != "nt", reason="Windows kernel Job Object")
def test_real_windows_job_kills_only_assigned_child():
    import subprocess
    import sys

    from edge.desktop_job import ServerJob

    owned = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    job = ServerJob()
    try:
        job.assign(int(owned._handle))
        job.close()
        owned.wait(timeout=10)
        assert unrelated.poll() is None
    finally:
        job.close()
        for process in (owned, unrelated):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def test_manual_desktop_self_test_uses_actual_services_and_all_bundle(tmp_path):
    facts = desktop._manual_self_test(tmp_path / "synthetic-application")
    assert facts == {
        "portfolio_fifo": True,
        "portfolio_valuation": True,
        "missing_observations_incomplete": True,
        "decision_persistence": True,
        "manual_monitoring": True,
        "alert_deduplication": True,
        "alert_acknowledgement": True,
        "application_bundle_roundtrip": True,
        "registered_stores_and_settings": True,
        "manual_csv_import": True,
        "explicit_risk_sizing": True,
        "unconfirmed_risk_blocked": True,
    }
    assert not (tmp_path / "synthetic-application" / "simulation").exists()


def test_desktop_self_test_sandboxes_profile_network_and_restores_process_state(
    tmp_path, monkeypatch,
):
    from edge import simulation_service
    from edge.data import openbb_sec_provider

    original_connect = socket.socket.connect
    original_dns = socket.getaddrinfo
    original_profiles = {
        key: os.environ.get(key) for key in (
            "HOME", "USERPROFILE", "LOCALAPPDATA", "APPDATA",
            "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
        )
    }
    observed_profiles = []

    def engine():
        profile = Path(os.environ["USERPROFILE"])
        observed_profiles.append(profile)
        assert profile.name == "synthetic-profile"
        assert profile.is_dir()
        assert all(os.environ[key] == str(profile) for key in original_profiles)
        with pytest.raises(OSError, match="external DNS"):
            socket.getaddrinfo("example.test", 443)
        with socket.socket() as sock, pytest.raises(OSError, match="external requests"):
            sock.connect(("192.0.2.1", 443))
        return "synthetic-engine-import-fixture"

    monkeypatch.setattr(simulation_service, "engine_version", engine)
    monkeypatch.setattr(simulation_service, "run", lambda *args: SimpleNamespace(ok=True))
    monkeypatch.setattr(openbb_sec_provider, "_load_openbb", lambda: {"sec": object()})
    report = tmp_path / "diagnostic.json"
    assert desktop.self_test(report) == 0
    result = json.loads(report.read_text())
    assert result["fixture_classification"] == "synthetic-not-market-facts"
    assert result["user_profile_access"] is False
    assert result["external_requests"] is False
    assert result["real_vault_access"] is False
    assert result["clipboard_access"] is False
    assert result["application_bundle_roundtrip"] is True
    assert socket.socket.connect is original_connect
    assert socket.getaddrinfo is original_dns
    assert {key: os.environ.get(key) for key in original_profiles} == original_profiles
    assert observed_profiles and not observed_profiles[0].exists()


def test_failed_desktop_self_test_restores_network_and_profiles(tmp_path, monkeypatch):
    from edge import simulation_service

    original_connect = socket.socket.connect
    original_dns = socket.getaddrinfo
    profile = os.environ.get("USERPROFILE")

    def fail():
        raise RuntimeError("sensitive synthetic failure detail")

    monkeypatch.setattr(simulation_service, "engine_version", fail)
    report = tmp_path / "failure.json"
    assert desktop.self_test(report) == 1
    result = json.loads(report.read_text())
    assert result["stage"] == "engine doctor"
    assert result["error_type"] == "RuntimeError"
    assert "sensitive" not in report.read_text()
    assert socket.socket.connect is original_connect
    assert socket.getaddrinfo is original_dns
    assert os.environ.get("USERPROFILE") == profile


def test_frozen_smoke_routes_match_current_app_displayed_api():
    """Parse constants without importing Streamlit UI or touching user state."""
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                    "WORKSPACES", "WORKSPACE_LABELS",
                ):
                    values[target.id] = ast.literal_eval(node.value)
    expected = tuple(
        values["WORKSPACE_LABELS"][name] for name in values["WORKSPACES"]
    )
    assert tuple(label for label, marker in desktop.SMOKE_WORKSPACES) == expected
    assert len(expected) == 12
    assert all(marker for label, marker in desktop.SMOKE_WORKSPACES)


def test_freeze_explicitly_includes_manual_callbacks_and_service_modules():
    tree = ast.parse(Path("EDGE.spec").read_text(encoding="utf-8"))
    hidden = next(
        ast.literal_eval(node.value)
        for node in tree.body if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "hiddenimports"
                for target in node.targets)
    )
    required = {
        "edge.portfolio", "edge.portfolio_store", "edge.decision_store",
        "edge.decision_contracts", "edge.monitoring", "edge.monitoring_store",
        "edge.application_backup", "edge.application_storage_lock",
        "edge.native_dialogs_manual", "edge.dashboard_manual_csv",
        "edge.dashboard_application_backup", "edge.dashboard_portfolio",
        "edge.dashboard_decisions", "edge.dashboard_discovery",
        "edge.dashboard_monitoring", "edge.data.manual_observations",
        "edge.data.research_readiness",
    }
    assert required <= set(hidden)


def test_native_renderer_failure_stops_owned_watch_thread_and_server(
    tmp_path, monkeypatch,
):
    from edge import desktop_splash, desktop_webview

    real_thread = desktop.threading.Thread
    watchers = []
    server = Mock(url="http://127.0.0.1:12345/capability/")
    server.poll_ready.return_value = True
    server.process.poll.return_value = None
    splash = Mock()

    class ClosedEvent:
        def __iadd__(self, callback):
            self.callback = callback
            return self

    window = SimpleNamespace(events=SimpleNamespace(closed=ClosedEvent()))

    def thread_factory(*args, **kwargs):
        thread = real_thread(*args, **kwargs)
        watchers.append(thread)
        return thread

    def renderer_failure(*args, **kwargs):
        raise RuntimeError("synthetic renderer failure")

    monkeypatch.setattr(desktop, "webview2_version", lambda: "100.0")
    monkeypatch.setattr(desktop, "_bundled_app", lambda: tmp_path / "synthetic.py")
    monkeypatch.setattr(desktop, "local_app_root", lambda: tmp_path)
    monkeypatch.setattr(desktop, "OwnedServer", lambda root: server)
    monkeypatch.setattr(desktop.threading, "Thread", thread_factory)
    monkeypatch.setattr(desktop, "_message", lambda message: None)
    monkeypatch.setattr(desktop_splash, "DesktopSplash", lambda **kwargs: splash)
    monkeypatch.setattr(desktop_webview, "configure_renderer", lambda url: None)
    monkeypatch.setattr(desktop_webview, "configure_window_settings", lambda settings: None)
    monkeypatch.setitem(sys.modules, "webview", SimpleNamespace(
        settings={}, create_window=lambda *args, **kwargs: window,
        start=renderer_failure,
    ))
    assert desktop.run_window() == 2
    assert watchers and all(not thread.is_alive() for thread in watchers)
    server.close.assert_called_once()
    assert list((tmp_path / "runtime").iterdir()) == []
