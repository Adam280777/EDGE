"""Opt-in real Tk lifecycle check with an in-memory dummy vault, never OS secrets."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from edge import native_dialogs_backup
from edge.windows_credentials import (
    CredentialStatus,
    _show_credential_dialog,
    credential_dialog,
)


def _cancel_owned_popup(action, title: str) -> dict[str, object]:
    """Close only a matching visible HWND belonging to this call's spawned child."""
    stop = threading.Event()
    closed: list[int] = []
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    def closer() -> None:
        deadline = time.monotonic() + 25
        while not stop.wait(.1) and time.monotonic() < deadline:
            owned = {child.pid for child in multiprocessing.active_children()}

            @callback_type
            def visit(hwnd, _context, owned=owned):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value in owned and user32.IsWindowVisible(hwnd):
                    text = ctypes.create_unicode_buffer(512)
                    user32.GetWindowTextW(hwnd, text, len(text))
                    if title in text.value and user32.PostMessageW(hwnd, 0x0010, 0, 0):
                        closed.append(pid.value)  # WM_CLOSE, owned child only
                return True

            user32.EnumWindows(visit, 0)

    thread = threading.Thread(target=closer, daemon=True)
    thread.start()
    try:
        result = action()
    finally:
        stop.set()
        thread.join(timeout=3)
    return {
        "cancelled": result is None or result == CredentialStatus.CANCELLED,
        "owned_dialog_closed": bool(closed),
        "owned_children_exited": not multiprocessing.active_children(),
    }


def run(report: Path) -> int:
    """Exercise actual masked Entry/save/cancel on this process's main thread.

    Dummy text is inserted, not taken from the user's clipboard. Confirmations
    are injected; this check makes no Windows Credential Manager calls.
    """
    facts: dict[str, object] = {}
    before_files = {
        str(path.relative_to(report.parent)): path.stat().st_size
        for path in report.parent.rglob("*") if path.is_file()
    }
    saved: list[bool] = []
    real_tk = tk.Tk

    class DummyVault:
        def save(self, value: str) -> None:
            saved.append(value == "EDGE_DIAGNOSTIC_DUMMY")

    def show(action_button: str) -> CredentialStatus:
        def root_factory() -> tk.Tk:
            root = real_tk()

            def interact() -> None:
                try:
                    entry = next(
                        item for item in root.winfo_children() if isinstance(item, tk.Entry)
                    )
                    facts["masked_entry"] = entry.cget("show") == "*"
                    facts["native_entry_rendered"] = bool(entry.winfo_ismapped())
                    entry.insert(0, "EDGE_DIAGNOSTIC_DUMMY")
                    button = next(
                        item for item in root.winfo_children()
                        if isinstance(item, tk.Button) and item.cget("text") == action_button
                    )
                    button.invoke()
                except Exception:  # noqa: BLE001 -- diagnostic emits fixed facts only
                    facts["interaction_failed"] = True
                    root.quit()

            root.after(500, interact)
            root.after(5000, root.quit)
            return root

        module = SimpleNamespace(
            Tk=root_factory, Label=tk.Label, Entry=tk.Entry, Button=tk.Button,
        )
        return _show_credential_dialog(
            "save", manager=DummyVault(), tk_module=module,
            messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: True),
        )

    try:
        facts["save_status"] = str(show("Save to Windows vault"))
        facts["cancel_status"] = str(show("Cancel"))
        facts["dummy_save_only"] = saved == [True]
        facts["real_vault_access"] = False
        facts["clipboard_access"] = False
        facts["native_confirmations_automated"] = False
        # Diagnostic-only wait ceiling; no runtime settings or user storage change.
        native_dialogs_backup._DIALOG_TIMEOUT = 30.0
        facts["public_spawn_cancellations"] = {
            "credential": _cancel_owned_popup(
                lambda: credential_dialog("save", timeout_seconds=30),
                "EDGE · FMP credential",
            ),
            "backup_save": _cancel_owned_popup(
                native_dialogs_backup.choose_backup_destination,
                "Save EDGE journal backup",
            ),
            "backup_open": _cancel_owned_popup(
                native_dialogs_backup.choose_backup_source, "Choose EDGE journal backup",
            ),
            "restore_folder": _cancel_owned_popup(
                native_dialogs_backup.choose_restore_destination,
                "Restore journal",
            ),
        }
        after_files = {
            str(path.relative_to(report.parent)): path.stat().st_size
            for path in report.parent.rglob("*") if path.is_file()
        }
        facts["public_cancellation_files_unchanged"] = before_files == after_files
        facts["ok"] = (
            facts.get("masked_entry") is True
            and facts.get("native_entry_rendered") is True
            and facts["save_status"] == CredentialStatus.SAVED
            and facts["cancel_status"] == CredentialStatus.CANCELLED
            and facts["dummy_save_only"] is True
            and facts["public_cancellation_files_unchanged"] is True
            and all(
                all(result.values())
                for result in facts["public_spawn_cancellations"].values()
            )
        )
    except Exception as error:  # noqa: BLE001 -- report class only, no native details
        facts = {"ok": False, "error_type": type(error).__name__}
    report.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return 0 if facts["ok"] else 1
