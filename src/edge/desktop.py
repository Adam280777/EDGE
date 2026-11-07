"""Frozen Windows runtime: owned Streamlit child and a native WebView2 window.

No browser launcher, Python installation, provider request or live execution is
part of startup. Private boot IPC is inherited only by the owned child; readiness
never attaches to an existing server. Runtime diagnostics contain no request data.
"""

from __future__ import annotations

import argparse
import hmac
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from edge.desktop_paths import activate_packaged_data_root, local_app_root

BOOT_ENV = "_EDGE_DESKTOP_BOOT"
STARTUP_TIMEOUT = 75.0
WEBVIEW2_CLIENT = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def webview2_version() -> str | None:
    """Check installed Evergreen Runtime only; never download or install anything."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(
                    hive, rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT}",
                    0, winreg.KEY_READ | view,
                ) as key:
                    value, _ = winreg.QueryValueEx(key, "pv")
                    if (
                        isinstance(value, str) and value.split(".")[0].isdigit()
                        and int(value.split(".")[0]) >= 100
                    ):
                        return value
            except OSError:
                continue
    return None


def streamlit_flags(base_path: str) -> dict[str, Any]:
    """Explicit flags override even a caller's unrelated working-directory config."""
    return {
        "server.address": "127.0.0.1",
        "server.port": 0,
        "server.baseUrlPath": base_path,
        "server.headless": True,
        "server.fileWatcherType": "none",
        "server.runOnSave": False,
        "server.enableCORS": True,
        "server.enableXsrfProtection": True,
        "server.enableStaticServing": False,
        "server.showEmailPrompt": False,
        "browser.gatherUsageStats": False,
        "browser.serverAddress": "127.0.0.1",
        "global.developmentMode": False,
        "logger.level": "error",
        "theme.base": "dark",
        "theme.primaryColor": "#ffb000",
        "theme.backgroundColor": "#050505",
        "theme.secondaryBackgroundColor": "#10100d",
        "theme.textColor": "#ffc15a",
        "theme.font": "monospace",
    }


def _bundled_app(*, create_runner: bool = False) -> Path:
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Build dist\\EDGE first. Desktop mode never launches a virtualenv.")
    if importlib.util.find_spec("desktop_dashboard") is None:
        raise RuntimeError("Compiled dashboard is missing; rebuild the complete EDGE folder.")
    # Streamlit requires a readable script. Generate only this two-line runner in
    # the child's private working directory; no raw Python source ships in dist.
    app = Path.cwd() / "desktop_app.py"
    if create_runner:
        app.write_text("from desktop_dashboard import main\nmain()\n", encoding="utf-8")
    return app


def _internal_server() -> int:
    """Private re-entry mode uses the bundled Streamlit Python API, never -m."""
    raw = os.environ.pop(BOOT_ENV, None)
    if not raw or not getattr(sys, "frozen", False):
        return 2
    # Windowed PyInstaller sets these streams to None, which third-party logging
    # cannot always handle. Discard rather than persisting request/credential logs.
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115 -- process-lifetime stream
    sys.stderr = sys.stdout
    try:
        boot = json.loads(raw)
        token = boot["token"]
        port = boot["port"]
        if not isinstance(token, str) or len(token) != 64:
            return 2
        if not isinstance(port, int) or not 1 <= port <= 65535:
            return 2
        with socket.create_connection(("127.0.0.1", port), timeout=10) as channel:
            channel.sendall((token + "\n").encode("ascii"))
            # Parent acknowledges only after assignment to its kill-on-close job.
            if channel.recv(1) != b"1":
                return 2
            activate_packaged_data_root()
            from streamlit import config
            from streamlit.web import bootstrap

            flags = streamlit_flags(token)
            bootstrap.load_config_options(flag_options=flags)

            def started(_server: Any) -> None:
                actual_port = int(config.get_option("server.port"))
                message = json.dumps({"port": actual_port, "token": token}) + "\n"
                channel.sendall(message.encode("ascii"))

            # Pinned 1.66.1 bootstrap hook: called after the socket has bound and
            # runtime started, before browser opening / incidental network work.
            bootstrap._on_server_start = started
            bootstrap.run(str(_bundled_app(create_runner=True)), False, [], flags)
        return 0
    except Exception:  # noqa: BLE001 -- fixed private-process exit, never leak secrets
        # A fixed error class, never exception strings/credentials, crosses IPC.
        return 3


