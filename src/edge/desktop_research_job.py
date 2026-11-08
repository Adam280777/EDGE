"""Bounded Windows worker ownership, assigned BEFORE its first instruction.

Workers inherit the desktop server's kill-on-close job and also enter their own
nested job. No breakaway, shell, PATH lookup, install or provider operation occurs.
The caller owns fixed interpreter/resource attestation and JSON protocol parsing.
"""

from __future__ import annotations

import ctypes
import math
import os
import subprocess
import threading
import time
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from edge.desktop_job import ServerJob


@dataclass(frozen=True)
class JobWorkerResult:
    status: Literal["succeeded", "failed", "timeout", "output-limit", "cancelled"]
    returncode: int | None
    stdout: bytes
    stderr: bytes
    elapsed_seconds: float = 0.0
    phase_timings: tuple[tuple[str, float], ...] = ()


CLEANUP_ALLOWANCE_SECONDS = 2.0


class _ManagedStop(Exception):
    def __init__(self, status: str) -> None:
        self.status = status


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ThreadID", wintypes.DWORD),
        ("th32OwnerProcessID", wintypes.DWORD),
        ("tpBasePri", ctypes.c_long),
        ("tpDeltaPri", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
    ]


def _resume_owned_process(pid: int) -> None:
    """Popen closes hThread; obtain ONLY this suspended process's primary thread."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    api.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)]
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.ResumeThread.argtypes = [wintypes.HANDLE]
    api.ResumeThread.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("Cannot inspect suspended worker thread.")
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        present = api.Thread32First(snapshot, ctypes.byref(entry))
        matches = []
        while present:
            if entry.th32OwnerProcessID == pid:
                matches.append(entry.th32ThreadID)
            present = api.Thread32Next(snapshot, ctypes.byref(entry))
        if len(matches) != 1:
            raise OSError("Suspended worker must have exactly one primary thread.")
        thread = api.OpenThread(2, False, matches[0])  # THREAD_SUSPEND_RESUME
        if not thread:
            raise OSError("Cannot open suspended worker thread.")
        try:
            if api.ResumeThread(thread) != 1:
                raise OSError("Unexpected worker suspension count.")
        finally:
            api.CloseHandle(thread)
    finally:
        api.CloseHandle(snapshot)


def _cancel_owned_pipe_io(threads: list[threading.Thread]) -> None:
    """Cancel only this call's pipe threads; never terminate arbitrary threads."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenThread.restype = wintypes.HANDLE
    api.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    for thread in threads:
        if not thread.is_alive() or thread.native_id is None:
            continue
        handle = api.OpenThread(1, False, thread.native_id)  # THREAD_TERMINATE access
        if handle:
            try:
                api.CancelSynchronousIo(handle)
            finally:
                api.CloseHandle(handle)


