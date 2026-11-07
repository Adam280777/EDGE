"""Desktop lifecycle/security boundary tests, no native window or provider calls."""

import json
import tomllib
from pathlib import Path
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
    job.close.assert_called_once()
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
