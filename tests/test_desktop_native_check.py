"""Diagnostic HWND targeting tests without opening windows or accessing the vault."""

from types import SimpleNamespace
from unittest.mock import Mock

from edge import desktop_native_check as check


def test_cancel_diagnostic_closes_only_owned_matching_visible_window(monkeypatch):
    posted = []
    user32 = SimpleNamespace(
        GetWindowThreadProcessId=Mock(
            side_effect=lambda hwnd, pointer: setattr(
                pointer._obj, "value", {1: 42, 2: 99, 3: 42}[hwnd]
            )
        ),
        GetWindowTextW=Mock(
            side_effect=lambda hwnd, text, size: setattr(
                text, "value", {1: "EDGE test", 2: "EDGE test", 3: "Unrelated"}[hwnd]
            )
        ),
        IsWindowVisible=Mock(return_value=True),
        PostMessageW=Mock(
            side_effect=lambda hwnd, *args: posted.append(hwnd) or True
        ),
        EnumWindows=Mock(side_effect=lambda callback, _: [callback(i, 0) for i in (1, 2, 3)]),
    )
    monkeypatch.setattr(check.ctypes, "WinDLL", lambda *a, **k: user32)
    monkeypatch.setattr(check.ctypes, "WINFUNCTYPE", lambda *a: lambda function: function)
    monkeypatch.setattr(
        check.multiprocessing, "active_children",
        lambda: [SimpleNamespace(pid=42)] if not posted else [],
    )

    def action():
        deadline = check.time.monotonic() + 2
        while not posted and check.time.monotonic() < deadline:
            check.time.sleep(.01)

    assert check._cancel_owned_popup(action, "EDGE test") == {
        "cancelled": True, "owned_dialog_closed": True, "owned_children_exited": True,
    }
    assert posted == [1]
    user32.PostMessageW.assert_called_once_with(1, 0x0010, 0, 0)


def test_diagnostic_does_not_claim_close_without_owned_popup(monkeypatch):
    user32 = SimpleNamespace(**{
        name: Mock() for name in (
            "GetWindowThreadProcessId", "GetWindowTextW", "IsWindowVisible",
            "PostMessageW", "EnumWindows",
        )
    })
    monkeypatch.setattr(check.ctypes, "WinDLL", lambda *a, **k: user32)
    monkeypatch.setattr(check.ctypes, "WINFUNCTYPE", lambda *a: lambda function: function)
    monkeypatch.setattr(check.multiprocessing, "active_children", list)
    result = check._cancel_owned_popup(lambda: None, "EDGE test")
    assert result["cancelled"] is True
    assert result["owned_dialog_closed"] is False
    user32.PostMessageW.assert_not_called()