def run_job_worker(
    argv: tuple[str, ...],
    *,
    cwd: Path,
    env: Mapping[str, str],
    payload: bytes,
    timeout_seconds: int,
    max_response_bytes: int,
    cancel_event: threading.Event | None = None,
    deadline_monotonic: float | None = None,
) -> JobWorkerResult:
    """Launch attested caller argv suspended; assign job, then resume and exchange.

    The deadline starts at entry, including setup, and is never reset. Managed
    child work is bounded, followed by one shared 2-second cleanup allowance.
    Synchronous bootstrap OS calls are not hard-real-time; guards prevent a late
    suspended child from executing. Use an outer owned supervisor to bound hashes
    and nested launch calls. Missing ownership raises; no unowned fallback.
    """
    entered = time.monotonic()
    if os.name != "nt":
        raise RuntimeError("Owned research workers require Windows.")
    if (
        not isinstance(argv, tuple)
        or not argv
        or any(not isinstance(argument, str) or "\0" in argument for argument in argv)
        or not Path(argv[0]).is_absolute()
        or not Path(argv[0]).is_file()
        or not isinstance(cwd, Path)
        or not cwd.is_absolute()
        or not cwd.is_dir()
        or type(timeout_seconds) is not int
        or not 1 <= timeout_seconds <= 300
        or type(max_response_bytes) is not int
        or not 1024 <= max_response_bytes <= 1024**2
        or not isinstance(payload, bytes)
        or len(payload) > 1024**2
        or (
            deadline_monotonic is not None
            and (
                isinstance(deadline_monotonic, bool)
                or not isinstance(deadline_monotonic, (float, int))
                or not math.isfinite(deadline_monotonic)
            )
        )
        or any(
            not isinstance(key, str)
            or not isinstance(value, str)
            or "\0" in key
            or "\0" in value
            for key, value in env.items()
        )
    ):
        raise ValueError("Invalid bounded owned worker launch.")
    deadline = min(
        entered + timeout_seconds,
        entered + timeout_seconds if deadline_monotonic is None else deadline_monotonic,
    )
    phases: list[tuple[str, float]] = []

    def stopped() -> str | None:
        if cancel_event is not None and cancel_event.is_set():
            return "cancelled"
        if time.monotonic() >= deadline:
            return "timeout"
        return None

    def check_stop() -> None:
        reason = stopped()
        if reason:
            raise _ManagedStop(reason)

    initial_stop = stopped()
    if initial_stop:
        return JobWorkerResult(initial_stop, None, b"", b"", time.monotonic() - entered)
    job = ServerJob()
    process = None
    readers: list[threading.Thread] = []
    stdout = bytearray()
    stderr = bytearray()
    exceeded = threading.Event()
    io_failed = threading.Event()
    status = "failed"
    cleanup_deadline: float | None = None

    def collect(stream, target: bytearray, limit: int) -> None:
        try:
            while chunk := stream.read(4096):
                remaining = limit - len(target)
                target.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    exceeded.set()
                    return
        except OSError:
            io_failed.set()
        finally:
            stream.close()

    try:
        phase_start = time.monotonic()
        process = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            bufsize=0,
            creationflags=0x00000004 | subprocess.CREATE_NO_WINDOW,  # CREATE_SUSPENDED
        )
        phases.append(("launch", time.monotonic() - phase_start))
        check_stop()
        phase_start = time.monotonic()
        job.assign(int(process._handle))
        phases.append(("assignment", time.monotonic() - phase_start))
        check_stop()
        phase_start = time.monotonic()
        _resume_owned_process(process.pid)
        phases.append(("resume", time.monotonic() - phase_start))
        check_stop()
        for stream, target, limit in (
            (process.stdout, stdout, max_response_bytes),
            (process.stderr, stderr, 64 * 1024),
        ):
            thread = threading.Thread(
                target=collect, args=(stream, target, limit), daemon=True
            )
            thread.start()
            readers.append(thread)

        def send() -> None:
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = process.stdin.write(remaining)
                    if not written:
                        raise OSError("Owned worker input pipe closed.")
                    remaining = remaining[written:]
            except (OSError, ValueError):
                io_failed.set()
            finally:
                process.stdin.close()

        sender = threading.Thread(target=send, daemon=True)
        sender.start()
        readers.append(sender)
        phases.append(("setup_total", time.monotonic() - entered))
        execution_start = time.monotonic()
        status = "succeeded"
        while True:
            if cancel_event is not None and cancel_event.is_set():
                status = "cancelled"
                break
            if exceeded.is_set():
                status = "output-limit"
                break
            if time.monotonic() >= deadline:
                status = "timeout"
                break
            if process.poll() is not None:
                status = "succeeded" if process.returncode == 0 else "failed"
                break
            time.sleep(0.01)
        phases.append(("execution", time.monotonic() - execution_start))
    except _ManagedStop as stop:
        status = stop.status
    finally:
        cleanup_start = time.monotonic()
        if cleanup_deadline is None:
            cleanup_deadline = cleanup_start + CLEANUP_ALLOWANCE_SECONDS
        cleanup_error: OSError | RuntimeError | None = None
        try:
            job.terminate_and_wait(cleanup_deadline)
        except (OSError, RuntimeError) as error:
            # An ownership diagnostic must not skip exact-primary/pipe cleanup.
            # Preserve failure rather than turning it into a timeout success.
            cleanup_error = error
        if process is not None and process.poll() is None:
            process.terminate()  # Exact handle, including suspended assignment failures.
            process.wait(timeout=max(0.001, cleanup_deadline - time.monotonic()))
        if status in ("timeout", "cancelled", "output-limit", "failed"):
            _cancel_owned_pipe_io(readers)
        for thread in readers:
            thread.join(timeout=max(0, cleanup_deadline - time.monotonic()))
        if any(thread.is_alive() for thread in readers):
            raise RuntimeError(
                "Owned worker pipe did not stop within cleanup allowance."
            )
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        phases.append(("cleanup", time.monotonic() - cleanup_start))
        if cleanup_error is not None:
            raise cleanup_error
    if exceeded.is_set():
        status = "output-limit"
    elif io_failed.is_set() and status == "succeeded":
        status = "failed"
    return JobWorkerResult(
        status,
        None if process is None else process.returncode,
        bytes(stdout),
        bytes(stderr),
        time.monotonic() - entered,
        tuple(phases),
    )