class OwnedServer:
    """Per-launch server and handshake; bounded startup, no external attachment."""

    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.process: subprocess.Popen[bytes] | None = None
        self.job: Any = None
        self.url: str | None = None
        self.token = secrets.token_hex(32)
        self.listener: socket.socket | None = None
        self.channel: socket.socket | None = None
        self._buffer = b""
        self._authenticated = False

    def start(self) -> None:
        from edge.desktop_job import ServerJob

        _bundled_app()
        self.job = ServerJob()
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.02)
        env = os.environ.copy()
        # No shell/PYTHONPATH/.venv dependence. Do not retain boot capability in app.
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
        env[BOOT_ENV] = json.dumps({
            "token": self.token, "port": self.listener.getsockname()[1],
        })
        env["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
        env["STREAMLIT_SERVER_HEADLESS"] = "true"
        self.process = subprocess.Popen(
            [sys.executable, "--internal-server"],
            cwd=self.runtime_dir, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            self.job.assign(int(self.process._handle))
        except Exception:
            self.process.terminate()
            self.process.wait(timeout=10)
            raise

    def poll_ready(self) -> bool:
        if self.process is None or self.process.poll() is not None:
            raise RuntimeError("Bundled server exited before readiness. Rebuild or run desktop self-test.")
        if self.url:
            return True
        if self.channel is None:
            try:
                self.channel, _ = self.listener.accept()
                self.channel.settimeout(0.02)
            except TimeoutError:
                return False
        try:
            block = self.channel.recv(4096)
        except TimeoutError:
            return False
        if not block:
            raise RuntimeError("Bundled server startup handshake closed unexpectedly.")
        self._buffer += block
        if len(self._buffer) > 4096:
            raise RuntimeError("Invalid desktop startup handshake.")
        if b"\n" not in self._buffer:
            return False
        line, self._buffer = self._buffer.split(b"\n", 1)
        if not self._authenticated:
            if not hmac.compare_digest(line.decode("ascii"), self.token):
                raise RuntimeError("Desktop server identity check failed.")
            self._authenticated = True
            self.channel.sendall(b"1")
            return False
        payload = json.loads(line)
        if not hmac.compare_digest(payload.get("token", ""), self.token):
            raise RuntimeError("Desktop server identity check failed.")
        port = payload.get("port")
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise RuntimeError("Invalid bundled server port.")
        url = f"http://127.0.0.1:{port}/{self.token}/"
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url + "_stcore/health", timeout=2) as response:
            if response.status != 200 or response.read(64) != b"ok":
                raise RuntimeError("Bundled server health check failed.")
        self.url = url
        return True

    def close(self) -> None:
        # Closing the job is kernel-enforced even if this process crashes.
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()  # specific owned handle only, never a name/port
                self.process.wait(timeout=5)
        for sock in (self.channel, self.listener):
            if sock is not None:
                sock.close()
        self.channel = self.listener = None


def _message(text: str) -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, text, "EDGE — desktop startup", 0x10)


def _write_status(path: Path, **values: Any) -> None:
    """Smoke-test opt-in only: fixed lifecycle facts, no URLs/tokens/financial data."""
    path.write_text(json.dumps(values, indent=2), encoding="utf-8")


