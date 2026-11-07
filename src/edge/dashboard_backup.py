"""Explicit local journal recovery; rendering never opens storage or dialogs."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from edge import native_dialogs_backup
from edge.journal_backup import backup_journal, restore_journal

RESTORED_JOURNAL_ROOT_KEY = "edge_restored_journal_root"
_BACKUP_ERROR = (
    "Backup could not be created. Choose a new filename in a writable folder "
    "and check that the source journal is valid. Existing files are never replaced."
)
_RESTORE_ERROR = (
    "Restore could not be completed. Choose a supported EDGE backup and a new "
    "or empty folder with an existing parent. Existing data is never replaced."
)


def _entry_count(manifest: dict[str, object]) -> int:
    """Only display validated counts, never arbitrary manifest strings."""
    count = manifest["entry_count"]
    if type(count) is not int or count < 0:
        raise ValueError("Invalid entry count")
    return count


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


def render(data_root: Path) -> None:
    """Render recovery for the same root used by JournalStore, without IO on entry.

    Successful restore publishes a Path in ``edge_restored_journal_root``.
    The caller must offer its own explicit, session-only selection action.
    """
    st.header("Journal backup / restore")
    st.caption(
        "Local journal only · No provider requests · Simulation data is not included"
    )
    st.warning(
        "Backups contain your user-authored notes and source references, unencrypted. "
        "Keep them in a private location. Restoring never merges or replaces data."
    )
    st.subheader("Create a backup")
    st.caption(
        "Save a consistent SQLite snapshot of the original journal. "
        "Choose a new ZIP filename; even an approved overwrite is refused."
    )
    if st.button("Save journal backup…", key="edge_backup_create", type="primary"):
        _backup(data_root)
    st.divider()
    st.subheader("Restore a separate copy")
    st.caption(
        "Choose an EDGE backup, then a new or empty destination folder. "
        "Folders containing a journal, simulation output or any other files are refused. "
        "Cancel either dialog to leave everything unchanged."
    )
    if st.button("Restore journal backup…", key="edge_backup_restore"):
        _restore()
