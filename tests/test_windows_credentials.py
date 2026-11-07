import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from edge.windows_credentials import (
    _DIALOG_LOCK,
    TARGET,
    CredentialError,
    CredentialStatus,
    WindowsCredentialManager,
    _Credential,
    _credential_dialog_worker,
)
from edge.windows_credentials import (
    _show_credential_dialog as credential_dialog,
)
from edge.windows_credentials import credential_dialog as spawn_dialog

SECRET = "offline-pasted-key-123"


class Api:
    def __init__(self):
        self.blob = (ctypes.c_ubyte * 1024)()
        self.credential = _Credential()
        self.calls = []
        self.ok = True

    def CredWriteW(self, pointer, flags):
        item = ctypes.cast(pointer, ctypes.POINTER(_Credential)).contents
        self.calls.append(("save", item.Type, item.TargetName, item.Persist))
        self.saved = ctypes.string_at(item.CredentialBlob, item.CredentialBlobSize)
        self.write_buffer = item.CredentialBlob
        return self.ok

    def CredReadW(self, target, kind, flags, pointer):
        self.calls.append(("read", target, kind, flags))
        if self.ok:
            ctypes.memmove(self.blob, SECRET.encode("utf-16-le"), len(SECRET) * 2)
            self.credential.CredentialBlob = self.blob
            self.credential.CredentialBlobSize = len(SECRET) * 2
            ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(_Credential)))[0] = (
                ctypes.pointer(self.credential)
            )
        return self.ok

    def CredFree(self, pointer):
        self.calls.append(("free",))

    def CredDeleteW(self, target, kind, flags):
        self.calls.append(("delete", target, kind, flags))
        return self.ok


def test_vault_fixed_target_laziness_save_read_free_delete():
    api = Api()
    vault = WindowsCredentialManager(api=api, last_error=lambda: 1168)
    assert api.calls == []
    vault.save(SECRET)
    assert api.saved.decode("utf-16-le") == SECRET
    assert api.calls == [("save", 1, TARGET, 2)]
    assert vault.read() == SECRET
    assert api.calls[-2:] == [("read", TARGET, 1, 0), ("free",)]
    vault.delete()
    assert api.calls[-1] == ("delete", TARGET, 1, 0)


def test_missing_vault_and_errors_are_safe():
    api = Api()
    api.ok = False
    vault = WindowsCredentialManager(api=api, last_error=lambda: 1168)
    assert vault.read() is None
    vault.delete()
    with pytest.raises(CredentialError) as error:
        vault.save(SECRET)
    assert SECRET not in str(error.value)


@pytest.mark.parametrize("size", [0, 1, 1025])
def test_invalid_read_still_frees(size):
    api = Api()
    original = api.CredReadW

    def invalid(*args):
        original(*args)
        api.credential.CredentialBlobSize = size
        return True

    api.CredReadW = invalid
    with pytest.raises(CredentialError):
        WindowsCredentialManager(api=api).read()
    assert api.calls[-1] == ("free",)


class TkFixture:
    def __init__(self, action="Save to Windows vault"):
        self.action = action
        self.buttons = {}
        self.destroyed = False
        self.entry_value = SECRET  # Simulates native paste, not browser input.
        self.entry_options = None

    def Tk(self):
        return self

    def __getattr__(self, name):
        return lambda *args, **kwargs: None

    def Label(self, *args, **kwargs):
        return self

    def Entry(self, *args, **kwargs):
        self.entry_options = kwargs
        return self

    def Button(self, *args, **kwargs):
        self.buttons[kwargs["text"]] = kwargs["command"]
        return self

    def get(self):
        return self.entry_value

    def delete(self, *args):
        self.entry_value = ""

    def destroy(self):
        self.destroyed = True

    def mainloop(self):
        self.buttons[self.action]()


