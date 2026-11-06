"""Local, append-only user notes. Rendering and downloads never create entries."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import streamlit as st

from edge.journal import (
    Horizon,
    JournalDraft,
    JournalEntry,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    SourceReference,
    export_entry_json,
    validate_draft,
)
from edge.journal_store import JournalStore

PAGE_SIZE = 10
SOURCE_SLOTS = 3
HORIZONS: dict[str, Horizon] = {"Swing": "swing", "Long-term": "long-term"}


def _date(value: str, *, optional: bool = False) -> date | None:
    if optional and not value:
        return None
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value and parsed <= datetime.now(UTC).date():
            return parsed
    except ValueError:
        pass
    raise JournalInputError("Use a real YYYY-MM-DD date, no later than today (UTC).")


def _field(label: str, value: str | None) -> None:
    """Only trusted labels use Markdown-capable APIs; all stored text is literal."""
    st.subheader(label)
    st.text(value or "Not supplied")


def _entry(entry: JournalEntry, *, key: str) -> None:
    draft = entry.draft
    st.header("Saved entry / immutable snapshot")
    st.text(f"Entry ID: {entry.entry_id}")
    st.text(
        f"Provenance: {entry.provenance}\n"
        f"Created (UTC): {entry.created_at.astimezone(UTC).isoformat()}\n"
        f"Research date (user-declared): {draft.research_date.isoformat()}\n"
        f"Horizon: {draft.horizon}"
    )
    st.caption(
        "This saved snapshot is independent of draft edits. Dates and citations are "
        "user declarations, not verified freshness or evidence of provider retrieval."
    )
    _field("Title", draft.title)
    _field("Ticker / company identity unverified", draft.symbol)
    _field("Company / user-declared", draft.company_name)
    for label, value in (
        ("Thesis", draft.thesis),
        ("Bull case", draft.bull_case),
        ("Bear case", draft.bear_case),
        ("Catalysts", draft.catalysts),
        ("Invalidation", draft.invalidation),
    ):
        _field(label, value)
    st.subheader("Source references / user-declared")
    if not draft.sources:
        st.text("Not supplied — no citations recorded.")
    for source in draft.sources:
        st.text(source.label)
        st.text(f"URL: {source.url or 'Not supplied'}")
        st.text(
            "Source date (user-declared): "
            + (source.source_date.isoformat() if source.source_date else "Not supplied")
        )
    st.download_button(
        "Download saved entry (JSON)",
        data=export_entry_json(entry),
        file_name=f"edge-journal-{entry.entry_id}.json",
        mime="application/json",
        on_click="ignore",
        key=f"{key}_download",
    )


def _create(store: JournalStore, horizon: Horizon, key: str) -> None:
    st.header("New user-authored entry")
    st.caption(
        "Required: title, ticker, company, research date and thesis. "
        "Title/company: 1–200 characters; narratives: up to 10,000 each. "
        "Only Save new entry writes to disk. Entries cannot be edited or deleted. "
        "Unsaved form edits may be lost when navigating away."
    )
    with st.form(f"{key}_form", enter_to_submit=False):
        title = st.text_input("Title (required)", max_chars=200, key=f"{key}_title")
        symbol = st.text_input(
            "Ticker (required; identity unverified)",
            max_chars=32,
            key=f"{key}_symbol",
        )
        st.caption(
            "Ticker: up to 32 ASCII letters/digits or . ^ : / -; "
            "start with a letter or digit. Identity is unverified."
        )
        company = st.text_input(
            "Company (required; user-declared)", max_chars=200, key=f"{key}_company"
        )
        research_date = st.text_input(
            "Research date (required; YYYY-MM-DD)",
            value=datetime.now(UTC).date().isoformat(),
            key=f"{key}_date",
        )
        thesis = st.text_area("Thesis (required)", max_chars=10000, key=f"{key}_thesis")
        with st.expander("Optional research sections"):
            narratives = {
                name: st.text_area(label, max_chars=10000, key=f"{key}_{name}")
                for name, label in (
                    ("bull_case", "Bull case"),
                    ("bear_case", "Bear case"),
                    ("catalysts", "Catalysts"),
                    ("invalidation", "Invalidation"),
                )
            }
        sources = []
        with st.expander("Optional source references / up to 3"):
            st.caption(
                "Up to 3 manual references in this form. Leave unused references blank. "
                "Each used reference needs a label (1–200 characters). URL and source "
                "date are optional. URLs must be absolute HTTPS, at most 2,048 characters, "
                "without credentials or whitespace. Dates: YYYY-MM-DD, not future. "
                "No links are fetched; no automatic SEC import."
            )
            for slot in range(SOURCE_SLOTS):
                label = st.text_input(
                    f"Reference {slot + 1} label",
                    max_chars=200,
                    key=f"{key}_source_{slot}_label",
                )
                url = st.text_input(
                    f"Reference {slot + 1} HTTPS URL",
                    max_chars=2048,
                    key=f"{key}_source_{slot}_url",
                )
                source_date = st.text_input(
                    f"Reference {slot + 1} source date (YYYY-MM-DD)",
                    key=f"{key}_source_{slot}_date",
                )
                sources.append((label, url, source_date))
        submitted = st.form_submit_button("Save new entry", type="primary")

    if submitted:
        # A failed attempt must never leave a previous save labelled as new success.
        st.session_state.pop(f"{key}_error", None)
        try:
            draft = JournalDraft(
                horizon=horizon,
                symbol=symbol,
                company_name=company,
                title=title,
                research_date=_date(research_date),
                thesis=thesis,
                **narratives,
                sources=tuple(
                    SourceReference(label, url or None, _date(day, optional=True))
                    for label, url, day in sources
                    if label or url or day
                ),
            )
            draft = validate_draft(draft)
            entry = store.create(draft)
        except JournalInputError:
            st.session_state[f"{key}_error"] = (
                "Entry not saved: invalid inputs. Check required fields, text limits, "
                "ticker format, exact calendar dates (not future) and HTTPS references. "
                "Correct the form and explicitly save again."
            )
        except (JournalStorageError, OSError):
            st.session_state[f"{key}_error"] = (
                "Entry save failed: local journal storage unavailable. Check access "
                "and the journal database before explicitly trying again. "
                "No successful save is confirmed for this attempt."
            )
        else:
            st.session_state[f"{key}_saved"] = entry
            st.success("New entry saved. Its immutable snapshot is shown below.")
    if error := st.session_state.get(f"{key}_error"):
        st.error(error)
    if saved := st.session_state.get(f"{key}_saved"):
        if st.session_state.get(f"{key}_error"):
            st.info("Previous saved entry below — not a result of the failed attempt.")
        try:
            _entry(saved, key=key)
        except (JournalInputError, JournalStorageError, OSError):
            st.error(
                "Saved snapshot export unavailable. The displayed entry ID still "
                "identifies the saved evidence. Reopen Saved entries to "
                "inspect the stored record; do not save again just to retry export."
            )


def _browse(store: JournalStore, horizon: Horizon, key: str) -> None:
    st.header("Saved entries")
    page_key = f"{key}_page"
    page = st.session_state.get(page_key, 0)
    try:
        summaries = store.list_entries(
            horizon=horizon, limit=PAGE_SIZE + 1, offset=page * PAGE_SIZE
        )
        visible = summaries[:PAGE_SIZE]
        if not visible:
            st.info(
                "No saved entries on this page. Choose New entry to record research."
            )
        st.caption(f"Page {page + 1} · Up to {PAGE_SIZE} entries per page")
        previous, following = st.columns(2)
        if previous.button("Previous page", disabled=page == 0, key=f"{key}_previous"):
            st.session_state[page_key] = page - 1
            st.rerun()
        if following.button(
            "Next page", disabled=len(summaries) <= PAGE_SIZE, key=f"{key}_next"
        ):
            st.session_state[page_key] = page + 1
            st.rerun()
        if not visible:
            return
        for index, summary in enumerate(visible, start=1):
            st.text(
                f"{index}. {summary.research_date.isoformat()} · {summary.symbol}\n"
                f"{summary.title}"
            )
        # Widget option labels never interpret a user's title as Markdown.
        labels = {
            summary.entry_id: f"Entry {index} · {summary.research_date.isoformat()}"
            for index, summary in enumerate(visible, start=1)
        }
        selected = st.selectbox(
            "View saved entry",
            list(labels),
            format_func=labels.__getitem__,
            key=f"{key}_selection_{page}",
        )
        _entry(store.get(selected), key=f"{key}_view")
    except JournalNotFoundError:
        st.error("Saved entry not found. Select another entry or reopen Saved entries.")
    except (JournalStorageError, JournalInputError, OSError):
        st.error(
            "Journal read failed: local storage is unavailable or invalid. "
            "Check the journal database and access permissions; nothing has been reset."
        )


def render(output_dir: Path) -> None:
    st.header("Research journal")
    st.warning(
        "USER-AUTHORED NOTES — not verified research, recommendations or trade plans. "
        "Ticker and company identity are unverified. No market data, calculations, "
        "provider requests or broker actions."
    )
    st.caption(
        "Local append-only journal · Separate research-journal.sqlite3 database · "
        "No database is created until an explicit valid save."
    )
    label = st.radio(
        "Research horizon", list(HORIZONS), horizontal=True, key="edge_journal_horizon"
    )
    horizon: Horizon = HORIZONS[label]
    key = f"edge_journal_{output_dir.resolve()}_{horizon}"
    mode = st.radio(
        "Journal task",
        ["Saved entries", "New entry"],
        horizontal=True,
        key=f"{key}_task",
    )
    store = JournalStore(output_dir)
    if mode == "New entry":
        _create(store, horizon, key)
    else:
        _browse(store, horizon, key)
