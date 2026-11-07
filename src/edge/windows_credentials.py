"""Fixed-target Windows vault access and native-only credential editing.

Import and dialog status never probe the vault. Only an authorized fetch may
call ``read``. No secret crosses the Tk/browser boundary or enters session state.
"""

# ruff: noqa: BLE001
# All native boundaries intentionally suppress unknown OS/Tk errors: they may
# include vault blobs or pasted text. Only fixed exceptions/status leave here.

from __future__ import annotations

import ctypes
import multiprocessing
import re
import sys
import threading
from ctypes import wintypes
from enum import StrEnum

TARGET = "EDGE/FMP/API_KEY/v1"
_KEY = re.compile(r"^[A-Za-z0-9._~-]{1,512}$")
_DIALOG_LOCK = threading.Lock()
DIALOG_TIMEOUT_SECONDS = 180
_STATUS_BYTES = {
    status.encode("ascii"): status
    for status in ("saved", "removed", "cancelled", "busy", "unavailable", "failed")
}


class CredentialStatus(StrEnum):
    SAVED = "saved"
    REMOVED = "removed"
    CANCELLED = "cancelled"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class CredentialError(Exception):
    def __init__(self) -> None:
        super().__init__("Windows credential operation failed.")


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialManager:
    """Generic credential, persisted for this Windows user on this machine."""

    def __init__(self, *, api=None, last_error=None) -> None:
        self._api = api
        self._last_error = last_error

    def _load(self):
        if self._api is None:
            if sys.platform != "win32":
                raise CredentialError()
            api = ctypes.WinDLL("advapi32", use_last_error=True)
            api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
            api.CredWriteW.restype = wintypes.BOOL
            api.CredReadW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.POINTER(_Credential)),
            ]
            api.CredReadW.restype = wintypes.BOOL
            api.CredDeleteW.argtypes = [
                wintypes.LPCWSTR,
                wintypes.DWORD,
                wintypes.DWORD,
            ]
            api.CredDeleteW.restype = wintypes.BOOL
            api.CredFree.argtypes = [ctypes.c_void_p]
            api.CredFree.restype = None
            self._api = api
            self._last_error = ctypes.get_last_error
        return self._api

    def save(self, secret: str) -> None:
        if not isinstance(secret, str) or not _KEY.fullmatch(secret):
            raise CredentialError()
        blob = bytearray(secret.encode("utf-16-le"))
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer(blob)
        try:
            credential = _Credential(
                Type=1,
                TargetName=TARGET,
                CredentialBlobSize=len(blob),
                CredentialBlob=buffer,
                Persist=2,
                UserName="EDGE",
            )
            if not self._load().CredWriteW(ctypes.byref(credential), 0):
                raise CredentialError()
        except Exception:
            raise CredentialError() from None
        finally:
            ctypes.memset(buffer, 0, len(blob))

    def read(self) -> str | None:
        """Explicit authorized fetch boundary only; always free the vault buffer."""
        pointer = ctypes.POINTER(_Credential)()
        try:
            api = self._load()
            if not api.CredReadW(TARGET, 1, 0, ctypes.byref(pointer)):
                if self._last_error and self._last_error() == 1168:
                    return None
                raise CredentialError()
            try:
                credential = pointer.contents
                size = credential.CredentialBlobSize
                if size < 2 or size > 1024 or size % 2:
                    raise CredentialError()
                secret = ctypes.string_at(credential.CredentialBlob, size).decode(
                    "utf-16-le"
                )
                if not _KEY.fullmatch(secret):
                    raise CredentialError()
                return secret
            finally:
                api.CredFree(pointer)
        except Exception:
            raise CredentialError() from None

    def delete(self) -> None:
        try:
            if not self._load().CredDeleteW(TARGET, 1, 0):
                if self._last_error and self._last_error() == 1168:
                    return
                raise CredentialError()
        except Exception:
            raise CredentialError() from None


