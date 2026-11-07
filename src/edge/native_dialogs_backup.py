"""Short-lived Tk dialogs, isolated from Streamlit's script thread.

The frozen executable entry must call multiprocessing.freeze_support() before
argument parsing. Spawned children inherit the desktop's kill-on-close job.
No browser, server, file writes, or persistent Tk root is used by this helper.
"""

from __future__ import annotations

import multiprocessing
import threading
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Literal

_Operation = Literal["save", "open", "directory"]
_DIALOG_TIMEOUT = 300.0
_DIALOG_LOCK = threading.Lock()
_ERROR = "Native file dialog unavailable. Close any open dialog and try again."


class NativeDialogError(RuntimeError):
    """A native dialog could not safely complete."""


def _show_dialog(operation: _Operation) -> str:
    """Create and destroy Tk on a dedicated process's main thread only."""
    if threading.current_thread() is not threading.main_thread():
        raise NativeDialogError(_ERROR)
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    try:
        root.withdraw()
        root.attributes("-topmost", True)
        options = {"parent": root}
        if operation == "save":
            return filedialog.asksaveasfilename(
                **options,
                title="Save EDGE journal backup — new filename only",
                initialfile="edge-journal-backup.zip",
                defaultextension=".zip",
                filetypes=[("EDGE journal backup", "*.zip")],
                confirmoverwrite=True,
            )
        if operation == "open":
            return filedialog.askopenfilename(
                **options,
                title="Choose EDGE journal backup",
                filetypes=[("EDGE journal backup", "*.zip")],
            )
        if operation == "directory":
            return filedialog.askdirectory(
                **options,
                title="Restore journal — choose a new or empty folder",
                mustexist=False,
            )
        raise NativeDialogError(_ERROR)
    finally:
        root.destroy()


def _dialog_worker(sender: Connection, operation: _Operation) -> None:
    """Send only a selected path or fixed status, never a native exception."""
    try:
        selected = _show_dialog(operation)
        response = ("selected", selected) if selected else ("cancelled", None)
    except Exception:  # noqa: BLE001 -- sanitize Tk errors before crossing IPC
        response = ("error", None)
    try:
        sender.send(response)
    except OSError:
        pass  # Parent has already cancelled/timed out; no diagnostic content.
    finally:
        sender.close()


def _choose(operation: _Operation) -> Path | None:
    """Wait for one explicitly requested dialog; reap the child on every outcome."""
    if not _DIALOG_LOCK.acquire(blocking=False):
        raise NativeDialogError(_ERROR)
    receiver = sender = process = None
    started = False
    try:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_dialog_worker, args=(sender, operation), daemon=True
        )
        process.start()
        started = True
        sender.close()
        if not receiver.poll(_DIALOG_TIMEOUT):
            raise NativeDialogError(_ERROR)
        status, selected = receiver.recv()
        if status == "cancelled" and selected is None:
            return None
        if status == "selected" and isinstance(selected, str) and selected:
            path = Path(selected)
            if path.is_absolute():
                return path
        raise NativeDialogError(_ERROR)
    except Exception:  # noqa: BLE001 -- IPC errors must never leak native content
        raise NativeDialogError(_ERROR) from None
    finally:
        try:
            if sender is not None:
                sender.close()
            if receiver is not None:
                receiver.close()
            if process is not None:
                if started:
                    process.join(timeout=1)
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=5)
                if not process.is_alive():
                    process.close()
        finally:
            _DIALOG_LOCK.release()


def choose_backup_destination() -> Path | None:
    """Native Save As selection, not permission to overwrite an existing file."""
    return _choose("save")


def choose_backup_source() -> Path | None:
    """Native Open selection; cancellation has no effects."""
    return _choose("open")


def choose_restore_destination() -> Path | None:
    """Select a separate new/empty journal directory; API enforces safety."""
    return _choose("directory")