def test_paste_masked_native_prompt_saves_directly_returns_only_status():
    tk = TkFixture()
    vault = Mock()
    status = credential_dialog(
        "save",
        manager=vault,
        tk_module=tk,
        messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: True),
    )
    assert status is CredentialStatus.SAVED
    assert tk.entry_options["show"] == "*"
    vault.save.assert_called_once_with(SECRET)
    vault.read.assert_not_called()
    assert tk.entry_value == "" and tk.destroyed
    assert SECRET not in repr(status)


def test_native_cancel_and_remove_confirmation_are_inert():
    vault = Mock()
    for action, choice in [("save", "Cancel"), ("remove", "unused")]:
        tk = TkFixture(choice)
        assert (
            credential_dialog(
                action,
                manager=vault,
                tk_module=tk,
                messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: False),
            )
            is CredentialStatus.CANCELLED
        )
        assert tk.destroyed
    vault.save.assert_not_called()
    vault.delete.assert_not_called()
    vault.read.assert_not_called()


def test_native_remove_and_failure_safe_cleanup():
    tk = TkFixture()
    vault = Mock()
    vault.delete.side_effect = RuntimeError(SECRET)
    assert (
        credential_dialog(
            "remove",
            manager=vault,
            tk_module=tk,
            messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: True),
        )
        is CredentialStatus.FAILED
    )
    assert tk.destroyed and not _DIALOG_LOCK.locked()


def test_native_serialization():
    _DIALOG_LOCK.acquire()
    try:
        assert spawn_dialog("save", context=Mock()) is CredentialStatus.BUSY
    finally:
        _DIALOG_LOCK.release()


def test_native_save_declined_confirmation_and_callback_suppression():
    tk = TkFixture()
    vault = Mock()
    assert (
        credential_dialog(
            "save",
            manager=vault,
            tk_module=tk,
            messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: False),
        )
        is CredentialStatus.CANCELLED
    )
    vault.save.assert_not_called()
    assert tk.entry_value == "" and tk.destroyed

    class CallbackError(TkFixture):
        def mainloop(self):
            self.report_callback_exception(RuntimeError, RuntimeError(SECRET), None)

    tk = CallbackError()
    assert (
        credential_dialog(
            "save",
            manager=vault,
            tk_module=tk,
            messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: True),
        )
        is CredentialStatus.FAILED
    )
    assert tk.destroyed
    vault.read.assert_not_called()


def test_native_remove_success_is_only_delete():
    vault = Mock()
    assert (
        credential_dialog(
            "remove",
            manager=vault,
            tk_module=TkFixture(),
            messagebox_module=SimpleNamespace(askyesno=lambda *a, **k: True),
        )
        is CredentialStatus.REMOVED
    )
    vault.delete.assert_called_once()
    vault.read.assert_not_called()
    vault.save.assert_not_called()


class Connection:
    def __init__(self, value=b"saved", ready=True, error=None):
        self.value, self.ready, self.error = value, ready, error
        self.closed = False
        self.sent = []

    def poll(self, timeout):
        self.timeout = timeout
        return self.ready

    def recv_bytes(self, maximum):
        assert maximum == 32
        if self.error:
            raise self.error
        if len(self.value) > maximum:
            raise OSError("oversize")
        return self.value

    def send_bytes(self, value):
        self.sent.append(value)
        if self.error:
            raise self.error

    def close(self):
        self.closed = True


class Process:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = self.closed = self.terminated = self.killed = False
        self.alive = False
        self.start_error = None
        self.stubborn = False
        self.joins = []

    def start(self):
        if self.start_error:
            raise self.start_error
        self.started = True

    def join(self, timeout):
        assert self.started
        self.joins.append(timeout)

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        if not self.stubborn:
            self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def close(self):
        self.closed = True


class Context:
    def __init__(self, value=b"saved", ready=True, error=None):
        self.receiver = Connection(value, ready, error)
        self.sender = Connection()
        self.process = Process()

    def Pipe(self, *, duplex):
        assert duplex is False
        return self.receiver, self.sender

    def Process(self, **kwargs):
        self.process.kwargs = kwargs
        return self.process


