"""Offline Streamlit tests for the company/watchlist workspace."""

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

from edge.journal import JournalDraft
from edge.journal_store import JournalStore
from edge.watchlist import WatchlistDraft
from edge.watchlist_store import WatchlistStore


def workspace(data_root: Path) -> AppTest:
    script = (
        "from pathlib import Path\n"
        "from edge.dashboard_company import render\n"
        f"render(Path({str(data_root)!r}))\n"
    )
    return AppTest.from_string(script, default_timeout=60).run()


def _text(app: AppTest) -> str:
    return "\n".join(str(item.value) for item in app.text)


def test_startup_is_local_and_never_creates_either_database(tmp_path: Path) -> None:
    root = tmp_path / "not-created"
    app = workspace(root)
    assert not app.exception
    assert any("identity are unverified" in str(item.value) for item in app.warning)
    assert not root.exists()


def test_company_view_groups_local_notes_and_journal_ticker_matches(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    watchlist = WatchlistStore(root)
    first = watchlist.create(
        WatchlistDraft("abc", "First user label", "First literal note", "swing")
    )
    second = watchlist.create(
        WatchlistDraft("ABC", "Alternate user label", "Second literal note", "both")
    )
    journal = JournalStore(root)
    journal.create(
        JournalDraft(
            "swing",
            "ABC",
            "Company identity unverified",
            "Local thesis title",
            date.today(),
            "User-authored thesis only.",
            "",
            "",
            "",
            "",
        )
    )
    journal.create(
        JournalDraft(
            "swing",
            "OTHER",
            "Other label",
            "Should not match",
            date.today(),
            "Different ticker declaration.",
            "",
            "",
            "",
            "",
        )
    )

    app = workspace(root)
    key = f"edge_company_{root.resolve()}"
    app.radio(key=f"{key}_workspace").set_value("Company view").run()
    assert not app.exception
    assert "Local notes with this ticker label: 2" in _text(app)
    assert "First literal note" in _text(app)
    assert "Second literal note" in _text(app)
    assert "Local thesis title" in _text(app)
    assert "Should not match" not in _text(app)
    assert "ticker string does not establish company or security identity" in "\n".join(
        str(item.value) for item in app.caption
    )
    assert first.watchlist_id in {
        entry.watchlist_id for entry in watchlist.list_entries()
    }
    assert second.watchlist_id in {
        entry.watchlist_id for entry in watchlist.list_entries()
    }


def test_navigation_is_namespaced_session_intent_without_lookup(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    WatchlistStore(root).create(WatchlistDraft("abc", "Local company"))
    app = workspace(root)
    key = f"edge_company_{root.resolve()}"
    app.radio(key=f"{key}_workspace").set_value("Company view").run()
    app.button(key=f"{key}_sec_navigation").click().run()
    assert not app.exception
    assert app.session_state["edge_navigation_request"] == {
        "target": "SEC filing research",
        "symbol": "ABC",
    }
    assert not (root / "research-journal.sqlite3").exists()


def test_explicit_form_submit_creates_once_and_user_text_is_literal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    app = workspace(root)
    key = f"edge_company_{root.resolve()}"
    app.text_input(key=f"{key}_symbol").set_value("xy")
    app.text_input(key=f"{key}_company").set_value("<script>literal & label</script>")
    app.text_area(key=f"{key}_notes").set_value("**not markdown** <img src=x>")
    next(
        button for button in app.button if button.label == "Save watchlist item"
    ).click().run()
    assert not app.exception
    assert len(WatchlistStore(root).list_entries()) == 1
    assert "literal & label" in _text(app)
    assert not any(
        "literal & label" in str(item.value) or "not markdown" in str(item.value)
        for item in app.markdown
    )
    app.run()
    assert len(WatchlistStore(root).list_entries()) == 1


def test_no_direct_links_or_fabricated_company_facts(tmp_path: Path) -> None:
    root = tmp_path / "data"
    WatchlistStore(root).create(WatchlistDraft("abc", "User label", "Only my note"))
    app = workspace(root)
    key = f"edge_company_{root.resolve()}"
    app.radio(key=f"{key}_workspace").set_value("Company view").run()
    assert not app.exception
    assert "Only my note" in _text(app)
    assert not app.link_button
    assert not app.markdown
    assert all(
        "market data" not in str(item.value).lower()
        or "no market data" in str(item.value).lower()
        for item in app.caption
    )
