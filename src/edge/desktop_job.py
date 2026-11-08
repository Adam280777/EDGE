"""Windows kernel ownership of the one server child (including crash cleanup)."""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_int64),
        ("PerJobUserTimeLimit", ctypes.c_int64),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint64)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


class ServerJob:
    """Close the job to terminate only assigned processes. Never kill by name."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("The desktop application requires Windows.")
        self._api = ctypes.WinDLL("kernel32", use_last_error=True)
        self._api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self._api.CreateJobObjectW.restype = wintypes.HANDLE
        self._api.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._api.SetInformationJobObject.restype = wintypes.BOOL
        self._api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self._api.AssignProcessToJobObject.restype = wintypes.BOOL
        self._api.CloseHandle.argtypes = [wintypes.HANDLE]
        self._api.CloseHandle.restype = wintypes.BOOL
        self._handle = self._api.CreateJobObjectW(None, None)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = (
            0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not self._api.SetInformationJobObject(
            self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process_handle: int) -> None:
        if not self._api.AssignProcessToJobObject(self._handle, process_handle):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self) -> None:
        if self._handle:
            self._api.CloseHandle(self._handle)
            self._handle = None

    def terminate_and_wait(self, deadline_monotonic: float) -> None:
        """Stop and await ALL exact job members before returning; one shared budget.

        Closing a job requests asynchronous termination. Retaining exact process
        handles and checking active membership avoids primary-only cleanup claims.
        """
        api = self._api
        api.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        api.QueryInformationJobObject.restype = wintypes.BOOL
        api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateJobObject.restype = wintypes.BOOL
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        api.TerminateProcess.restype = wintypes.BOOL
        api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry)]
        api.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        api.GetProcessTimes.restype = wintypes.BOOL
        handles: dict[int, int] = {}

        def created(handle: int) -> int:
            stamps = [wintypes.FILETIME() for _ in range(4)]
            if not api.GetProcessTimes(
                handle, *(ctypes.byref(stamp) for stamp in stamps)
            ):
                raise OSError("Cannot verify owned process creation time.")
            return (stamps[0].dwHighDateTime << 32) | stamps[0].dwLowDateTime

        def members() -> list[int]:
            capacity = 16
            while capacity <= 4096:
                buffer = ctypes.create_string_buffer(
                    8 + ctypes.sizeof(ctypes.c_size_t) * capacity
                )
                returned = wintypes.DWORD()
                if api.QueryInformationJobObject(
                    self._handle,
                    3,
                    buffer,
                    len(buffer),
                    ctypes.byref(returned),
                ):
                    count = ctypes.c_uint32.from_buffer(buffer, 4).value
                    array = (ctypes.c_size_t * count).from_buffer(buffer, 8)
                    return list(array)
                if ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                    raise OSError("Cannot enumerate owned job members.")
                capacity *= 2
            raise OSError("Owned job member count exceeds cleanup limit.")

        def retain(pids: list[int]) -> None:
            for pid in pids:
                if pid in handles:
                    continue
                # SYNCHRONIZE | TERMINATE | QUERY_LIMITED_INFORMATION
                handle = api.OpenProcess(0x00101001, False, pid)
                if handle:
                    handles[pid] = handle
                elif ctypes.get_last_error() != 87:  # PID already exited
                    raise OSError("Cannot retain owned job member handle.")

        def descendants(roots: set[int]) -> list[int]:
            # Windows Store Python's redirector/broker can create an executed
            # descendant outside native Job membership. Retain exact ancestry,
            # never image names, ports, arbitrary PIDs or unrelated processes.
            snapshot = api.CreateToolhelp32Snapshot(2, 0)  # TH32CS_SNAPPROCESS
            if snapshot == ctypes.c_void_p(-1).value:
                raise OSError("Cannot inspect owned process ancestry.")
            try:
                entry = _ProcessEntry()
                entry.dwSize = ctypes.sizeof(entry)
                pairs = []
                present = api.Process32FirstW(snapshot, ctypes.byref(entry))
                while present:
                    pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
                    present = api.Process32NextW(snapshot, ctypes.byref(entry))
                owned = set(roots)
                while True:
                    added = set()
                    for pid, parent in pairs:
                        if parent not in owned or pid in owned:
                            continue
                        retain([pid])
                        if pid not in handles:
                            continue  # Exited between snapshot and OpenProcess.
                        # A historical parent ID can refer to a recycled PID.
                        # Hold both identities and require child >= live parent.
                        if created(handles[pid]) < created(handles[parent]):
                            api.CloseHandle(handles.pop(pid))
                            continue
                        added.add(pid)
                    if not added:
                        return list(owned - roots)
                    owned.update(added)
                    if len(owned) > 4096:
                        raise OSError("Owned ancestry exceeds cleanup limit.")
            finally:
                api.CloseHandle(snapshot)

        try:
            if not self._handle:
                return
            retain(members())
            retain(descendants(set(handles)))
            if not api.TerminateJobObject(self._handle, 1):
                raise OSError("Cannot stop owned job members.")
            while True:
                active = members()
                retain(active)
                retain(descendants(set(handles)))
                waiting = False
                for handle in handles.values():
                    status = api.WaitForSingleObject(handle, 0)
                    if status == 258:
                        # Native Job termination may already be tearing down the
                        # process: TerminateProcess returns ACCESS_DENIED before
                        # its retained handle becomes signalled. Await that exact
                        # handle within the budget, never report early success.
                        if (
                            not api.TerminateProcess(handle, 1)
                            and ctypes.get_last_error() != 5
                            and api.WaitForSingleObject(handle, 0) != 0
                        ):
                            raise OSError("Cannot stop owned escaped descendant.")
                        waiting = True
                    elif status != 0:
                        raise OSError("Cannot confirm owned process termination.")
                if not active and not waiting:
                    return
                if time.monotonic() >= deadline_monotonic:
                    raise TimeoutError(
                        "Owned descendants exceeded shared cleanup allowance."
                    )
                time.sleep(min(0.01, max(0, deadline_monotonic - time.monotonic())))
        finally:
            for handle in handles.values():
                api.CloseHandle(handle)
            self.close()