@pytest.mark.parametrize(
    "action,value,expected",
    [
        ("save", b"saved", CredentialStatus.SAVED),
        ("remove", b"removed", CredentialStatus.REMOVED),
        ("save", b"cancelled", CredentialStatus.CANCELLED),
        ("save", b"removed", CredentialStatus.FAILED),
        ("save", b"unavailable", CredentialStatus.UNAVAILABLE),
        ("save", b"failed", CredentialStatus.FAILED),
        ("save", b"busy", CredentialStatus.FAILED),
        ("save", SECRET.encode(), CredentialStatus.FAILED),
        ("save", b"x" * 33, CredentialStatus.FAILED),
    ],
)
def test_spawn_status_only_fixed_action_args_cleanup(action, value, expected):
    context = Context(value)
    assert spawn_dialog(action, context=context) is expected
    assert context.process.kwargs == {
        "target": _credential_dialog_worker,
        "args": (action, context.sender),
        "daemon": True,
    }
    assert SECRET not in repr(context.process.kwargs)
    assert context.receiver.timeout == 180
    assert context.receiver.closed and context.sender.closed
    assert context.process.closed
    assert not _DIALOG_LOCK.locked()


@pytest.mark.parametrize(
    "ready,error",
    [
        (False, None),
        (True, EOFError()),
        (True, OSError(SECRET)),
    ],
)
def test_spawn_timeout_eof_owned_child_termination(ready, error):
    context = Context(ready=ready, error=error)
    context.process.alive = True
    assert (
        spawn_dialog("save", context=context, timeout_seconds=1)
        is CredentialStatus.FAILED
    )
    assert context.process.terminated and context.process.closed
    assert context.process.joins == [1, 2]
    assert context.receiver.closed and context.sender.closed
    assert not _DIALOG_LOCK.locked()


def test_spawn_stubborn_child_killed_and_start_failure_not_joined():
    context = Context(ready=False)
    context.process.alive = context.process.stubborn = True
    assert spawn_dialog("save", context=context) is CredentialStatus.FAILED
    assert context.process.killed and context.process.closed
    assert context.process.joins == [1, 2, 2]
    context = Context()
    context.process.start_error = RuntimeError(SECRET)
    assert spawn_dialog("save", context=context) is CredentialStatus.FAILED
    assert context.process.joins == []
    assert context.receiver.closed and context.sender.closed
    assert context.process.closed and not _DIALOG_LOCK.locked()


def test_worker_only_status_bytes_no_pickled_key_or_exception(monkeypatch):
    local = Mock(return_value=CredentialStatus.SAVED)
    monkeypatch.setattr("edge.windows_credentials._show_credential_dialog", local)
    connection = Connection()
    _credential_dialog_worker("save", connection)
    local.assert_called_once_with("save")
    assert connection.sent == [b"saved"] and connection.closed
    local.side_effect = RuntimeError(SECRET)
    connection = Connection()
    _credential_dialog_worker("save", connection)
    assert connection.sent == [b"failed"] and connection.closed
    assert SECRET.encode() not in connection.sent
    connection = Connection(error=BrokenPipeError(SECRET))
    _credential_dialog_worker("remove", connection)
    assert connection.closed


def test_real_path_selects_spawn_context_but_never_real_vault(monkeypatch):
    context = Context()
    factory = Mock(return_value=context)
    monkeypatch.setattr("edge.windows_credentials.multiprocessing.get_context", factory)
    monkeypatch.setattr("edge.windows_credentials.sys.platform", "win32")
    assert spawn_dialog("save") is CredentialStatus.SAVED
    factory.assert_called_once_with("spawn")


def test_native_tk_refuses_nonmain_thread_without_creation():
    tk = TkFixture()
    tk.Tk = Mock()
    result = []
    worker = threading.Thread(
        target=lambda: result.append(credential_dialog("save", tk_module=tk))
    )
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert result == [CredentialStatus.UNAVAILABLE]
    tk.Tk.assert_not_called()