def run_window(*, reduced_motion: bool = False, smoke_report: Path | None = None) -> int:
    """Show splash only during real readiness, then hand the main thread to WebView2."""
    from edge.desktop_splash import DesktopSplash

    if webview2_version() is None:
        if smoke_report:
            _write_status(smoke_report, error_type="MissingWebView2")
            return 2
        _message(
            "Microsoft Edge WebView2 Runtime (Evergreen, version 100+) is required.\n"
            "Install it from Microsoft's official WebView2 download page, then retry.\n"
            "EDGE has not downloaded or installed anything."
        )
        return 2
    _bundled_app()
    runtime_root = local_app_root() / "runtime"
    runtime_root.mkdir(parents=True, exist_ok=True)
    runtime_dir = Path(tempfile.mkdtemp(prefix="session-", dir=runtime_root))
    server = OwnedServer(runtime_dir)
    splash = None
    cancelled = threading.Event()
    stage = "splash"
    try:
        splash = DesktopSplash(
            on_cancel=cancelled.set, reduced_motion=True if reduced_motion else None
        )
        splash.set_stage("Starting bundled local research server")
        splash.pump()
        stage = "starting server"
        server.start()
        deadline = time.monotonic() + STARTUP_TIMEOUT
        splash.set_stage("Waiting for owned server readiness")
        while not server.poll_ready():
            if not splash.pump() or cancelled.is_set():
                return 1
            if time.monotonic() >= deadline:
                raise TimeoutError("Local server did not become ready within 75 seconds.")
            time.sleep(0.025)
        if cancelled.is_set():
            return 1
        splash.set_stage("Local server ready — opening research window")
        splash.close()  # no minimum animation time, no cosmetic delay
        stage = "native renderer"
        import webview

        from edge.desktop_webview import configure_renderer, configure_window_settings

        configure_renderer(server.url)
        # pywebview routes accepted downloads through a native Save As dialog.
        # Its default overwrite prompt remains enabled; remote sources are
        # cancelled by the renderer's DownloadStarting guard.
        configure_window_settings(webview.settings)
        window = webview.create_window(
            "EDGE // Research workbench", server.url,
            width=1440, height=960, min_size=(960, 640),
            background_color="#050505", text_select=True,
        )
        stop_monitor = threading.Event()
        window.events.closed += lambda: stop_monitor.set()

        def watch_server() -> None:
            while not stop_monitor.wait(0.5):
                if server.process.poll() is not None:
                    _message("EDGE's local server stopped. Close and reopen EDGE; no other server was used.")
                    window.destroy()
                    break

        threading.Thread(target=watch_server, daemon=True).start()
        if smoke_report is not None:
            def smoke() -> None:
                # The native renderer must execute real JS after Streamlit mounts.
                started = time.monotonic()
                last_error_type = None
                visits = (
                    ("Engine acceptance", "SYNTHETIC DATA ONLY"),
                    ("Backup and recovery", "Journal backup / restore"),
                    ("Provider settings", "Provider settings"),
                    ("Company watchlist", "Company / watchlist workspace"),
                    ("Research journal", "USER-AUTHORED NOTES"),
                    ("SEC filing research", "FILING METADATA ONLY"),
                )
                visited: list[str] = []
                requested = False
                while time.monotonic() - started < 60 and not stop_monitor.is_set():
                    try:
                        body = window.evaluate_js("document.body.innerText")
                        target, marker = visits[len(visited)]
                        if body and marker.casefold() in body.casefold() and "Live trading disabled" in body:
                            visited.append(target)
                            requested = False
                            if len(visited) == len(visits):
                                _write_status(
                                    smoke_report, native_window=True, dashboard_rendered=True,
                                    workspaces_rendered=visited,
                                    health=True, server_pid=server.process.pid, parent_pid=os.getpid(),
                                )
                                window.destroy()
                                return
                        if visited and not requested:
                            target = visits[len(visited)][0]
                            requested = bool(window.evaluate_js(
                                "(() => { const label = Array.from(document.querySelectorAll("
                                "'[data-testid=\"stRadio\"] label')).find(e => "
                                f"e.innerText.trim() === {json.dumps(target)}); "
                                "if (!label) return false; label.click(); return true; })()"
                            ))
                    except Exception as error:  # noqa: BLE001 -- smoke-only native interop boundary
                        last_error_type = type(error).__name__
                    time.sleep(0.3)
                _write_status(
                    smoke_report, native_window=True, dashboard_rendered=False,
                    workspaces_rendered=visited, last_error_type=last_error_type,
                )
                window.destroy()

            webview.start(
                smoke, gui="edgechromium", debug=False, private_mode=True,
                storage_path=str(runtime_dir / "webview"),
            )
        else:
            webview.start(
                gui="edgechromium", debug=False, private_mode=True,
                storage_path=str(runtime_dir / "webview"),
            )
        stop_monitor.set()
        return 0
    except Exception as error:  # noqa: BLE001 -- top-level native boundary, sanitize diagnostics
        # Never persist raw exceptions that might include provider secrets.
        if smoke_report:
            _write_status(
                smoke_report, error_type=type(error).__name__, stage=stage,
                missing_module=getattr(error, "name", None),
            )
        else:
            _message(
                f"EDGE could not start during {stage} ({type(error).__name__}).\n"
                "Use the complete dist\\EDGE folder and an installed WebView2 Runtime.\n"
                "Run the documented desktop self-test, or rebuild in .venv-desktop.\n"
                "Existing research data has not been moved or reset."
            )
        return 2
    finally:
        if splash is not None:
            splash.close()
        server.close()
        # Runtime cache only. Never clean, migrate or copy the user's data directory.
        shutil.rmtree(runtime_dir, ignore_errors=True)


