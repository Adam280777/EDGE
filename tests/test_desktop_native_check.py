"""Diagnostic HWND targeting tests without opening windows or accessing the vault."""

from pathlib import Path
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


def test_cache_changes_preserve_broad_failure_fact_without_hiding_user_mutation(tmp_path):
    cache = str(Path("userprofile/AppData/Local/Microsoft/Windows/Explorer/iconcache_16.db"))
    facts = check._cancellation_changes(tmp_path, {}, {cache: "synthetic-hash"})
    assert facts["public_cancellation_files_unchanged"] is False
    assert facts["owned_data_settings_and_existing_files_unchanged"] is True
    assert facts["known_explorer_cache_changes"] == [cache]
    assert facts["unexpected_file_changes"] == []
    data = str(Path("EDGE/data/portfolio.sqlite3"))
    settings = str(Path("EDGE/settings/provider-v1.json"))
    for path in (data, settings, "preexisting-sentinel", "profile/unknown.sqlite3"):
        facts = check._cancellation_changes(tmp_path, {path: "before"}, {path: "after", cache: "new"})
        assert facts["owned_data_settings_and_existing_files_unchanged"] is False
        assert path in facts["unexpected_file_changes"]


def test_hash_inventory_detects_same_size_content_change_and_deletion(tmp_path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_bytes(b"old")
    before = check._file_hashes(tmp_path)
    sentinel.write_bytes(b"new")
    after = check._file_hashes(tmp_path)
    assert before != after
    assert check._cancellation_changes(tmp_path, before, after)["unexpected_file_changes"] == ["sentinel"]
    sentinel.unlink()
    assert check._cancellation_changes(tmp_path, after, {})["unexpected_file_changes"] == ["sentinel"]


def test_unknown_explorer_database_not_allowlisted(tmp_path):
    path = str(Path("userprofile/AppData/Local/Microsoft/Windows/Explorer/user.sqlite3"))
    facts = check._cancellation_changes(tmp_path, {}, {path: "new"})
    assert facts["owned_data_settings_and_existing_files_unchanged"] is False
    assert facts["known_explorer_cache_changes"] == []
