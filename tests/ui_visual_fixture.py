"""Bounded developer-only WebView2 visual check with a disposable data/profile root."""

import os
import runpy
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1] / "app.py"


def native_check():
    import ctypes
    from ctypes import wintypes

    from PIL import Image

    def child_pids(parent_pid):
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        class ProcessEntry(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
                ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
                ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
                ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
                ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260),
            ]
        snapshot = kernel32.CreateToolhelp32Snapshot(2, 0)
        relations = {}
        try:
            entry = ProcessEntry()
            entry.size = ctypes.sizeof(entry)
            more = kernel32.Process32FirstW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
            while more:
                relations[entry.pid] = entry.parent
                more = kernel32.Process32NextW(ctypes.c_void_p(snapshot), ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(snapshot))
        parents = {parent_pid}
        while True:
            found = {pid for pid, parent in relations.items() if parent in parents}
            if found <= parents:
                return parents - {parent_pid}
            parents |= found

    def capture_owned_window(handle, path):
        hwnd = int(handle.Handle.ToInt64())
        rect = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right - rect.left, rect.bottom - rect.top
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        user32.GetDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
        gdi32.SelectObject.restype = ctypes.c_void_p
        screen = user32.GetDC(hwnd)
        memory = gdi32.CreateCompatibleDC(ctypes.c_void_p(screen))
        bitmap = gdi32.CreateCompatibleBitmap(ctypes.c_void_p(screen), width, height)
        old = gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(bitmap))

        class BitmapInfo(ctypes.Structure):
            _fields_ = [
                ("size", wintypes.DWORD), ("width", wintypes.LONG),
                ("height", wintypes.LONG), ("planes", wintypes.WORD),
                ("bits", wintypes.WORD), ("compression", wintypes.DWORD),
                ("image_size", wintypes.DWORD), ("xppm", wintypes.LONG),
                ("yppm", wintypes.LONG), ("used", wintypes.DWORD),
                ("important", wintypes.DWORD),
            ]

        try:
            if not user32.PrintWindow(hwnd, ctypes.c_void_p(memory), 2):
                raise RuntimeError("Owned-window capture unavailable.")
            info = BitmapInfo(40, width, -height, 1, 32, 0, 0, 0, 0, 0, 0)
            bits = ctypes.create_string_buffer(width * height * 4)
            if not gdi32.GetDIBits(
                ctypes.c_void_p(memory), ctypes.c_void_p(bitmap), 0, height,
                bits, ctypes.byref(info), 0,
            ):
                raise RuntimeError("Owned-window pixels unavailable.")
            Image.frombuffer("RGB", (width, height), bits, "raw", "BGRX", 0, 1).save(path)
        finally:
            gdi32.SelectObject(ctypes.c_void_p(memory), ctypes.c_void_p(old))
            gdi32.DeleteObject(ctypes.c_void_p(bitmap))
            gdi32.DeleteDC(ctypes.c_void_p(memory))
            user32.ReleaseDC(hwnd, ctypes.c_void_p(screen))

    root = Path(tempfile.mkdtemp(prefix="edge-ui-check-"))
    for name in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        directory = root / name.lower()
        directory.mkdir()
        os.environ[name] = str(directory)
    os.environ["EDGE_UI_TEST_ROOT"] = str(root)
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    server = subprocess.Popen([
        sys.executable, "-m", "streamlit", "run", str(Path(__file__).resolve()),
        "--server.address=127.0.0.1", f"--server.port={port}",
        "--browser.gatherUsageStats=false", "--server.headless=true",
    ])
    print(f"Temporary evidence root: {root}", flush=True)
    try:
        for _ in range(120):
            try:
                with urlopen(f"{url}/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(0.5)
        else:
            raise RuntimeError("Owned server did not become ready.")
        import webview

        from edge.desktop_webview import configure_renderer, configure_window_settings

        configure_renderer(url)
        configure_window_settings(webview.settings)
        window = webview.create_window(
            "EDGE disposable UI verification", url, width=1440, height=960,
            min_size=(960, 640), background_color="#050505", text_select=True,
        )
        failures = []

        def capture():
            try:
                for _ in range(100):
                    if window.evaluate_js("document.body.innerText.toLowerCase().includes('live trading disabled')"):
                        break
                    time.sleep(0.25)
                else:
                    raise RuntimeError("Dashboard did not render.")
                namespace = runpy.run_path(str(APP))
                labels = namespace["WORKSPACE_LABELS"]

                def click_label(label):
                    window.evaluate_js(
                        "Array.from(document.querySelectorAll('label'))"
                        f".find(e=>e.innerText.trim().toLowerCase()==={label.lower()!r}).click()"
                    )
                    time.sleep(0.6)

                def click_button(label):
                    window.evaluate_js(
                        "Array.from(document.querySelectorAll('button'))"
                        f".find(e=>e.innerText.trim().toLowerCase()==={label.lower()!r}).click()"
                    )
                    time.sleep(0.7)

                def input_text(label, value):
                    window.evaluate_js(
                        "(()=>{const c=Array.from(document.querySelectorAll('[data-testid=\"stTextInput\"]'))"
                        f".find(e=>e.querySelector('label')?.innerText.trim()==={label!r});"
                        "const e=c.querySelector('input');"
                        f"Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,{value!r});"
                        "e.dispatchEvent(new Event('input',{bubbles:true}));"
                        "e.dispatchEvent(new Event('change',{bubbles:true}));e.blur();})()"
                    )

                def native_path(path):
                    dialog = None
                    try:
                        for _ in range(100):
                            pids = child_pids(server.pid)
                            found = []
                            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
                            def inspect(hwnd, unused, owned=pids, matches=found):
                                pid = wintypes.DWORD()
                                ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                                title = ctypes.create_unicode_buffer(512)
                                ctypes.windll.user32.GetWindowTextW(hwnd, title, 512)
                                if (pid.value in owned and ctypes.windll.user32.IsWindowVisible(hwnd)
                                        and ("Choose user-authored CSV" in title.value
                                             or "application backup" in title.value
                                             or "application bundle" in title.value)):
                                    matches.append(hwnd)
                                return True
                            ctypes.windll.user32.EnumWindows(callback_type(inspect), 0)
                            if found:
                                dialog = found[0]
                                break
                            time.sleep(0.1)
                        if dialog is None:
                            raise RuntimeError("Owned native file dialog not observable.")
                        user32 = ctypes.windll.user32
                        user32.GetDlgItem.restype = ctypes.c_void_p
                        edit = user32.GetDlgItem(dialog, 1148)
                        if not edit:
                            raise RuntimeError("Native filename control could not be identified safely.")
                        user32.SendMessageW(ctypes.c_void_p(edit), 0x000C, 0, ctypes.c_wchar_p(str(path)))
                        user32.SendMessageW(dialog, 0x0111, 1, 0)
                        time.sleep(2)
                    finally:
                        if dialog and ctypes.windll.user32.IsWindow(dialog):
                            ctypes.windll.user32.PostMessageW(dialog, 0x0010, 0, 0)

                def cdp(method, parameters):
                    import json

                    from System import Action, String
                    from System.Threading.Tasks import Task

                    done = threading.Event()
                    result = []
                    def complete(task):
                        result.append(str(task.Result))
                        done.set()
                    def invoke():
                        window.native.webview.CoreWebView2.CallDevToolsProtocolMethodAsync(
                            method, json.dumps(parameters)
                        ).ContinueWith(Action[Task[String]](complete))
                    window.native.webview.Invoke(Action(invoke))
                    if not done.wait(10):
                        raise RuntimeError("Native DevTools check timed out.")
                    return result[0]

                cdp("Emulation.setEmulatedMedia", {
                    "features": [{"name": "prefers-reduced-motion", "value": "reduce"}]
                })
                print("Native reduced motion: " + str(window.evaluate_js(
                    "matchMedia('(prefers-reduced-motion: reduce)').matches"
                )), flush=True)
                for width, height in (() if "--flows" in sys.argv else ((1440, 960), (960, 640))):
                    window.resize(width, height)
                    time.sleep(1)
                    for workspace in namespace["WORKSPACES"]:
                        click_label(labels[workspace])
                        time.sleep(1.5)
                        window.evaluate_js("window.scrollTo(0,0)")
                        handle = window.native
                        name = workspace.replace(" / ", "-").replace(" ", "-")
                        path = root / f"{name}-{width}x{height}.png"
                        capture_owned_window(handle, path)
                        metrics = window.evaluate_js(
                            "({width:innerWidth,height:innerHeight,"
                            "overflow:document.documentElement.scrollWidth>innerWidth,"
                            "title:document.querySelector('h1')?.innerText})"
                        )
                        print(f"Native capture: {path}; {metrics}", flush=True)
                click_label(labels["Monitoring"])
                cdp("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
                cdp("Input.dispatchKeyEvent", {"type": "keyUp", "key": "Tab", "code": "Tab", "windowsVirtualKeyCode": 9})
                print("Native keyboard focus: " + str(window.evaluate_js(
                    "({tag:document.activeElement.tagName,role:document.activeElement.getAttribute('role'),"
                    "visible:document.activeElement.matches(':focus-visible'),"
                    "outline:getComputedStyle(document.activeElement).outline})"
                )), flush=True)
                click_label("New rule")
                click_button("Save immutable monitoring rule")
                print("Native rule invalid input: " + str(window.evaluate_js(
                    "document.body.innerText.includes('Rule save failed')"
                )), flush=True)
                window.evaluate_js("document.querySelector('[data-testid=\"stAlertContentError\"]').scrollIntoView()")
                capture_owned_window(window.native, root / "Monitoring-error-960x640.png")
                click_label(labels["Manual portfolio"])
                click_label("Append record")
                click_button("Append manual ledger record")
                print("Native ledger invalid input: " + str(window.evaluate_js(
                    "document.body.innerText.includes('Ledger append failed')"
                )), flush=True)
                click_label("Import CSV")
                click_button("Choose native CSV and validate all rows…")
                print("Native CSV missing declaration: " + str(window.evaluate_js(
                    "document.body.innerText.includes('Both manual authorship')"
                )), flush=True)
                click_label(labels["Discovery"])
                click_label("Import CSV")
                click_button("Choose native CSV and validate all rows…")
                print("Native fundamental missing declaration: " + str(window.evaluate_js(
                    "document.body.innerText.includes('Both manual authorship')"
                )), flush=True)
                click_label(labels["Application recovery"])
                click_button("Restore separate application copy…")
                print("Native restore invalid destination name: " + str(window.evaluate_js(
                    "document.body.innerText.includes('Application restore failed')"
                )), flush=True)
                if "--flows" in sys.argv:
                    import csv
                    import io
                    from datetime import UTC, datetime

                    from edge.data.manual_observations import PRICE_CSV_HEADERS

                    stream = io.StringIO()
                    writer = csv.DictWriter(stream, fieldnames=PRICE_CSV_HEADERS)
                    writer.writeheader()
                    now = datetime.now(UTC).isoformat()
                    writer.writerow({
                        "symbol": "UITEST", "market": "UK", "currency": "GBP",
                        "exchange": "LSE", "session_date": now[:10],
                        "as_of": now, "declared_at": now, "source": "UI test / not market data",
                        "source_origin": "user-authored", "price_unit": "GBp",
                        "open": "100", "high": "110", "low": "90", "close": "105",
                        "volume": "1", "volume_unit": "shares",
                    })
                    path = root / "manual-ui-test.csv"
                    path.write_bytes(stream.getvalue().encode("utf-8"))
                    click_label(labels["Manual portfolio"])
                    click_label("Import CSV")
                    input_text("CSV ticker / user-declared", "UITEST")
                    input_text("CSV exchange / exact declared label", "LSE")
                    input_text("CSV company name / user-declared", "UI fixture / identity unverified")
                    click_label("I authored these observations; this is not a vendor export")
                    click_label("I declare permission for local manual-data use")
                    click_button("Choose native CSV and validate all rows…")
                    native_path(path)
                    if not window.evaluate_js("document.body.innerText.includes('All rows validated as manual declarations')"):
                        raise RuntimeError("Native-selected CSV did not produce a validated preview.")
                    click_button("Apply selected observation to session valuation")
                    print("Native CSV selection → bounded read → parser → preview → explicit apply: PASS", flush=True)
                    capture_owned_window(window.native, root / "Native-CSV-applied.png")
                print(f"Temporary data created: {(root / 'data').exists()}", flush=True)
            except Exception as error:
                failures.append(error)
                raise
            finally:
                window.destroy()

        webview.start(capture, gui="edgechromium", private_mode=True,
                      storage_path=str(root / "webview"), debug=False)
        if failures:
            raise failures[0]
    finally:
        for pid in child_pids(server.pid):
            kernel32 = ctypes.windll.kernel32
            kernel32.OpenProcess.restype = ctypes.c_void_p
            handle = kernel32.OpenProcess(0x100001, False, pid)
            if handle:
                kernel32.TerminateProcess(ctypes.c_void_p(handle), 1)
                kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 5000)
                kernel32.CloseHandle(ctypes.c_void_p(handle))
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=10)
        print("Owned native window and server stopped.", flush=True)


if "--native" in sys.argv:
    native_check()
else:
    root = Path(os.environ["EDGE_UI_TEST_ROOT"])
    namespace = runpy.run_path(str(APP))
    namespace["main"](root / "data")
