"""Real Windows pre-execution job ownership; temporary fixtures, no providers."""

import os
import sys
import threading
import time

import pytest

import edge.desktop_research_job as worker

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows suspended Job ownership"
)


def launch(tmp_path, script, **kwargs):
    return worker.run_job_worker(
        (sys.executable, "-I", "-c", script),
        cwd=tmp_path,
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        payload=b"synthetic-input",
        timeout_seconds=kwargs.pop("timeout_seconds", 5),
        max_response_bytes=kwargs.pop("max_response_bytes", 1024),
        **kwargs,
    )


def test_actual_suspended_worker_payload_result(tmp_path):
    result = launch(tmp_path, "import sys; print(sys.stdin.buffer.read().decode())")
    assert result.status == "succeeded"
    assert result.stdout.strip() == b"synthetic-input"
    assert result.returncode == 0


def test_assignment_failure_prevents_first_worker_instruction(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-execute"

    def fail(self, handle):
        assert not marker.exists()
        raise OSError("synthetic assignment failure")

    monkeypatch.setattr(worker.ServerJob, "assign", fail)
    with pytest.raises(OSError):
        launch(
            tmp_path,
            f"from pathlib import Path; Path({str(marker)!r}).write_text('bad')",
        )
    assert not marker.exists()


def test_timeout_and_bounded_stdout_stderr(tmp_path):
    timed = launch(tmp_path, "import time; time.sleep(30)", timeout_seconds=1)
    assert timed.status == "timeout"
    output = launch(tmp_path, "import sys; sys.stdout.write('x'*100000)")
    assert output.status == "output-limit"
    assert len(output.stdout) == 1024
    errors = launch(tmp_path, "import sys; sys.stderr.write('x'*100000)")
    assert errors.status == "output-limit"
    assert len(errors.stderr) == 64 * 1024


def test_cancel_and_nested_parent_job(tmp_path):
    from edge.desktop_job import ServerJob

    cancel = threading.Event()
    timer = threading.Timer(0.2, cancel.set)
    timer.start()
    try:
        result = launch(tmp_path, "import time; time.sleep(30)", cancel_event=cancel)
        assert result.status == "cancelled"
    finally:
        timer.join()
    # Assign this helper launcher to an outer job in a separate process; its
    # descendant starts suspended and successfully enters the nested worker job.
    import subprocess

    code = (
        "from pathlib import Path; import os,sys;"
        "from edge.desktop_research_job import run_job_worker;"
        f"r=run_job_worker((sys.executable,'-I','-c','print(123)'),cwd=Path({str(tmp_path)!r}),"
        "env={'SYSTEMROOT':os.environ['SYSTEMROOT']},payload=b'',"
        "timeout_seconds=5,max_response_bytes=1024);"
        "assert r.status=='succeeded' and r.stdout.strip()==b'123'"
    )
    process = subprocess.Popen([sys.executable, "-c", code], creationflags=0x00000004)
    job = ServerJob()
    try:
        job.assign(int(process._handle))
        worker._resume_owned_process(process.pid)
        assert process.wait(timeout=15) == 0
    finally:
        job.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_invalid_launch_and_precancel_are_inert(tmp_path):
    with pytest.raises(ValueError):
        worker.run_job_worker(
            ("python",),
            cwd=tmp_path,
            env={},
            payload=b"",
            timeout_seconds=1,
            max_response_bytes=1024,
        )
    cancelled = threading.Event()
    cancelled.set()
    assert (
        launch(tmp_path, "raise AssertionError", cancel_event=cancelled).returncode
        is None
    )


def test_desktop_parent_job_close_kills_worker_grandchild_only(tmp_path):
    import ctypes
    import subprocess
    from ctypes import wintypes

    from edge.desktop_job import ServerJob

    marker = tmp_path / "grandchild-pid"
    worker_code = (
        "import subprocess,sys,time; from pathlib import Path;"
        "p=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(60)']);"
        f"Path({str(marker)!r}).write_text(str(p.pid));time.sleep(60)"
    )
    launcher_code = (
        "import os,sys;from pathlib import Path;"
        "from edge.desktop_research_job import run_job_worker;"
        f"run_job_worker((sys.executable,'-I','-c',{worker_code!r}),cwd=Path({str(tmp_path)!r}),"
        "env={'SYSTEMROOT':os.environ['SYSTEMROOT']},payload=b'',"
        "timeout_seconds=60,max_response_bytes=1024)"
    )
    launcher = subprocess.Popen(
        [sys.executable, "-c", launcher_code],
        creationflags=0x00000004,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time;time.sleep(60)"]
    )
    outer_job = ServerJob()
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = None
    try:
        outer_job.assign(int(launcher._handle))
        worker._resume_owned_process(launcher.pid)
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        pid = int(marker.read_text())
        handle = api.OpenProcess(
            0x00100000, False, pid
        )  # SYNCHRONIZE, exact fixture PID
        assert handle
        outer_job.close()
        assert launcher.wait(timeout=10) is not None
        assert api.WaitForSingleObject(handle, 10000) == 0
        assert unrelated.poll() is None
    finally:
        outer_job.close()
        if handle:
            api.CloseHandle(handle)
        for process in (launcher, unrelated):
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)


