"""Local, append-only user notes. Rendering and downloads never create entries."""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import streamlit as st

from edge import ui_charts as charts
from edge.journal import (
    Horizon,
    JournalDraft,
    JournalEntry,
    JournalInputError,
    JournalNotFoundError,
    JournalStorageError,
    JournalSummary,
    SourceReference,
    export_entry_json,
    validate_draft,
)
from edge.journal_store import JournalStore
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
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

PAGE_SIZE = 10
SOURCE_SLOTS = 3
HORIZONS: dict[str, Horizon] = {"Swing": "swing", "Long-term": "long-term"}


def key_slug(key: str) -> str:
    """Stable CSS-safe container key fragment for a path-bearing widget prefix."""
    return hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]


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


HORIZON_NAMES = {value: name for name, value in HORIZONS.items()}
OVERVIEW_PAGES = 5  # Overview reads at most 5 x 100 summaries; never writes.


def _field(label: str, value: str | None) -> None:
    """Only trusted labels use Markdown-capable APIs; all stored text is literal."""
    section_rule(label)
    st.text(value or "Not supplied")


def _entry(entry: JournalEntry, *, key: str) -> None:
    draft = entry.draft
    with st.container(key=f"edge_box_{key_slug(key)}_entry"):
        panel("NOTE", "Saved entry / immutable snapshot",
              meta=HORIZON_NAMES.get(draft.horizon, draft.horizon),
              badges=("manual", "local"))
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
        ident, company = st.columns(2, gap="small")
        with ident:
            _field("Ticker / company identity unverified", draft.symbol)
        with company:
            _field("Company / user-declared", draft.company_name)
        _field("Thesis", draft.thesis)
        bull, bear = st.columns(2, gap="small")
        with bull:
            _field("Bull case", draft.bull_case)
        with bear:
            _field("Bear case", draft.bear_case)
        catalysts, invalidation = st.columns(2, gap="small")
        with catalysts:
            _field("Catalysts", draft.catalysts)
        with invalidation:
            _field("Invalidation", draft.invalidation)
        section_rule("Source references / user-declared")
        if not draft.sources:
            st.text("Not supplied — no citations recorded.")
        for source in draft.sources:
            st.text(source.label)
            st.text(f"URL: {source.url or 'Not supplied'}")
            st.text(
                "Source date (user-declared): "
                + (source.source_date.isoformat() if source.source_date else "Not supplied")
            )
        _download(entry, key)


def _download(entry: JournalEntry, key: str) -> None:
    st.download_button(
        "Download saved entry (JSON)",
        data=export_entry_json(entry),
        file_name=f"edge-journal-{entry.entry_id}.json",
        mime="application/json",
        on_click="ignore",
        key=f"{key}_download",
    )


