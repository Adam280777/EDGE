"""Shared desktop workspace routing tests, isolated from contribution modules."""

import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

pytest.importorskip("streamlit")
from test_dashboard import dashboard


@pytest.mark.parametrize("workspace,module_name", [
    ("Backup and recovery", "edge.dashboard_backup"),
    ("Provider settings", "edge.dashboard_provider"),
    ("Company watchlist", "edge.dashboard_company"),
])
def test_workspace_passes_original_data_root_only_when_selected(
    tmp_path, monkeypatch, workspace, module_name
):
    module = ModuleType(module_name)
    module.render = Mock()
    monkeypatch.setitem(sys.modules, module_name, module)
    app = dashboard(tmp_path)
    module.render.assert_not_called()
    app.radio(key="edge_workspace").set_value(workspace).run()
    assert not app.exception
    module.render.assert_called_once_with(tmp_path.resolve())
    assert list(tmp_path.iterdir()) == []


def test_watchlist_navigation_prefills_sec_without_lookup(tmp_path, monkeypatch):
    import edge.data

    lookup = Mock(side_effect=AssertionError("Navigation must not request data"))
    monkeypatch.setattr(edge.data, "lookup_sec_filings", lookup)
    app = dashboard(tmp_path)
    app.session_state["edge_navigation_request"] = {
        "target": "SEC filing research", "symbol": "test",
    }
    app.run()
    assert not app.exception
    assert app.radio(key="edge_workspace").value == "SEC filing research"
    assert app.text_input(key="edge_sec_ticker").value == "TEST"
    lookup.assert_not_called()
    assert "edge_navigation_request" not in app.session_state
    assert list(tmp_path.iterdir()) == []


def test_watchlist_navigation_prefills_journal_draft_without_save(tmp_path):
    app = dashboard(tmp_path)
    app.session_state["edge_navigation_request"] = {
        "target": "Research journal", "symbol": "test.l",
    }
    app.run()
    assert not app.exception
    prefix = f"edge_journal_{tmp_path.resolve()}_swing"
    assert app.radio(key=f"{prefix}_task").value == "New entry"
    assert app.text_input(key=f"{prefix}_symbol").value == "TEST.L"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("intent", [
    {"target": "https://example.test", "symbol": "TEST"},
    {"target": "SEC filing research", "symbol": "<script>"},
    {"target": "SEC filing research", "symbol": None},
    "not a routing intent",
])
def test_invalid_navigation_intents_are_discarded(tmp_path, intent):
    app = dashboard(tmp_path)
    app.session_state["edge_navigation_request"] = intent
    app.run()
    assert not app.exception
    assert app.radio(key="edge_workspace").value == "Engine acceptance"
    assert list(tmp_path.iterdir()) == []


def test_recovery_root_requires_explicit_session_open(tmp_path, monkeypatch):
    from edge import dashboard_journal

    render = Mock()
    monkeypatch.setattr(dashboard_journal, "render", render)
    recovered = tmp_path.parent / "separate-recovered-journal"
    app = dashboard(tmp_path)
    app.session_state["edge_restored_journal_root"] = recovered
    app.radio(key="edge_workspace").set_value("Research journal").run()
    render.assert_called_with(tmp_path.resolve())
    app.button(key="edge_open_recovered_journal").click().run()
    assert not app.exception
    render.assert_called_with(recovered)
    app.button(key="edge_return_original_journal").click().run()
    render.assert_called_with(tmp_path.resolve())
    assert not recovered.exists()
    assert list(tmp_path.iterdir()) == []


def test_backup_restore_handoff_keeps_original_root_until_open(tmp_path, monkeypatch):
    from datetime import date

    from edge import dashboard_journal, native_dialogs_backup
    from edge.journal import JournalDraft
    from edge.journal_backup import backup_journal
    from edge.journal_store import JournalStore

    original = tmp_path / "original"
    entry = JournalStore(original).create(
        JournalDraft(
            "swing", "NOTE", "User-declared", "Temporary routing test",
            date(2020, 1, 1), "User opinion only", "", "", "", "",
        )
    )
    archive = tmp_path / "journal.zip"
    backup_journal(original, archive)
    restored = tmp_path / "restored"
    before = (original / "research-journal.sqlite3").read_bytes()
    monkeypatch.setattr(
        native_dialogs_backup, "choose_backup_source", lambda: archive
    )
    monkeypatch.setattr(
        native_dialogs_backup, "choose_restore_destination", lambda: restored
    )
    render = Mock()
    monkeypatch.setattr(dashboard_journal, "render", render)
    app = dashboard(original)
    app.radio(key="edge_workspace").set_value("Backup and recovery").run()
    app.button(key="edge_backup_restore").click().run()
    assert not app.exception
    assert app.session_state["edge_restored_journal_root"] == restored
    assert "edge_open_journal_root" not in app.session_state
    assert JournalStore(restored).get(entry.entry_id) == entry
    app.radio(key="edge_workspace").set_value("Research journal").run()
    render.assert_called_with(original)
    assert app.button(key="edge_open_recovered_journal").label == "Open restored journal"
    app.button(key="edge_open_recovered_journal").click().run()
    render.assert_called_with(restored)
    app.button(key="edge_return_original_journal").click().run()
    render.assert_called_with(original)
    assert (original / "research-journal.sqlite3").read_bytes() == before


def test_company_actions_route_without_provider_or_journal_writes(tmp_path, monkeypatch):
    import edge.data
    from edge.watchlist import WatchlistDraft
    from edge.watchlist_store import WatchlistStore

    WatchlistStore(tmp_path).create(
        WatchlistDraft("TEST", "User-declared label", "User notes only")
    )
    lookup = Mock(side_effect=AssertionError("Company navigation must not fetch"))
    monkeypatch.setattr(edge.data, "lookup_sec_filings", lookup)
    app = dashboard(tmp_path)
    company_key = f"edge_company_{tmp_path.resolve()}"
    app.radio(key="edge_workspace").set_value("Company watchlist").run()
    app.radio(key=f"{company_key}_workspace").set_value("Company view").run()
    app.button(key=f"{company_key}_sec_navigation").click().run()
    assert not app.exception
    assert app.text_input(key="edge_sec_ticker").value == "TEST"
    lookup.assert_not_called()
    app.radio(key="edge_workspace").set_value("Company watchlist").run()
    app.radio(key=f"{company_key}_workspace").set_value("Company view").run()
    app.button(key=f"{company_key}_journal_navigation").click().run()
    prefix = f"edge_journal_{tmp_path.resolve()}_swing"
    assert not app.exception
    assert app.text_input(key=f"{prefix}_symbol").value == "TEST"
    assert not (tmp_path / "research-journal.sqlite3").exists()