def self_test(report: Path) -> int:
    """Offline packaged imports, native synthetic engine and temporary journal persistence."""
    stage = "application imports"
    try:
        from datetime import date

        from edge import simulation_service
        from edge.data.openbb_sec_provider import OpenBBSecFilingsProvider, _load_openbb
        from edge.domain import SimulationConfig
        from edge.fixtures import demo_dataset
        from edge.journal import JournalDraft, export_entry_json
        from edge.journal_store import JournalStore

        # Explicit test mode: block Python external network/DNS before optional imports.
        original_connect = socket.socket.connect
        original_dns = socket.getaddrinfo

        def local_connect(sock: socket.socket, address: Any) -> Any:
            if not isinstance(address, tuple) or address[0] not in ("127.0.0.1", "::1"):
                raise OSError("Offline desktop self-test prohibits external requests.")
            return original_connect(sock, address)

        def local_dns(host: Any, *args: Any, **kwargs: Any) -> Any:
            if host not in ("127.0.0.1", "::1", "localhost"):
                raise OSError("Offline desktop self-test prohibits external DNS.")
            return original_dns(host, *args, **kwargs)

        socket.socket.connect = local_connect
        socket.getaddrinfo = local_dns
        stage = "engine doctor"
        engine = simulation_service.engine_version()
        provider = OpenBBSecFilingsProvider().describe()
        stage = "SEC optional imports"
        modules = _load_openbb()  # imports all SEC fetchers; no lookup/request
        with tempfile.TemporaryDirectory(prefix="EDGE-self-test-") as directory:
            root = Path(directory)
            stage = "synthetic native simulation"
            result = simulation_service.run(demo_dataset(), SimulationConfig(), root / "simulation")
            draft = JournalDraft(
                "long-term", "NOTE", "User-authored test", "Temporary persistence test",
                date(2026, 1, 1), "Not company data", "Test", "Test", "", "",
            )
            stage = "temporary journal persistence"
            entry = JournalStore(root / "journal").create(draft)
            restored = JournalStore(root / "journal").get(entry.entry_id)
            if restored != entry:
                raise RuntimeError("Temporary journal persistence failed.")
            exported = json.loads(export_entry_json(restored))
            if exported["entry"]["entry_id"] != entry.entry_id:
                raise RuntimeError("Temporary journal export failed.")
        _write_status(
            report, ok=True, frozen=bool(getattr(sys, "frozen", False)),
            engine=engine, sec_provider=provider.name, sec_modules=sorted(modules),
            synthetic_run=bool(result), journal_persistence=True, journal_export=True,
            external_requests=False,
        )
        return 0
    except Exception as error:  # noqa: BLE001 -- offline diagnostic boundary
        _write_status(
            report, ok=False, error_type=type(error).__name__, stage=stage,
            missing_module=getattr(error, "name", None),
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="EDGE bundled Windows application")
    parser.add_argument("--internal-server", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--reduced-motion", action="store_true", help="Disable startup animation.")
    parser.add_argument("--desktop-self-test", type=Path, metavar="REPORT_JSON")
    parser.add_argument("--desktop-smoke", type=Path, metavar="REPORT_JSON", help=argparse.SUPPRESS)
    parser.add_argument("--desktop-native-check", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.internal_server:
        return _internal_server()
    if args.desktop_self_test:
        return self_test(args.desktop_self_test)
    if args.desktop_native_check:
        from edge.desktop_native_check import run

        return run(args.desktop_native_check)
    return run_window(reduced_motion=args.reduced_motion, smoke_report=args.desktop_smoke)


if __name__ == "__main__":
    raise SystemExit(main())