def _create(store: JournalStore, horizon: Horizon, key: str) -> None:
    with st.container(key=f"edge_box_{key_slug(key)}_new"):
        panel("NEW", "New user-authored entry", meta=HORIZON_NAMES.get(horizon, horizon),
              badges=("manual",))
        st.caption(
            "Required: title, ticker, company, research date and thesis. "
            "Title/company: 1–200 characters; narratives: up to 10,000 each. "
            "Only Save new entry writes to disk. Entries cannot be edited or deleted. "
            "Unsaved form edits may be lost when navigating away."
        )
        with st.form(f"{key}_form", enter_to_submit=False):
            title = st.text_input("Title (required)", max_chars=200, key=f"{key}_title")
            ident, company_col, date_col = st.columns([1, 2, 1], gap="small")
            symbol = ident.text_input(
                "Ticker (required; identity unverified)",
                max_chars=32,
                key=f"{key}_symbol",
            )
            company = company_col.text_input(
                "Company (required; user-declared)", max_chars=200, key=f"{key}_company"
            )
            research_date = date_col.text_input(
                "Research date (required; YYYY-MM-DD)",
                value=datetime.now(UTC).date().isoformat(),
                key=f"{key}_date",
            )
            st.caption(
                "Ticker: up to 32 ASCII letters/digits or . ^ : / -; "
                "start with a letter or digit. Identity is unverified."
            )
            thesis = st.text_area("Thesis (required)", max_chars=10000, key=f"{key}_thesis")
            with st.expander("Optional research sections"):
                narratives = {}
                for row in (
                    (("bull_case", "Bull case"), ("bear_case", "Bear case")),
                    (("catalysts", "Catalysts"), ("invalidation", "Invalidation")),
                ):
                    for column, (name, label) in zip(
                        st.columns(2, gap="small"), row, strict=True
                    ):
                        narratives[name] = column.text_area(
                            label, max_chars=10000, key=f"{key}_{name}"
                        )
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
                    label_col, url_col, day_col = st.columns([2, 3, 1.4], gap="small")
                    label = label_col.text_input(
                        f"Reference {slot + 1} label",
                        max_chars=200,
                        key=f"{key}_source_{slot}_label",
                    )
                    url = url_col.text_input(
                        f"Reference {slot + 1} HTTPS URL",
                        max_chars=2048,
                        key=f"{key}_source_{slot}_url",
                    )
                    source_date = day_col.text_input(
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


def _cards(visible: list[JournalSummary]) -> None:
    """Entry cards; stored titles and tickers are literal text, never Markdown."""
    columns = st.columns(2, gap="small")
    for index, summary in enumerate(visible, start=1):
        with columns[(index - 1) % 2], st.container(key=f"edge_box_jrn_card_{index}"):
            html_block(
                '<div class="edge-pane-head">'
                f'<span class="edge-pane-code">#{index}</span>'
                f'<span class="edge-pane-title">{esc(summary.research_date.isoformat())}</span>'
                '<span class="edge-pane-rule" aria-hidden="true"></span>'
                f'{chip(HORIZON_NAMES.get(summary.horizon, summary.horizon).upper(), "amber")}'
                f'<span class="edge-pane-meta">Saved {esc(fmt_time(summary.created_at))}</span>'
                "</div>"
            )
            st.text(
                f"{index}. {summary.research_date.isoformat()} · {summary.symbol}\n"
                f"{summary.title}"
            )


def _browse(store: JournalStore, horizon: Horizon, key: str) -> None:
    section_rule("Saved entries")
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
        previous, following, _ = st.columns([1, 1, 3], gap="small")
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
        _cards(visible)
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


def _load_overview(store: JournalStore) -> list[JournalSummary] | None:
    """Read-only summary across both horizons; reading never creates the database."""
    notes: list[JournalSummary] = []
    try:
        for page in range(OVERVIEW_PAGES):
            batch = store.list_entries(limit=100, offset=page * 100)
            notes.extend(batch)
            if len(batch) < 100:
                break
    except (JournalStorageError, JournalInputError, OSError):
        return None
    return notes


def _overview(notes: list[JournalSummary] | None, ctx: PanelContext) -> None:
    if notes is None:
        empty("Journal overview unavailable ? local storage could not be read.",
              hint="Nothing was reset. See Saved entries below for details.")
        return
    capped = len(notes) >= OVERVIEW_PAGES * 100
    counts = Counter(note.horizon for note in notes)
    latest_saved = max((note.created_at for note in notes), default=None)
    latest_research = max((note.research_date for note in notes), default=None)
    suffix = "+" if capped else ""
    tiles([
        Tile("Notes saved", f"{len(notes)}{suffix}", "Both horizons ? append-only", "amber"),
        Tile("Swing", f"{counts.get('swing', 0)}{suffix if capped else ''}", "Short-horizon notes", "cyan"),
        Tile("Long-term", f"{counts.get('long-term', 0)}{suffix if capped else ''}",
             "Long-horizon notes", "violet"),
        Tile("Latest research date", fmt_time(latest_research), "User-declared", "ink"),
        Tile("Last saved", fmt_time(latest_saved), "Local clock at save", "ink"),
    ], columns=5)
    if not notes:
        empty("No journal notes yet in either horizon.",
              hint="Choose New entry below to record a swing or long-term thesis.")
        return
    events = [
        {"lane": HORIZON_NAMES.get(note.horizon, note.horizon),
         "when": note.research_date.isoformat(), "label": note.symbol}
        for note in notes
    ]
    saved = [
        {"when": note.created_at.astimezone(UTC).isoformat(),
         "series": HORIZON_NAMES.get(note.horizon, note.horizon)}
        for note in notes
    ]
    timeline = charts.timeline(events, title="Notes by research date ? lane = horizon")
    cumulative = charts.step_area(saved, title="Cumulative notes saved", y_title="Notes")
    timeline_alt = "Timeline of journal notes by user-declared research date and horizon"
    cumulative_alt = "Stepped cumulative count of saved journal notes per horizon"
    if ctx.view == "timeline":
        charts.show(timeline, alt_text=timeline_alt)
    elif ctx.view == "cumulative":
        charts.show(cumulative, alt_text=cumulative_alt)
    else:
        left, right = st.columns([1.3, 1], gap="small")
        with left:
            charts.show(timeline, alt_text=timeline_alt)
        with right:
            charts.show(cumulative, alt_text=cumulative_alt)
    if capped:
        st.caption(f"Overview reads the latest {OVERVIEW_PAGES * 100} notes only.")


def _head() -> None:
    st.warning(
        "USER-AUTHORED NOTES ? not verified research, recommendations or trade plans. "
        "Ticker and company identity are unverified. No market data, calculations, "
        "provider requests or broker actions."
    )
    st.caption(
        "Local append-only journal ? Separate research-journal.sqlite3 database ? "
        "No database is created until an explicit valid save."
    )


def _entries(store: JournalStore, output_dir: Path) -> None:
    """Horizon and task switchers with the chosen form or browser (one panel)."""
    horizon_col, task_col = st.columns(2, gap="small")
    with horizon_col:
        label = st.radio(
            "Research horizon", list(HORIZONS), horizontal=True, key="edge_journal_horizon"
        )
    horizon: Horizon = HORIZONS[label]
    key = f"edge_journal_{output_dir.resolve()}_{horizon}"
    with task_col:
        mode = st.radio(
            "Journal task",
            ["Saved entries", "New entry"],
            horizontal=True,
            key=f"{key}_task",
        )
    if mode == "New entry":
        _create(store, horizon, key)
    else:
        _browse(store, horizon, key)


def panels(output_dir: Path) -> list[Panel]:
    """Journal panels; the overview is read once, before layout."""
    store = JournalStore(output_dir)
    notes = _load_overview(store)
    return [
        Panel("head", "JRN", "Research journal", lambda c: _head(), w=12, h=4, min_w=4,
              fit=True, meta="Swing and long-term theses", badges=("manual", "local")),
        Panel("overview", "OVW", "Journal overview", lambda c: _overview(notes, c), w=12, h=12,
              min_w=4, fit=True, badges=("manual", "local"),
              views=(("both", "Timeline + cumulative"), ("timeline", "Timeline"),
                     ("cumulative", "Cumulative saves"))),
        Panel("entries", "TSK", "Journal entries", lambda c: _entries(store, output_dir),
              w=12, h=16, min_w=5, fit=True, badges=("manual",)),
    ]


def render(output_dir: Path) -> None:
    grid("journal", panels(output_dir))