def _show_credential_dialog(
    action: str, *, manager=None, tk_module=None, messagebox_module=None
) -> CredentialStatus:
    """Child-main-thread Tk implementation; injection is local test-only."""
    if action not in {"save", "remove"}:
        return CredentialStatus.FAILED
    if tk_module is None and sys.platform != "win32":
        return CredentialStatus.UNAVAILABLE
    if threading.current_thread() is not threading.main_thread():
        return CredentialStatus.UNAVAILABLE
    root = None
    entry = None
    status = CredentialStatus.CANCELLED
    try:
        if tk_module is None:
            import tkinter as tk_module
        if messagebox_module is None:
            from tkinter import messagebox as messagebox_module
        vault = manager if manager is not None else WindowsCredentialManager()
        root = tk_module.Tk()

        def callback_error(*ignored) -> None:
            nonlocal status
            status = CredentialStatus.FAILED
            root.quit()

        # Tk normally prints callback exceptions, possibly including pasted text.
        root.report_callback_exception = callback_error
        root.title("EDGE · FMP credential")
        root.resizable(False, False)
        root.attributes("-topmost", True)
        if action == "remove":
            root.withdraw()
            if messagebox_module.askyesno(
                "Remove FMP credential?",
                "Remove EDGE's FMP key from Windows Credential Manager?",
                parent=root,
            ):
                vault.delete()
                status = CredentialStatus.REMOVED
        else:
            root.configure(background="#050505")
            tk_module.Label(
                root,
                text="FMP API key · Windows Credential Manager only",
                background="#050505",
                foreground="#ffc15a",
            ).pack(padx=24, pady=(20, 8))
            entry = tk_module.Entry(root, show="*", width=48)
            entry.pack(padx=24, pady=8)
            entry.focus_set()

            # Native Entry paste remains enabled; no clipboard read or clearing.
            def save() -> None:
                nonlocal status
                if not messagebox_module.askyesno(
                    "Save FMP credential?",
                    "Save or replace EDGE's FMP key in Windows Credential Manager?",
                    parent=root,
                ):
                    return
                try:
                    vault.save(entry.get())
                    status = CredentialStatus.SAVED
                except Exception:
                    status = CredentialStatus.FAILED
                entry.delete(0, "end")
                root.quit()

            def cancel() -> None:
                entry.delete(0, "end")
                root.quit()

            tk_module.Button(root, text="Save to Windows vault", command=save).pack(
                padx=24, pady=8
            )
            tk_module.Button(root, text="Cancel", command=cancel).pack(
                padx=24, pady=(0, 20)
            )
            root.protocol("WM_DELETE_WINDOW", cancel)
            root.bind("<Escape>", lambda event: cancel())
            root.mainloop()
    except Exception:
        status = CredentialStatus.FAILED
    finally:
        try:
            if entry is not None:
                entry.delete(0, "end")
            if root is not None:
                root.destroy()
        except Exception:
            status = CredentialStatus.FAILED
    return status


def _credential_dialog_worker(action: str, connection) -> None:
    """Spawn target: key entry and vault writes stay entirely inside this child."""
    try:
        status = _show_credential_dialog(action)
        if type(status) is not CredentialStatus:
            status = CredentialStatus.FAILED
        connection.send_bytes(status.value.encode("ascii"))
    except Exception:
        # No exception object, traceback, secret or provider text enters IPC.
        try:
            connection.send_bytes(b"failed")
        except Exception:  # noqa: S110 - logging could disclose native credential text.
            pass
    finally:
        connection.close()


def credential_dialog(
    action: str, *, context=None, timeout_seconds: int = DIALOG_TIMEOUT_SECONDS
) -> CredentialStatus:
    """Bounded owned spawn: action in, closed status bytes out, never secrets.

    The packaged entrypoint must call multiprocessing.freeze_support before main.
    Tk is created only on the spawned process's main thread. A timeout terminates
    that owned child; it cannot roll back a vault save already confirmed there.
    """
    if action not in {"save", "remove"}:
        return CredentialStatus.FAILED
    if (
        type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= DIALOG_TIMEOUT_SECONDS
    ):
        return CredentialStatus.FAILED
    if context is None and sys.platform != "win32":
        return CredentialStatus.UNAVAILABLE
    if not _DIALOG_LOCK.acquire(blocking=False):
        return CredentialStatus.BUSY
    receive = send = process = None
    started = False
    status = CredentialStatus.FAILED
    try:
        ctx = context if context is not None else multiprocessing.get_context("spawn")
        receive, send = ctx.Pipe(duplex=False)
        process = ctx.Process(
            target=_credential_dialog_worker,
            args=(action, send),
            daemon=True,
        )
        process.start()
        started = True
        send.close()
        if receive.poll(timeout_seconds):
            # Never unpickle IPC; fixed tiny bytes only. Oversize fails closed.
            value = receive.recv_bytes(32)
            if value in _STATUS_BYTES:
                candidate = CredentialStatus(_STATUS_BYTES[value])
                allowed = {
                    CredentialStatus.CANCELLED,
                    CredentialStatus.UNAVAILABLE,
                    CredentialStatus.FAILED,
                    CredentialStatus.SAVED
                    if action == "save"
                    else CredentialStatus.REMOVED,
                }
                if candidate in allowed:
                    status = candidate
        else:
            # A timeout cannot prove that no native-confirmed write occurred.
            status = CredentialStatus.FAILED
    except Exception:
        status = CredentialStatus.FAILED
    finally:
        try:
            if started:
                process.join(timeout=1)
                if process.is_alive():
                    status = CredentialStatus.FAILED
                    process.terminate()
                    process.join(timeout=2)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                if process.is_alive():
                    status = CredentialStatus.FAILED
                else:
                    process.close()
            elif process is not None:
                process.close()
        except Exception:
            status = CredentialStatus.FAILED
        try:
            if receive is not None:
                receive.close()
            if send is not None:
                send.close()
        except Exception:
            status = CredentialStatus.FAILED
        _DIALOG_LOCK.release()
    return status
