"""Local company/watchlist workspace with no research or provider requests.

Presentation only: counts and dates are taken from saved local notes. User text
(tickers, company names, notes, journal titles) is always rendered with
``st.text``; terminal chrome carries fixed labels only.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import streamlit as st

from edge import ui_charts as charts
from edge.journal import JournalInputError, JournalNotFoundError, JournalStorageError
from edge.journal_store import JournalStore
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    chip,
    empty,
    esc,
    fmt_time,
    html_block,
    panel,
    section_rule,
    tiles,
)
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
SUBFUNCTIONS = {"Watchlist": "LST · Watchlist", "Company view": "CMP · Company view"}
HORIZON_CHIPS = {
    "swing": ("SWING", "amber"),
    "longterm": ("LONG-TERM", "violet"),
    "both": ("BOTH", "cyan"),
    None: ("NO HORIZON", "muted"),
}
HORIZON_NAMES = {"swing": "Swing", "longterm": "Long-term", "both": "Both", None: "Not specified"}


def _card_head(code: str, title: str, horizon: object, meta: str = "") -> None:
    """Fixed-vocabulary card header; never carries user-authored text."""
    label, tone = HORIZON_CHIPS.get(horizon, HORIZON_CHIPS[None])
    html_block(
        '<div class="edge-pane-head">'
        f'<span class="edge-pane-code">{esc(code)}</span>'
        f'<span class="edge-pane-title">{esc(title)}</span>'
        '<span class="edge-pane-rule" aria-hidden="true"></span>'
        f"{chip(label, tone)}"
        + (f'<span class="edge-pane-meta">{esc(meta)}</span>' if meta else "")
        + "</div>"
    )


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


def _overview(items: tuple[WatchlistSummary, ...]) -> None:
    """KPI strip and saved-over-time curve from the saved-list snapshot only."""
    horizons = Counter(item.horizon for item in items)
    latest = max((item.created_at for item in items), default=None)
    tiles([
        Tile("Watchlist notes", str(len(items)), "Saved-list snapshot", "amber"),
        Tile("Distinct tickers", str(len({item.symbol for item in items})),
             "Exact label strings", "ink"),
        Tile("Swing", str(horizons.get("swing", 0)), "User-declared", "amber"),
        Tile("Long-term", str(horizons.get("longterm", 0)), "User-declared", "violet"),
        Tile("Both / unspecified",
             f"{horizons.get('both', 0)} / {horizons.get(None, 0)}", "User-declared", "cyan"),
        Tile("Last added", fmt_time(latest) if latest else UNAVAILABLE, "Local save time", "ink"),
    ], columns=6)
    if items:
        charts.show(
            charts.step_area(
                [{"when": item.created_at.isoformat(),
                  "series": HORIZON_NAMES.get(item.horizon, "Not specified")}
                 for item in items],
                title="Cumulative watchlist notes saved · by declared horizon",
                y_title="Notes",
            ),
            alt_text="Stepped cumulative count of saved watchlist notes over time per horizon",
            height=190,
        )


def _create(store: WatchlistStore, key: str, snapshot_key: str) -> None:
    with st.container(key="edge_box_company_add"):
        panel("ADD", "Add a user-declared company", badges=("manual",))
        st.caption(
            "Ticker and company name are your labels, not verified security identity. "
            "Only Save watchlist item writes a new immutable local note. No market data, "
            "provider requests, financial calculations, or broker actions."
        )
        with st.form(f"{key}_form", enter_to_submit=False):
            ticker_col, company_col, horizon_col = st.columns([1, 2, 1], gap="small")
            symbol = ticker_col.text_input(
                "Ticker / user-declared; identity unverified",
                max_chars=32,
                key=f"{key}_symbol",
            )
            company_name = company_col.text_input(
                "Company name / user-declared",
                max_chars=200,
                key=f"{key}_company",
            )
            horizon_label = horizon_col.selectbox(
                "Horizon / optional",
                ["Not specified", "Both", "Swing", "Long-term"],
                key=f"{key}_horizon",
            )
            notes = st.text_area("Local notes", max_chars=10000, key=f"{key}_notes")
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
    with st.container(key="edge_box_company_list"):
        panel("LST", "Saved watchlist notes", badges=("manual", "local"))
        refresh, previous, following, _ = st.columns([1.2, 1, 1, 2], gap="small")
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
        if previous.button("Previous page", disabled=page == 0, key=f"{key}_previous"):
            st.session_state[page_key] = page - 1
            st.rerun()
        if following.button(
            "Next page", disabled=page + 1 >= page_count, key=f"{key}_next"
        ):
            st.session_state[page_key] = page + 1
            st.rerun()
        st.caption(f"Saved-list snapshot · Page {page + 1} of {page_count}")
        if not items:
            st.info(
                "No local watchlist notes yet. Add one above; no file is created by reading."
            )
            return items
        columns = st.columns(2, gap="small")
        for index, item in enumerate(visible, start=start + 1):
            with columns[(index - start - 1) % 2], st.container(
                key=f"edge_box_company_card_{index}"
            ):
                _card_head(f"#{index}", "Watchlist entry", item.horizon,
                           meta=fmt_time(item.created_at))
                st.text(
                    f"Entry {index} · {item.created_at.isoformat()}\n"
                    f"Ticker / unverified: {item.symbol}\n"
                    f"Company / user-declared: {item.company_name}"
                )
    return items


def _request_navigation(target: str, symbol: str, key: str) -> None:
    if st.button(target, key=key, width="stretch"):
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
    if not items:
        with st.container(key="edge_box_company_view"):
            panel("CMP", "Company view · local notes only", badges=("manual",))
            st.info("No saved company notes. Open Watchlist to add a user-declared company.")

            def open_watchlist():
                st.session_state[f"{key}_workspace"] = "Watchlist"

            st.button("Go to Watchlist", key=f"{key}_empty_watchlist", on_click=open_watchlist)
        return
    labels = {
        item.watchlist_id: f"Company entry {index}"
        for index, item in enumerate(items, start=1)
    }
    with st.container(key="edge_box_company_view"):
        panel("CMP", "Company view · local notes only", badges=("manual", "local"))
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
        ident, actions = st.columns([2, 1], gap="small")
        with ident:
            st.text(f"Ticker / unverified: {draft.symbol}")
            st.text(f"Company / user-declared: {draft.company_name}")
            st.text(f"Local notes with this ticker label: {len(related)}")
        with actions:
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
    with st.container(key="edge_box_company_notes"):
        panel("NTS", "Watchlist notes with this ticker label", badges=("manual",))
        columns = st.columns(2, gap="small")
        for index, entry in enumerate(related, start=1):
            with columns[(index - 1) % 2], st.container(
                key=f"edge_box_company_note_{index}"
            ):
                _card_head(f"N{index}", "Watchlist note", entry.draft.horizon,
                           meta=fmt_time(entry.created_at))
                st.text(
                    f"Watchlist note {index} · {entry.created_at.isoformat()}\n"
                    f"Company / user-declared: {entry.draft.company_name}\n"
                    f"Horizon / user-declared: {entry.draft.horizon or 'Not specified'}\n"
                    f"Notes:\n{entry.draft.notes or 'None supplied'}"
                )
    with st.container(key="edge_box_company_journal"):
        panel("JRN", "Research journal notes · ticker-label matches only",
              badges=("manual", "local"))
        st.caption(
            "A matching ticker string does not establish company or security identity. "
            "No journal records are created, edited, or verified here."
        )
        try:
            matches = _journal_matches(data_root, draft.symbol)
        except (JournalInputError, JournalNotFoundError, JournalStorageError, OSError):
            st.info("Existing journal notes could not be safely read; no data was changed.")
            return
        events = [
            {"lane": "Watchlist notes", "when": entry.created_at.isoformat(),
             "label": f"Watchlist note {index}"}
            for index, entry in enumerate(related, start=1)
        ] + [
            {"lane": "Journal notes", "when": created_at, "label": "Journal entry"}
            for _entry_id, created_at, _title in matches
        ]
        charts.show(
            charts.timeline(events, title="Saved activity for this ticker label · local save time"),
            alt_text="Timeline of saved watchlist and journal notes for the selected ticker label",
        )
        if not matches:
            st.info("No existing journal entries match this declared ticker label.")
        for entry_id, created_at, title in matches:
            st.text(
                f"Journal entry {entry_id} · {created_at}\nTitle / user-authored: {title}"
            )


def _head() -> None:
    st.warning(
        "LOCAL USER-DECLARED NOTES ? ticker and company identity are unverified. "
        "No company facts, market data, prices, financial calculations, provider "
        "requests, or broker actions are provided."
    )


def _work(store: WatchlistStore, key: str, snapshot_key: str, data_root: Path) -> None:
    """Sub-function switcher and its forms/views stay together in one panel."""
    with st.container(key="edge_subnav_company"):
        section_rule("Sub-function")
        workspace = st.radio(
            "Workspace",
            list(SUBFUNCTIONS),
            horizontal=True,
            key=f"{key}_workspace",
            format_func=SUBFUNCTIONS.__getitem__,
            label_visibility="collapsed",
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


def _fill_overview(store: WatchlistStore, snapshot_key: str) -> None:
    try:
        _overview(_snapshot(store, snapshot_key))
    except (WatchlistInputError, WatchlistStorageError, OSError):
        empty("Watchlist overview unavailable ? local storage could not be read.",
              hint="Nothing was reset. See the error below the sub-function selector.")


def render(data_root: Path) -> None:
    """Render the local company/watchlist workspace without external research."""
    store = WatchlistStore(data_root)
    root_key = str(store.root)
    key = f"edge_company_{root_key}"
    snapshot_key = f"{key}_snapshot"
    slots: dict[str, object] = {}

    def reserve_overview(ctx: PanelContext) -> None:
        slots["overview"] = st.container()

    grid("watchlist", [
        Panel("head", "WCH", "Company / watchlist workspace", lambda c: _head(), w=12, h=3,
              min_w=4, fit=True, meta="Local notes only", badges=("manual", "local")),
        Panel("overview", "OVW", "Watchlist overview", reserve_overview, w=12, h=9, min_w=4,
              fit=True, meta="Saved-list snapshot", badges=("manual", "local")),
        Panel("work", "SUB", "Watchlist / company view",
              lambda c: _work(store, key, snapshot_key, data_root), w=12, h=18, min_w=5,
              fit=True, framed=False),
    ])
    # Filled last so a save in this run is reflected in the KPI strip, wherever
    # the overview panel sits (and skipped when the user hides it).
    if "overview" in slots:
        with slots["overview"]:
            _fill_overview(store, snapshot_key)