def test_absolute_deadline_not_reset_and_cleanup_phases_measured(tmp_path):
    started = time.monotonic()
    result = launch(
        tmp_path,
        "import time;time.sleep(30)",
        deadline_monotonic=started + 0.35,
        timeout_seconds=5,
    )
    elapsed = time.monotonic() - started
    assert result.status == "timeout"
    assert elapsed < 0.35 + worker.CLEANUP_ALLOWANCE_SECONDS
    assert 0 < result.elapsed_seconds <= elapsed
    phases = dict(result.phase_timings)
    assert {
        "launch",
        "assignment",
        "resume",
        "setup_total",
        "execution",
        "cleanup",
    } <= phases.keys()
    assert phases["execution"] < 1
    assert phases["cleanup"] <= worker.CLEANUP_ALLOWANCE_SECONDS


def test_expired_deadline_and_late_assignment_never_resume(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-run-late"
    result = launch(
        tmp_path,
        f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')",
        deadline_monotonic=time.monotonic() - 1,
    )
    assert result.status == "timeout"
    assert result.returncode is None
    real_assign = worker.ServerJob.assign

    def delayed_assign(self, handle):
        real_assign(self, handle)
        time.sleep(0.2)

    monkeypatch.setattr(worker.ServerJob, "assign", delayed_assign)
    result = launch(
        tmp_path,
        f"from pathlib import Path;Path({str(marker)!r}).write_text('bad')",
        deadline_monotonic=time.monotonic() + 0.1,
    )
    assert result.status == "timeout"
    assert not marker.exists()
    assert "resume" not in dict(result.phase_timings)


def test_supervisor_managed_hash_and_nested_launch_stalls_bounded(tmp_path):
    # Delays model an owned verifier's hash/launch work, not a hard-real-time
    # assertion about the host kernel's initial CreateProcess bootstrap.
    for stage in ("hash", "nested-launch"):
        marker = tmp_path / f"no-instruction-after-{stage}"
        script = (
            "import time;from pathlib import Path;"
            f"print({stage!r},flush=True);time.sleep(5);"
            f"Path({str(marker)!r}).write_text('bad')"
        )
        entered = time.monotonic()
        result = launch(
            tmp_path,
            script,
            timeout_seconds=1,
            deadline_monotonic=entered + 1,
        )
        assert result.status == "timeout"
        assert time.monotonic() - entered < 1 + worker.CLEANUP_ALLOWANCE_SECONDS
        assert not marker.exists()
        assert result.stdout.strip() == stage.encode()


def test_supervisor_blocked_pipe_descendants_reaped_with_shared_cleanup(tmp_path):
    # A descendant inherits stdout and never reads stdin. Closing the supervisor
    # job must release BOTH pipe readers and the potentially blocked input sender.
    script = (
        "import subprocess,sys,time;"
        "subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(30)']);"
        "time.sleep(30)"
    )
    entered = time.monotonic()
    result = worker.run_job_worker(
        (sys.executable, "-I", "-c", script),
        cwd=tmp_path,
        env={"SYSTEMROOT": os.environ["SYSTEMROOT"]},
        payload=b"x" * 1024**2,
        timeout_seconds=1,
        max_response_bytes=1024,
        deadline_monotonic=entered + 1,
    )
    assert result.status == "timeout"
    assert time.monotonic() - entered < 1 + worker.CLEANUP_ALLOWANCE_SECONDS


@pytest.mark.skipif(os.name != "nt", reason="Windows native cleanup semantics")
def test_job_cleanup_waits_after_native_termination_access_denied() -> None:
    """Model the observed teardown race; the real tree tests remain separate."""
    import ctypes
    from unittest.mock import MagicMock

    from edge.desktop_job import ServerJob

    api = MagicMock()
    queries = iter(([1234], [], []))

    def query(_job, _kind, buffer, _size, _returned):
        pids = next(queries)
        ctypes.c_uint32.from_buffer(buffer, 4).value = len(pids)
        for index, pid in enumerate(pids):
            ctypes.c_size_t.from_buffer(
                buffer, 8 + index * ctypes.sizeof(ctypes.c_size_t)
            ).value = pid
        return True

    def terminating(_handle, _exit_code):
        ctypes.set_last_error(5)  # Native teardown in progress, not yet signalled.
        return False

    api.QueryInformationJobObject.side_effect = query
    api.OpenProcess.return_value = 201
    api.CreateToolhelp32Snapshot.return_value = 301
    api.Process32FirstW.return_value = False
    api.TerminateJobObject.return_value = True
    api.TerminateProcess.side_effect = terminating
    api.WaitForSingleObject.side_effect = (258, 0)
    job = object.__new__(ServerJob)
    job._api = api
    job._handle = 99
    job.terminate_and_wait(time.monotonic() + 1)
    assert api.WaitForSingleObject.call_count == 2
    api.TerminateProcess.assert_called_once_with(201, 1)
    assert job._handle is None
