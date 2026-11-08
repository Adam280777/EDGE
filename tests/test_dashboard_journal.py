"""Actual app-entry journal workflows against isolated local storage, offline."""

import json
import socket
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

pytest.importorskip("streamlit")
import streamlit as st
from test_dashboard import dashboard, text

from edge import dashboard_journal, simulation_service
from edge.journal import (
    JournalDraft,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    export_entry_json,
)
from edge.journal_store import JournalStore


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Journal workflows must not invoke providers, engines or network")

    import edge.data

    monkeypatch.setattr(edge.data, "lookup_sec_filings", forbidden)
    monkeypatch.setattr(simulation_service, "run", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    real_connect = socket.socket.connect

    def loopback_only(sock, address):
        # Windows asyncio creates an internal loopback socket pair.
        if not (isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1")):
            forbidden()
        return real_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)


def key(root, horizon="swing"):
    return f"edge_journal_{root.resolve()}_{horizon}"


def journal(root):
    app = dashboard(root)
    app.radio(key="edge_workspace").set_value("Research journal").run()
    assert not app.exception
    return app


def new_entry(app, root, horizon="swing"):
    app.radio(key=f"{key(root, horizon)}_task").set_value("New entry").run()
    assert not app.exception
    return app


def fill(app, root, horizon="swing", **overrides):
    prefix = key(root, horizon)
    values = {
        "title": "User test note",
        "symbol": "test.l",
        "company": "Unverified test company",
        "date": "2024-01-02",
        "thesis": "User opinion, not verified evidence.",
    } | overrides
    for field, value in values.items():
        widgets = (
            app.text_area
            if field
            in ("thesis", "bull_case", "bear_case", "catalysts", "invalidation")
            else app.text_input
        )
        widgets(key=f"{prefix}_{field}").set_value(value)
    return app


def save(app):
    next(b for b in app.button if b.label == "Save new entry").click().run()
    assert not app.exception
    return app


def all_text(app):
    return text(app) + "\n" + "\n".join(str(item.value) for item in app.text)


def test_startup_navigation_and_unsubmitted_draft_never_write(tmp_path):
    root = tmp_path / "not-created"
    app = journal(root)
    assert app.radio(key="edge_workspace").options == [
        "HOME · Command center",
        "DSC · Discovery",
        "WCH · Watchlist",
        "DEC · Decisions",
        "SEC · SEC filings",
        "JRN · Journal",
        "LAB · Research lab",
        "PRT · Portfolio",
        "MON · Monitoring",
        "ENG · Engine",
        "PRV · Providers",
        "BAK · Recovery",
        "APP · Bundle recovery",
    ]
    assert app.radio(key="edge_workspace").value == "Research journal"
    assert "No saved entries" in all_text(app)
    assert "USER-AUTHORED NOTES" in all_text(app)
    assert not root.exists()
    app = fill(new_entry(app, root), root)
    app.run()
    assert not app.exception
    assert not app.error
    form = app.get("form")[0]
    assert form.proto.form.enter_to_submit is False
    app.radio(key="edge_journal_horizon").set_value("Long-term").run()
    assert "No saved entries" in all_text(app)
    app.radio(key="edge_workspace").set_value("SEC filing research").run()
    app.radio(key="edge_workspace").set_value("Engine acceptance").run()
    assert not app.exception
    assert not root.exists()


def test_create_snapshot_export_rerun_navigation_and_new_session(tmp_path, monkeypatch):
    download = Mock(wraps=st.download_button)
    monkeypatch.setattr(st, "download_button", download)
    app = save(fill(new_entry(journal(tmp_path), tmp_path), tmp_path))
    entries = JournalStore(tmp_path).list_entries()
    assert len(entries) == 1
    saved = JournalStore(tmp_path).get(entries[0].entry_id)
    assert saved.draft.symbol == "TEST.L"
    assert saved.provenance == "user-authored"
    assert "Created (UTC):" in all_text(app)
    assert "Research date (user-declared): 2024-01-02" in all_text(app)
    assert "Not supplied — no citations recorded." in all_text(app)
    assert "Not supplied" in all_text(app)
    assert len(app.success) == 1
    kwargs = download.call_args.kwargs
    assert kwargs["on_click"] == "ignore"
    assert app.get("download_button")[0].proto.ignore_rerun
    assert kwargs["mime"] == "application/json"
    assert kwargs["data"] == export_entry_json(saved)
    document = json.loads(kwargs["data"])
    assert document["entry"]["draft"]["sources"] == []
    assert document["entry"]["entry_id"] == saved.entry_id
    assert sorted(p.name for p in tmp_path.iterdir()) == ["research-journal.sqlite3"]
    before = (tmp_path / "research-journal.sqlite3").read_bytes()

    app.text_input(key=f"{key(tmp_path)}_title").set_value("Unsaved replacement").run()
    app.run()
    assert not app.success  # A rerun never presents a new save.
    assert saved.draft.title in all_text(app)
    assert "Unsaved replacement" not in "\n".join(item.value for item in app.text)
    assert download.call_args.kwargs["data"] == export_entry_json(saved)
    app.radio(key="edge_workspace").set_value("Engine acceptance").run()
    app.radio(key="edge_workspace").set_value("Research journal").run()
    fresh = journal(tmp_path)
    assert not fresh.exception
    assert saved.entry_id in all_text(fresh)
    assert len(JournalStore(tmp_path).list_entries()) == 1
    assert (tmp_path / "research-journal.sqlite3").read_bytes() == before


def test_horizons_separate_form_list_and_snapshot(tmp_path):
    app = save(fill(new_entry(journal(tmp_path), tmp_path), tmp_path))
    swing = JournalStore(tmp_path).list_entries(horizon="swing")[0]
    app.radio(key="edge_journal_horizon").set_value("Long-term").run()
    assert "No saved entries" in all_text(app)
    assert swing.entry_id not in all_text(app)
    app = new_entry(app, tmp_path, "long-term")
    assert app.text_input(key=f"{key(tmp_path, 'long-term')}_title").value == ""
    save(fill(app, tmp_path, "long-term", title="Long thesis"))
    long = JournalStore(tmp_path).list_entries(horizon="long-term")
    assert len(long) == 1
    assert long[0].title == "Long thesis"
    app.radio(key="edge_journal_horizon").set_value("Swing").run()
    assert long[0].entry_id not in all_text(app)
    assert len(JournalStore(tmp_path).list_entries()) == 2


def test_literal_notes_sources_and_summary_no_markdown_or_html(tmp_path, monkeypatch):
    payload = "<script>alert(1)</script> **not bold** [click](https://example.test)"
    download = Mock(wraps=st.download_button)
    monkeypatch.setattr(st, "download_button", download)
    app = new_entry(journal(tmp_path), tmp_path)
    fill(
        app,
        tmp_path,
        title=payload,
        company=payload,
        thesis=payload,
        bull_case=payload,
        bear_case=payload,
        catalysts=payload,
        invalidation=payload,
        source_0_label=payload,
        source_0_url="https://example.test/source",
        source_0_date="2024-01-01",
        source_1_label="User print reference",
    )
    save(app)
    assert not app.error
    assert sum(payload == item.value for item in app.text) == 8
    for kind in ("markdown", "caption", "header", "subheader", "warning"):
        assert all(payload not in str(item.value) for item in app.get(kind))
    exported = json.loads(download.call_args.kwargs["data"])
    refs = exported["entry"]["draft"]["sources"]
    assert refs[0] == {
        "label": payload,
        "url": "https://example.test/source",
        "source_date": "2024-01-01",
    }
    assert refs[1]["url"] is None
    assert refs[1]["source_date"] is None
    app.radio(key=f"{key(tmp_path)}_task").set_value("Saved entries").run()
    assert any(payload in item.value for item in app.text)
    assert all(payload not in str(item.value) for item in app.markdown)


@pytest.mark.parametrize(
    "values",
    [
        {"title": " "},
        {"company": " "},
        {"symbol": "<b>"},
        {"thesis": ""},
        {"date": "20240102"},
        {"date": "2024-1-02"},
        {"date": "2024-02-30"},
        {"date": "2024-01-02T00:00:00"},
        {"date": (date.today() + timedelta(days=2)).isoformat()},
        {"source_0_url": "https://example.test/no-label"},
        {"source_0_label": "Unsafe", "source_0_url": "http://example.test"},
        {"source_0_label": "Unsafe", "source_0_url": "https://u:p@example.test"},
        {"source_0_label": "Unsafe", "source_0_url": "https://example.test/a b"},
        {"source_0_label": "Bad date", "source_0_date": "2024-02-30"},
        {"source_0_label": "Bad date", "source_0_date": "2999-01-01"},
    ],
)
def test_invalid_form_no_write_and_explicit_safe_error(tmp_path, values):
    root = tmp_path / "absent"
    app = save(fill(new_entry(journal(root), root), root, **values))
    assert "Entry not saved: invalid inputs" in all_text(app)
    assert not app.success
    assert not app.get("download_button")
    assert not root.exists()


@pytest.mark.parametrize("operation", ["create", "list_entries", "get"])
def test_storage_errors_safe_and_previous_identity_retained(
    tmp_path, monkeypatch, operation
):
    app = save(fill(new_entry(journal(tmp_path), tmp_path), tmp_path))
    saved = JournalStore(tmp_path).list_entries()[0]
    secret = "PRIVATE-NOTE SQL SELECT * FROM journal_entries"
    monkeypatch.setattr(
        JournalStore, operation, Mock(side_effect=JournalStorageError(secret))
    )
    if operation == "create":
        save(fill(app, tmp_path, title="Failed attempt"))
        assert saved.entry_id in all_text(app)
        assert "Previous saved entry below" in all_text(app)
    else:
        app.radio(key=f"{key(tmp_path)}_task").set_value("Saved entries").run()
        assert not app.get("download_button")
    assert not app.exception
    assert app.error
    assert not app.success
    assert secret not in all_text(app)
    assert "Failed attempt" not in "\n".join(item.value for item in app.text)


def test_invalid_after_success_never_relabels_previous_save(tmp_path):
    app = save(fill(new_entry(journal(tmp_path), tmp_path), tmp_path))
    saved = JournalStore(tmp_path).list_entries()[0]
    save(fill(app, tmp_path, title=""))
    assert not app.success
    assert app.error
    assert saved.entry_id in all_text(app)
    assert "Previous saved entry below" in all_text(app)
    assert len(JournalStore(tmp_path).list_entries()) == 1


def test_pagination_not_found_and_corrupt_storage(tmp_path, monkeypatch):
    store = JournalStore(tmp_path)
    for i in range(12):
        store.create(
            JournalDraft(
                "swing",
                "TEST",
                "User company",
                f"User entry {i}",
                date(2024, 1, 2),
                "User opinion",
                "",
                "",
                "",
                "",
            )
        )
    app = journal(tmp_path)
    prefix = key(tmp_path)
    assert len(app.selectbox(key=f"{prefix}_selection_0").options) == 10
    app.button(key=f"{prefix}_next").click().run()
    assert "Page 2" in all_text(app)
    assert len(app.selectbox(key=f"{prefix}_selection_1").options) == 2
    assert app.button(key=f"{prefix}_next").disabled
    app.button(key=f"{prefix}_previous").click().run()
    assert "Page 1" in all_text(app)
    assert len(store.list_entries()) == 12
    with monkeypatch.context() as patch:
        patch.setattr(store.__class__, "get", Mock(side_effect=JournalNotFoundError()))
        app.run()
        assert "Saved entry not found" in all_text(app)
        assert not app.get("download_button")
    path = tmp_path / "research-journal.sqlite3"
    path.write_bytes(b"corrupt journal")
    app.run()
    assert not app.exception
    assert "Journal read failed" in all_text(app)
    assert path.read_bytes() == b"corrupt journal"


def test_exact_date_parser_and_native_input_limits(tmp_path):
    assert dashboard_journal._date("2024-01-02") == date(2024, 1, 2)
    assert type(dashboard_journal._date("2024-01-02")) is date
    assert dashboard_journal._date("", optional=True) is None
    with pytest.raises(JournalInputError):
        dashboard_journal._date(" 2024-01-02 ")
    app = new_entry(journal(tmp_path), tmp_path)
    prefix = key(tmp_path)
    assert app.text_input(key=f"{prefix}_title").proto.max_chars == 200
    assert app.text_input(key=f"{prefix}_company").proto.max_chars == 200
    ticker = app.text_input(key=f"{prefix}_symbol")
    assert ticker.proto.max_chars == 32
    assert not ticker.proto.help
    assert ticker.label == "Ticker (required; identity unverified)"
    assert (
        "Ticker: up to 32 ASCII letters/digits or . ^ : / -; "
        "start with a letter or digit. Identity is unverified."
    ) in [item.value for item in app.caption]
    assert app.text_area(key=f"{prefix}_thesis").proto.max_chars == 10000
    assert app.text_input(key=f"{prefix}_source_0_url").proto.max_chars == 2048
    assert "up to 3" in all_text(app)


@pytest.mark.parametrize("task", ["New entry", "Saved entries"])
def test_export_failure_is_safe_read_only_and_retryable(tmp_path, monkeypatch, task):
    app = save(fill(new_entry(journal(tmp_path), tmp_path), tmp_path))
    store = JournalStore(tmp_path)
    saved = store.list_entries()[0]
    before = (tmp_path / "research-journal.sqlite3").read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(
            dashboard_journal,
            "export_entry_json",
            Mock(side_effect=JournalInputError("PRIVATE NOTE SQL payload")),
        )
        app.radio(key=f"{key(tmp_path)}_task").set_value(task).run()
        assert not app.exception
        assert app.error
        assert not app.success
        assert not app.get("download_button")
        assert saved.entry_id in all_text(app)
        assert "PRIVATE NOTE SQL payload" not in all_text(app)
    app.run()
    assert not app.exception
    assert not app.error
    assert len(app.get("download_button")) == 1
    assert len(store.list_entries()) == 1
    assert (tmp_path / "research-journal.sqlite3").read_bytes() == before
