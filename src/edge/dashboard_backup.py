"""Explicit local journal recovery; rendering never opens storage or dialogs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

# Layout primitives use a separate alias so presentation never depends on the
# narrow ``st`` surface the recovery tests substitute.
import streamlit as _layout
import streamlit as st

from edge import native_dialogs_backup
from edge.journal_backup import backup_journal, restore_journal
from edge.ui_grid import Panel, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    kv_grid,
    panel,
    status_board,
    status_light,
    tiles,
)

RESTORED_JOURNAL_ROOT_KEY = "edge_restored_journal_root"
_BACKUP_ERROR = (
    "Backup could not be created. Choose a new filename in a writable folder "
    "and check that the source journal is valid. Existing files are never replaced."
)
_RESTORE_ERROR = (
    "Restore could not be completed. Choose a supported EDGE backup and a new "
    "or empty folder with an existing parent. Existing data is never replaced."
)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_RESTORE_STEPS = (
    ("1 · Choose backup", "Select an EDGE journal backup ZIP in the native dialog."),
    ("2 · Choose folder", "Select a new or empty destination folder with an existing parent."),
    ("3 · Validate", "EDGE checks the manifest, schema and snapshot digest before copying."),
    ("4 · Restore copy", "A separate journal is written; the current journal is untouched."),
    ("5 · Open explicitly", "Use Open restored journal to select it for this session only."),
)
_BACKUP_STEPS = (
    ("1 · Choose filename", "Pick a new ZIP name in a private, writable folder."),
    ("2 · Snapshot", "A consistent SQLite snapshot of the journal is validated."),
    ("3 · Write archive", "Manifest and snapshot are written; existing files are refused."),
)


def _entry_count(manifest: dict[str, object]) -> int:
    """Only display validated counts, never arbitrary manifest strings."""
    count = manifest["entry_count"]
    if type(count) is not int or count < 0:
        raise ValueError("Invalid entry count")
    return count


def _whole(value: object) -> str:
    """Validated non-negative integers only; anything else is shown as unavailable."""
    return f"{value:,}" if type(value) is int and value >= 0 else UNAVAILABLE


def _receipt(code: str, title: str, manifest: dict[str, object], count: int) -> None:
    """Fixed-vocabulary manifest receipt for this action only; nothing persisted."""
    digest = manifest.get("snapshot_sha256")
    digest_text = (
        f"{digest[:12]}…{digest[-6:]}"
        if isinstance(digest, str) and _SHA256.fullmatch(digest)
        else UNAVAILABLE
    )
    panel(code, title, meta="This action only · not retained", badges=("local",))
    tiles(
        [
            Tile("Journal entries", f"{count:,}", "Validated count", "green"),
            Tile("Snapshot size", _whole(manifest.get("snapshot_bytes")), "bytes", "cyan"),
            Tile(
                "Completed (UTC)",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "Local clock at completion",
                "muted",
            ),
        ],
        columns=3,
    )
    kv_grid(
        [
            ("Snapshot SHA-256", digest_text),
            ("Schema version", _whole(manifest.get("schema_version"))),
            ("Archive format version", _whole(manifest.get("version"))),
            ("Encryption", "None · keep the archive private"),
        ]
    )


def _backup(data_root: Path) -> None:
    try:
        destination = native_dialogs_backup.choose_backup_destination()
        if destination is None:
            return
        manifest = backup_journal(data_root, destination)
        count = _entry_count(manifest)
    except Exception:  # noqa: BLE001 -- native/IO boundary, no raw diagnostics
        st.error(_BACKUP_ERROR)
        return
    st.success(f"Backup created · {count} journal entries.")
    with _layout.container(key="edge_box_backup_receipt"):
        _receipt("RCP", "Backup receipt", manifest, count)


def _restore() -> None:
    try:
        source = native_dialogs_backup.choose_backup_source()
        if source is None:
            return
        destination = native_dialogs_backup.choose_restore_destination()
        if destination is None:
            return
        manifest = restore_journal(source, destination)
        count = _entry_count(manifest)
        restored_root = destination.absolute()
    except Exception:  # noqa: BLE001 -- native/IO boundary, fixed recovery guidance
        st.error(_RESTORE_ERROR)
        return
    # A candidate only: the integration owner controls explicit session selection.
    # Never change the default root, active workspace, or any persistent setting.
    st.session_state[RESTORED_JOURNAL_ROOT_KEY] = restored_root
    st.success(f"Restore completed · {count} journal entries.")
    st.caption(
        "The current journal has not changed. Use Open restored journal to select "
        "the restored copy for this session only."
    )
    with _layout.container(key="edge_box_backup_restore_receipt"):
        _receipt("RCP", "Restore receipt", manifest if isinstance(manifest, dict) else {}, count)


def _status() -> None:
    """Session-only facts; rendering never opens storage, dialogs or archives."""
    restored = RESTORED_JOURNAL_ROOT_KEY in st.session_state
    status_board(
        [
            status_light("Scope", "ok", "local journal only"),
            status_light("Provider requests", "off", "none"),
            status_light("Simulation data", "off", "not included"),
            status_light("Encryption", "warn", "none · keep backups private"),
            status_light("Overwrite", "ok", "always refused"),
            status_light(
                "Restored copy",
                "ok" if restored else "off",
                "available to open this session" if restored else "none this session",
            ),
        ]
    )


def _steps(rows: tuple[tuple[str, str], ...]) -> None:
    kv_grid(rows, columns=1)


def _head() -> None:
    st.caption(
        "Local journal only ? No provider requests ? Simulation data is not included"
    )
    st.warning(
        "Backups contain your user-authored notes and source references, unencrypted. "
        "Keep them in a private location. Restoring never merges or replaces data."
    )
    _status()


def _create(data_root: Path) -> None:
    st.caption(
        "Save a consistent SQLite snapshot of the original journal. "
        "Choose a new ZIP filename; even an approved overwrite is refused."
    )
    _steps(_BACKUP_STEPS)
    if st.button("Save journal backup?", key="edge_backup_create", type="primary"):
        _backup(data_root)


def _restore_form() -> None:
    st.caption(
        "Choose an EDGE backup, then a new or empty destination folder. "
        "Folders containing a journal, simulation output or any other files are refused. "
        "Cancel either dialog to leave everything unchanged."
    )
    _steps(_RESTORE_STEPS)
    if st.button("Restore journal backup?", key="edge_backup_restore"):
        _restore()


def render(data_root: Path) -> None:
    """Render recovery for the same root used by JournalStore, without IO on entry.

    Successful restore publishes a Path in ``edge_restored_journal_root``.
    The caller must offer its own explicit, session-only selection action.
    """
    grid("recovery", [
        Panel("head", "BAK", "Journal backup / restore", lambda c: _head(), w=12, h=6,
              min_w=4, fit=True, meta="Explicit native dialogs only", badges=("local",)),
        Panel("create", "NEW", "Create a backup", lambda c: _create(data_root), w=6, h=9,
              min_w=3, fit=True, meta="New ZIP filename only"),
        Panel("restore", "RST", "Restore a separate copy", lambda c: _restore_form(), w=6,
              h=9, min_w=3, fit=True, meta="Never merges or replaces"),
    ])
