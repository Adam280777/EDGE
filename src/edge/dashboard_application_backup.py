"""Separate versioned application recovery; journal archives remain unchanged."""

# ruff: noqa: BLE001 -- never leak ZIP, SQL or user data through exceptions

import re
from pathlib import Path

import streamlit as st

from edge.application_backup import backup_application, restore_application
from edge.dashboard_manual import failure, readout
from edge.native_dialogs_manual import choose


def render(data_root: Path) -> None:
    st.header("Application recovery / separate bundle")
    st.warning(
        "UNENCRYPTED USER DATA — includes registered journals, watchlist, portfolio, "
        "decisions, monitoring and non-secret settings. Credentials, vendor snapshots, "
        "simulation evidence and runtime files are excluded. Journal-only backups remain separate."
    )
    st.caption(
        "The default bundle validates registered schemas/internal links and captures a "
        "cooperative locked cut of those user stores. This is not provider acceptance, "
        "financial validation or complete production recovery. Restore never merges, "
        "overwrites or switches active application data."
    )
    if st.button("Save application bundle…", key="edge_application_backup", type="primary"):
        try:
            selected = choose("bundle-save")
            if selected is not None:
                manifest = backup_application(data_root.parent, selected)
                st.success("Application bundle saved. Existing destinations were not replaced.")
                readout(manifest)
        except Exception:
            failure("Application backup")
    st.subheader("Restore to a new application folder")
    leaf = st.text_input(
        "New child folder name / letters, digits, hyphen or underscore",
        max_chars=64, key="edge_application_restore_leaf",
    )
    st.caption(
        "Choose an existing parent in the native dialog; the named child must not exist, "
        "even if empty. Restored files use separate data/ and settings/ directories."
    )
    if st.button("Restore separate application copy…", key="edge_application_restore"):
        try:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", leaf) is None:
                raise ValueError
            source = choose("bundle-open")
            if source is None:
                return
            parent = choose("restore-parent")
            if parent is None:
                return
            destination = parent / leaf
            manifest = restore_application(source, destination)
            st.success("Separate application copy restored. Active data has not changed.")
            st.text(f"Restored application folder: {destination}")
            readout(manifest)
        except Exception:
            failure("Application restore")
