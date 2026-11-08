"""Explicit, short-lived native selections for manual CSV and application bundles."""

import multiprocessing
import stat
import threading
from pathlib import Path

from edge.application_storage_lock import check_local_path

_LOCK = threading.Lock()


def _worker(sender, operation):
    import tkinter as tk
    from tkinter import filedialog

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        options = {"parent": root}
        if operation == "csv":
            path = filedialog.askopenfilename(
                **options, title="Choose user-authored CSV / no vendor imports",
                filetypes=[("User-authored UTF-8 CSV", "*.csv")],
            )
        elif operation == "bundle-save":
            path = filedialog.asksaveasfilename(
                **options, title="Save application backup / new filename only",
                initialfile="edge-application-backup.zip", defaultextension=".zip",
                filetypes=[("EDGE application bundle", "*.zip")], confirmoverwrite=True,
            )
        elif operation == "bundle-open":
            path = filedialog.askopenfilename(
                **options, title="Choose EDGE application bundle",
                filetypes=[("EDGE application bundle", "*.zip")],
            )
        elif operation == "restore-parent":
            path = filedialog.askdirectory(
                **options, title="Choose existing parent for a NEW restored application folder",
                mustexist=True,
            )
        else:
            raise ValueError
        sender.send(("selected", path) if path else ("cancelled", None))
    except Exception:  # noqa: BLE001 -- only fixed IPC status leaves the child
        sender.send(("error", None))
    finally:
        if root is not None:
            root.destroy()
        sender.close()


def choose(operation: str) -> Path | None:
    if operation not in ("csv", "bundle-save", "bundle-open", "restore-parent"):
        raise ValueError("Unsupported native selection.")
    if not _LOCK.acquire(blocking=False):
        raise RuntimeError("Close the existing native dialog before retrying.")
    process = receiver = sender = None
    try:
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_worker, args=(sender, operation), daemon=True)
        process.start()
        sender.close()
        if not receiver.poll(300):
            raise RuntimeError("Native dialog timed out.")
        status, value = receiver.recv()
        if status == "cancelled" and value is None:
            return None
        if status == "selected" and isinstance(value, str) and Path(value).is_absolute():
            return Path(value)
        raise RuntimeError("Native dialog unavailable.")
    finally:
        if sender is not None:
            sender.close()
        if receiver is not None:
            receiver.close()
        if process is not None and process.pid is not None:
            process.join(timeout=1)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            process.close()
        _LOCK.release()


def read_csv(path: Path, *, max_bytes: int) -> bytes:
    """Read only a selected regular file, bounded before and during reading."""
    path = check_local_path(path)
    with path.open("rb") as stream:
        metadata = stat.S_ISREG(__import__("os").fstat(stream.fileno()).st_mode)
        if not metadata or __import__("os").fstat(stream.fileno()).st_size > max_bytes:
            raise ValueError("CSV exceeds the regular-file or byte limit.")
        content = stream.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise ValueError("CSV exceeds the byte limit.")
    return content
