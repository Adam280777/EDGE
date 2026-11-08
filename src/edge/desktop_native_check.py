"""Opt-in real Tk lifecycle check with an in-memory dummy vault, never OS secrets."""

from __future__ import annotations

import ctypes
import hashlib
import json
import multiprocessing
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

from edge import native_dialogs_backup, native_dialogs_manual
from edge.windows_credentials import (
    CredentialStatus,
    _show_credential_dialog,
    credential_dialog,
)

_EXPLORER_CACHE_SIZES = (
    "16", "32", "48", "96", "256", "768", "1280", "1920", "2560",
    "custom_stream", "exif", "idx", "sr", "wide", "wide_alternate",
)
_EXPLORER_CACHE_NAMES = {
    f"{prefix}_{size}.db"
    for prefix in ("iconcache", "thumbcache") for size in _EXPLORER_CACHE_SIZES
}


def _file_hashes(root: Path) -> dict[str, str]:
    """Hash diagnostic-owned files; no user profile locations are discovered."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*") if path.is_file()
    }


def _cancellation_changes(
    root: Path, before: dict[str, str], after: dict[str, str],
) -> dict[str, object]:
    """Separate a strict Explorer-cache allowlist, not arbitrary profile changes."""
    changes = sorted(
        name for name in before.keys() | after.keys()
        if before.get(name) != after.get(name)
    )
    cache_changes = []
    unexpected = []
    for name in changes:
        path = Path(name)
        parts = tuple(part.casefold() for part in path.parts)
        expected_parent = ("appdata", "local", "microsoft", "windows", "explorer")
        if (
            len(parts) >= 6 and parts[-6:-1] == expected_parent
            and path.name.casefold() in _EXPLORER_CACHE_NAMES
            and not any(part in ("data", "settings") for part in parts[:-6])
        ):
            cache_changes.append(name)
        else:
            unexpected.append(name)
    return {
        "public_cancellation_files_unchanged": not changes,
        "owned_data_settings_and_existing_files_unchanged": not unexpected,
        "known_explorer_cache_changes": cache_changes,
        "unexpected_file_changes": unexpected,
    }


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


def _select_owned_popup(action, title: str, selected: Path) -> dict[str, object]:
    """Select a synthetic path in an actual PUBLIC spawned picker, no clipboard.

    Two bounded approaches: the filename control (1148), then visible child Edit
    controls. Only #32770 HWNDs owned by current spawned children are eligible.
    """
    stop = threading.Event()
    facts: dict[str, object] = {"owned_dialog_observed": False, "path_submitted": False}
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetDlgItem.restype = wintypes.HWND
    user32.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user32.GetDlgCtrlID.restype = ctypes.c_int
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.SetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.PostMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    ]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumChildWindows.argtypes = [wintypes.HWND, callback_type, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    started = time.monotonic()
    submitted: set[tuple[int, int]] = set()

    def text(hwnd, *, class_name=False):
        buffer = ctypes.create_unicode_buffer(1024)
        function = user32.GetClassNameW if class_name else user32.GetWindowTextW
        function(hwnd, buffer, len(buffer))
        return buffer.value

    def edit_controls(hwnd) -> list[int]:
        edits = []

        @callback_type
        def enumerate_edit(control, context):
            if text(control, class_name=True) == "Edit" and user32.IsWindowVisible(control):
                edits.append(control)
            return True

        user32.EnumChildWindows(hwnd, enumerate_edit, 0)
        return edits

    def selector() -> None:
        while not stop.wait(0.1) and time.monotonic() - started < 20:
            owned = {child.pid for child in multiprocessing.active_children()}

            @callback_type
            def visit(hwnd, context, owned=owned):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if (
                    pid.value not in owned or not user32.IsWindowVisible(hwnd)
                    or text(hwnd, class_name=True) != "#32770"
                    or title not in text(hwnd)
                ):
                    return True
                facts["owned_dialog_observed"] = True
                approach = 1 if time.monotonic() - started < 8 else 2
                key = (int(hwnd), approach)
                if key in submitted:
                    return True
                edits = []
                filename = user32.GetDlgItem(hwnd, 1148)
                if approach == 1 and filename:
                    edits.append(filename)
                else:
                    edits = edit_controls(hwnd)
                facts["edit_control_ids"] = [user32.GetDlgCtrlID(edit) for edit in edits]
                if edits and user32.SetWindowTextW(edits[0], str(selected)):
                    submitted.add(key)
                    facts["path_submitted"] = True
                    facts["approach"] = approach
                    button = user32.GetDlgItem(hwnd, 1)
                    if button:
                        user32.SendMessageW(button, 0x00F5, 0, 0)  # BM_CLICK IDOK
                    else:
                        user32.PostMessageW(hwnd, 0x0111, 1, 0)
                return True

            user32.EnumWindows(visit, 0)
        if not stop.is_set():
            # Both bounded approaches are exhausted. Close only the observed
            # owned picker; do not leave a diagnostic dialog alive for 300s.
            owned = {child.pid for child in multiprocessing.active_children()}

            @callback_type
            def close_failed(hwnd, context):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if (
                    pid.value in owned and text(hwnd, class_name=True) == "#32770"
                    and title in text(hwnd)
                ):
                    user32.PostMessageW(hwnd, 0x0010, 0, 0)
                return True

            user32.EnumWindows(close_failed, 0)

    thread = threading.Thread(target=selector, daemon=True)
    thread.start()
    try:
        result = action()
        facts["returned_status"] = (
            "cancelled" if result is None else
            "selected-path" if isinstance(result, Path) else "unexpected-type"
        )
        facts["selected_expected_path"] = (
            isinstance(result, Path) and result.resolve() == selected.resolve()
        )
        if isinstance(result, Path) and result.resolve() != selected.resolve():
            # Never record an unexpected path outside this diagnostic's temp area.
            facts["different_path_returned"] = True
    finally:
        stop.set()
        thread.join(timeout=3)
    facts["owned_children_exited"] = not multiprocessing.active_children()
    return facts


def selected_file_check(report: Path) -> int:
    """Actual public pickers plus real CSV parsing and all-store bundle recovery.

    Backend application is proven here, not a Streamlit UI-click/apply claim.
    All file contents and selected paths are synthetic temporary test fixtures.
    """
    from edge.application_backup import backup_application, restore_application
    from edge.desktop import _manual_self_test

    facts: dict[str, object] = {
        "fixture_classification": "synthetic-not-market-facts",
        "clipboard_access": False, "real_vault_access": False,
        "ui_import_apply_verified": False,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="EDGE-native-selection-") as directory:
            root = Path(directory)
            application = root / "application"
            facts["backend_fixture_checks"] = _manual_self_test(application)
            paths = {
                "csv": application / "synthetic-manual-prices.csv",
                "bundle-save": root / "selected-bundle.zip",
                "bundle-open": root / "selected-bundle.zip",
                "restore-parent": root,
            }
            titles = {
                "csv": "Choose user-authored CSV",
                "bundle-save": "Save application backup",
                "bundle-open": "Choose EDGE application bundle",
                "restore-parent": "Choose existing parent for a NEW restored application folder",
            }
            outcomes = {}
            for operation, selected_path in paths.items():
                outcome = _select_owned_popup(
                    lambda operation=operation: native_dialogs_manual.choose(operation),
                    titles[operation], selected_path,
                )
                outcomes[operation] = outcome
                if not outcome.get("selected_expected_path"):
                    break
                if operation == "csv":
                    facts["selected_csv_read"] = bool(native_dialogs_manual.read_csv(
                        selected_path, max_bytes=1024 * 1024,
                    ))
                elif operation == "bundle-save":
                    backup_application(application, selected_path)
                elif operation == "restore-parent":
                    restore_application(paths["bundle-open"], root / "selected-restored")
                    facts["selected_bundle_roundtrip"] = True
            facts["public_selections"] = outcomes
            facts["ok"] = len(outcomes) == 4 and all(
                result.get("selected_expected_path") and result.get("owned_children_exited")
                for result in outcomes.values()
            )
    except Exception as error:  # noqa: BLE001 -- fixed native diagnostic facts only
        facts["ok"] = False
        facts["error_type"] = type(error).__name__
    report.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return 0 if facts["ok"] else 1


def run(report: Path) -> int:
    """Exercise actual masked Entry/save/cancel on this process's main thread.

    Dummy text is inserted, not taken from the user's clipboard. Confirmations
    are injected; this check makes no Windows Credential Manager calls.
    """
    facts: dict[str, object] = {}
    before_files = _file_hashes(report.parent)
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
            "manual_csv_open": _cancel_owned_popup(
                lambda: native_dialogs_manual.choose("csv"),
                "Choose user-authored CSV",
            ),
            "application_bundle_save": _cancel_owned_popup(
                lambda: native_dialogs_manual.choose("bundle-save"),
                "Save application backup",
            ),
            "application_bundle_open": _cancel_owned_popup(
                lambda: native_dialogs_manual.choose("bundle-open"),
                "Choose EDGE application bundle",
            ),
            "application_restore_parent": _cancel_owned_popup(
                lambda: native_dialogs_manual.choose("restore-parent"),
                "Choose existing parent for a NEW restored application folder",
            ),
        }
        after_files = _file_hashes(report.parent)
        facts.update(_cancellation_changes(report.parent, before_files, after_files))
        facts["ok"] = (
            facts.get("masked_entry") is True
            and facts.get("native_entry_rendered") is True
            and facts["save_status"] == CredentialStatus.SAVED
            and facts["cancel_status"] == CredentialStatus.CANCELLED
            and facts["dummy_save_only"] is True
            and facts["owned_data_settings_and_existing_files_unchanged"] is True
            and all(
                all(result.values())
                for result in facts["public_spawn_cancellations"].values()
            )
        )
    except Exception as error:  # noqa: BLE001 -- report class only, no native details
        facts = {"ok": False, "error_type": type(error).__name__}
    report.write_text(json.dumps(facts, indent=2), encoding="utf-8")
    return 0 if facts["ok"] else 1
