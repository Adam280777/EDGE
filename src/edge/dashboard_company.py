"""Local company/watchlist workspace with no research or provider requests."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from edge.journal import JournalInputError, JournalNotFoundError, JournalStorageError
from edge.journal_store import JournalStore
from edge.watchlist import (
    WatchlistDraft,
    WatchlistInputError,
    WatchlistNotFoundError,
    WatchlistStorageError,
    WatchlistSummary,
    validate_draft,
)
from edge.watchlist_store import WatchlistStore

PAGE_SIZE = 10
SNAPSHOT_BATCH_SIZE = 100
NAVIGATION_INTENT_KEY = "edge_navigation_request"


def _snapshot(store: WatchlistStore, key: str) -> tuple[WatchlistSummary, ...]:
    existing = st.session_state.get(key)
    if isinstance(existing, tuple) and all(
        isinstance(item, WatchlistSummary) for item in existing
    ):
        return existing
    summaries = []
    offset = 0
    while True:
        batch = store.list_entries(limit=SNAPSHOT_BATCH_SIZE, offset=offset)
        summaries.extend(batch)
        if len(batch) < SNAPSHOT_BATCH_SIZE:
            break
        offset += SNAPSHOT_BATCH_SIZE
    result = tuple(summaries)
    st.session_state[key] = result
    return result


def _create(store: WatchlistStore, key: str, snapshot_key: str) -> None:
    st.subheader("Add a user-declared company")
    st.caption(
        "Ticker and company name are your labels, not verified security identity. "
        "Only Save watchlist item writes a new immutable local note. No market data, "
        "provider requests, financial calculations, or broker actions."
    )
    with st.form(f"{key}_form", enter_to_submit=False):
        symbol = st.text_input(
            "Ticker / user-declared; identity unverified",
            max_chars=32,
            key=f"{key}_symbol",
        )
        company_name = st.text_input(
            "Company name / user-declared",
            max_chars=200,
            key=f"{key}_company",
        )
        notes = st.text_area("Local notes", max_chars=10000, key=f"{key}_notes")
        horizon_label = st.selectbox(
            "Horizon / optional",
            ["Not specified", "Both", "Swing", "Long-term"],
            key=f"{key}_horizon",
        )
        submitted = st.form_submit_button("Save watchlist item", type="primary")

    if submitted:
        st.session_state.pop(f"{key}_error", None)
        horizon = {
            "Not specified": None,
            "Both": "both",
            "Swing": "swing",
            "Long-term": "longterm",
        }[horizon_label]
        try:
            store.create(
                validate_draft(
                    WatchlistDraft(
                        symbol=symbol,
                        company_name=company_name,
                        notes=notes,
                        horizon=horizon,
                    )
                )
            )
        except WatchlistInputError:
            st.session_state[f"{key}_error"] = (
                "Not saved: check the required ticker/company fields, ASCII ticker "
                "format, exact horizon choice, and text limits."
            )
        except (WatchlistStorageError, OSError):
            st.session_state[f"{key}_error"] = (
                "Save failed: local watchlist storage is unavailable or invalid. "
                "Nothing was reset; check the database before explicitly trying again."
            )
        else:
            st.session_state.pop(snapshot_key, None)
            st.session_state[f"{key}_page"] = 0
            st.session_state[f"{key}_saved"] = True
    if st.session_state.get(f"{key}_error"):
        st.error(st.session_state[f"{key}_error"])
    elif st.session_state.pop(f"{key}_saved", False):
        st.success("Watchlist note saved locally.")


def _browse(
    store: WatchlistStore,
    key: str,
    snapshot_key: str,
) -> tuple[WatchlistSummary, ...]:
    st.subheader("Saved watchlist notes")
    refresh, _ = st.columns(2)
    if refresh.button("Refresh saved list", key=f"{key}_refresh"):
        st.session_state.pop(snapshot_key, None)
        st.session_state[f"{key}_page"] = 0
    items = _snapshot(store, snapshot_key)
    page_key = f"{key}_page"
    page = st.session_state.get(page_key, 0)
    if type(page) is not int or page < 0:
        page = 0
    page_count = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, page_count - 1)
    st.session_state[page_key] = page
    start = page * PAGE_SIZE
    visible = items[start : start + PAGE_SIZE]
    st.caption(f"Saved-list snapshot · Page {page + 1} of {page_count}")
    previous, following = st.columns(2)
    if previous.button("Previous page", disabled=page == 0, key=f"{key}_previous"):
        st.session_state[page_key] = page - 1
        st.rerun()
    if following.button(
        "Next page", disabled=page + 1 >= page_count, key=f"{key}_next"
    ):
        st.session_state[page_key] = page + 1
        st.rerun()
    if not items:
        st.info(
            "No local watchlist notes yet. Add one above; no file is created by reading."
        )
        return items
    for index, item in enumerate(visible, start=start + 1):
        st.text(
            f"Entry {index} · {item.created_at.isoformat()}\n"
            f"Ticker / unverified: {item.symbol}\n"
            f"Company / user-declared: {item.company_name}"
        )
    return items


def _request_navigation(target: str, symbol: str, key: str) -> None:
    if st.button(target, key=key):
        st.session_state[NAVIGATION_INTENT_KEY] = {
            "target": target,
            "symbol": symbol,
        }
        st.rerun()


def _journal_matches(data_root: Path, symbol: str) -> list[tuple[str, str, str]]:
    """Return saved journal notes matching only the locally declared ticker string."""
    journal = JournalStore(data_root)
    matches = []
    offset = 0
    while True:
        batch = journal.list_entries(limit=SNAPSHOT_BATCH_SIZE, offset=offset)
        for summary in batch:
            if summary.symbol == symbol:
                entry = journal.get(summary.entry_id)
                matches.append(
                    (
                        entry.entry_id,
                        entry.created_at.isoformat(),
                        entry.draft.title,
                    )
                )
        if len(batch) < SNAPSHOT_BATCH_SIZE:
            return matches
        offset += SNAPSHOT_BATCH_SIZE


def _company_view(
    store: WatchlistStore,
    items: tuple[WatchlistSummary, ...],
    key: str,
    data_root: Path,
) -> None:
    st.subheader("Company view · local notes only")
    if not items:
        st.info("No saved company notes. Open Watchlist to add a user-declared company.")
        def open_watchlist():
            st.session_state[f"{key}_workspace"] = "Watchlist"
        st.button("Go to Watchlist", key=f"{key}_empty_watchlist", on_click=open_watchlist)
        return
    labels = {
        item.watchlist_id: f"Company entry {index}"
        for index, item in enumerate(items, start=1)
    }
    selected_id = st.selectbox(
        "Choose a saved company entry",
        list(labels),
        format_func=labels.__getitem__,
        key=f"{key}_company_selection",
    )
    try:
        selected = store.get(selected_id)
        related = [
            store.get(item.watchlist_id)
            for item in items
            if item.symbol == selected.draft.symbol
        ]
    except (
        WatchlistNotFoundError,
        WatchlistInputError,
        WatchlistStorageError,
        OSError,
    ):
        st.error(
            "Company view could not safely read the saved watchlist. "
            "No records were reset or changed."
        )
        return

    draft = selected.draft
    st.warning(
        "USER-DECLARED COMPANY LABEL — ticker and company identity are unverified. "
        "This view contains only local notes and matching journal labels; it is not "
        "company research or a recommendation."
    )
    st.text(f"Ticker / unverified: {draft.symbol}")
    st.text(f"Company / user-declared: {draft.company_name}")
    st.text(f"Local notes with this ticker label: {len(related)}")
    for index, entry in enumerate(related, start=1):
        st.text(
            f"Watchlist note {index} · {entry.created_at.isoformat()}\n"
            f"Company / user-declared: {entry.draft.company_name}\n"
            f"Horizon / user-declared: {entry.draft.horizon or 'Not specified'}\n"
            f"Notes:\n{entry.draft.notes or 'None supplied'}"
        )

    _request_navigation(
        "SEC filing research",
        draft.symbol,
        key=f"{key}_sec_navigation",
    )
    _request_navigation(
        "Research journal",
        draft.symbol,
        key=f"{key}_journal_navigation",
    )
    st.caption(
        "Navigation only. The SEC page may use this ticker as an unverified prefill; "
        "no filing lookup or provider request is made here."
    )
    st.subheader("Research journal notes · ticker-label matches only")
    st.caption(
        "A matching ticker string does not establish company or security identity. "
        "No journal records are created, edited, or verified here."
    )
    try:
        matches = _journal_matches(data_root, draft.symbol)
    except (JournalInputError, JournalNotFoundError, JournalStorageError, OSError):
        st.info("Existing journal notes could not be safely read; no data was changed.")
        return
    if not matches:
        st.info("No existing journal entries match this declared ticker label.")
    for entry_id, created_at, title in matches:
        st.text(
            f"Journal entry {entry_id} · {created_at}\nTitle / user-authored: {title}"
        )


def render(data_root: Path) -> None:
    """Render the local company/watchlist workspace without external research."""
    st.header("Company / watchlist workspace")
    st.warning(
        "LOCAL USER-DECLARED NOTES — ticker and company identity are unverified. "
        "No company facts, market data, prices, financial calculations, provider "
        "requests, or broker actions are provided."
    )
    store = WatchlistStore(data_root)
    root_key = str(store.root)
    key = f"edge_company_{root_key}"
    snapshot_key = f"{key}_snapshot"
    workspace = st.radio(
        "Workspace",
        ["Watchlist", "Company view"],
        horizontal=True,
        key=f"{key}_workspace",
    )
    try:
        if workspace == "Watchlist":
            _create(store, key, snapshot_key)
            _browse(store, key, snapshot_key)
        else:
            items = _snapshot(store, snapshot_key)
            _company_view(store, items, key, data_root)
    except (WatchlistInputError, WatchlistStorageError, OSError):
        st.error(
            "Watchlist read failed: local storage is unavailable or invalid. "
            "Nothing was reset or migrated."
        )
