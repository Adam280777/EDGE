"""Separate versioned application recovery; journal archives remain unchanged."""

# ruff: noqa: BLE001 -- never leak ZIP, SQL or user data through exceptions

import re
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from edge.application_backup import backup_application, restore_application
from edge.dashboard_manual import failure, readout
from edge.native_dialogs_manual import choose
from edge.ui_grid import Panel, grid
from edge.ui_kit import (
    Tile,
    kv_grid,
    section_rule,
    status_board,
    status_light,
    tiles,
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_BACKUP_STEPS = (
    ("1 · Choose file", "Pick a new bundle filename in the native dialog."),
    ("2 · Locked cut", "Registered user stores are captured under a cooperative lock."),
    ("3 · Validate", "Schemas, internal links and per-member SHA-256 digests are checked."),
    ("4 · Write bundle", "Existing destinations are never replaced."),
)
_RESTORE_STEPS = (
    ("1 · Name folder", "Type a new child folder name below (letters, digits, - or _)."),
    ("2 · Choose bundle", "Select an EDGE application bundle in the native dialog."),
    ("3 · Choose parent", "Select an existing parent; the named child must not exist."),
    ("4 · Restore copy", "Separate data/ and settings/ folders are written and validated."),
    ("5 · Active data", "Unchanged. EDGE never merges, overwrites or switches folders."),
)


def _inventory(manifest: object) -> None:
    """Validated manifest members only; malformed entries are never displayed."""
    if not isinstance(manifest, dict):
        return
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        return
    rows = [
        {
            "Member": entry["member"],
            "Bytes": entry["bytes"],
            "SHA-256": f"{entry['sha256'][:16]}…",
        }
        for entry in entries
        if isinstance(entry, dict)
        and isinstance(entry.get("member"), str)
        and type(entry.get("bytes")) is int
        and isinstance(entry.get("sha256"), str)
        and _SHA256.fullmatch(entry["sha256"])
    ]
    absent = manifest.get("absent") if isinstance(manifest.get("absent"), list) else []
    verified = manifest.get("cross_store_consistency_verified")
    tiles(
        [
            Tile("Stores captured", str(len(rows)), "Manifest members", "green"),
            Tile("Total size", f"{sum(row['Bytes'] for row in rows):,}", "bytes", "cyan"),
            Tile("Absent stores", str(len(absent)), "Not present at capture", "muted"),
            Tile(
                "Cross-store verified",
                "Yes" if verified is True else "No",
                "Registered-store links",
                "green" if verified is True else "amber",
            ),
            Tile(
                "Completed (UTC)",
                datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
                "Local clock",
                "muted",
            ),
        ],
        columns=5,
    )
    if rows:
        st.dataframe(
            rows,
            hide_index=True,
            width="stretch",
            column_config={
                "Bytes": st.column_config.NumberColumn("Bytes", format="%d"),
            },
        )


def _result(title: str, manifest: object) -> None:
    section_rule(title)
    _inventory(manifest)
    with st.expander("Complete manifest readout", expanded=False):
        readout(manifest)


def _status() -> None:
    status_board(
        [
            status_light("Scope", "ok", "registered user stores"),
            status_light("Encryption", "warn", "none · keep bundles private"),
            status_light("Credentials", "off", "excluded"),
            status_light("Vendor snapshots", "off", "excluded"),
            status_light("Simulation evidence", "off", "excluded"),
            status_light("Overwrite", "ok", "never · new folder only"),
        ]
    )


def _head() -> None:
    st.warning(
        "UNENCRYPTED USER DATA ? includes registered journals, watchlist, portfolio, "
        "decisions, monitoring and non-secret settings. Credentials, vendor snapshots, "
        "simulation evidence and runtime files are excluded. Journal-only backups remain separate."
    )
    st.caption(
        "The default bundle validates registered schemas/internal links and captures a "
        "cooperative locked cut of those user stores. This is not provider acceptance, "
        "financial validation or complete production recovery. Restore never merges, "
        "overwrites or switches active application data."
    )
    _status()


def _save(data_root: Path) -> None:
    kv_grid(_BACKUP_STEPS, columns=1)
    clicked = st.button(
        "Save application bundle?", key="edge_application_backup", type="primary"
    )
    if clicked:
        with st.container(key="edge_box_appbak_saved"):
            try:
                selected = choose("bundle-save")
                if selected is not None:
                    manifest = backup_application(data_root.parent, selected)
                    st.success(
                        "Application bundle saved. Existing destinations were not replaced."
                    )
                    _result("Bundle inventory", manifest)
            except Exception:
                failure("Application backup")


def _restore(data_root: Path) -> None:
    kv_grid(_RESTORE_STEPS, columns=1)
    leaf = st.text_input(
        "New child folder name / letters, digits, hyphen or underscore",
        max_chars=64, key="edge_application_restore_leaf",
    )
    st.caption(
        "Choose an existing parent in the native dialog; the named child must not exist, "
        "even if empty. Restored files use separate data/ and settings/ directories."
    )
    restore_clicked = st.button(
        "Restore separate application copy?", key="edge_application_restore"
    )
    if restore_clicked:
        with st.container(key="edge_box_appbak_restored"):
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
                st.success(
                    "Separate application copy restored. Active data has not changed."
                )
                st.text(f"Restored application folder: {destination}")
                _result("Restored inventory", manifest)
            except Exception:
                failure("Application restore")


def render(data_root: Path) -> None:
    # Each action's receipt stays inside its own panel, so no panel depends on
    # another having rendered first.
    grid("bundle", [
        Panel("head", "APP", "Application recovery / separate bundle", lambda c: _head(),
              w=12, h=7, min_w=4, fit=True, meta="Explicit native dialogs only",
              badges=("local",)),
        Panel("save", "NEW", "Save application bundle", lambda c: _save(data_root), w=6,
              h=9, min_w=3, fit=True, meta="New filename only"),
        Panel("restore", "RST", "Restore to a new application folder",
              lambda c: _restore(data_root), w=6, h=11, min_w=3, fit=True,
              meta="Never merges or replaces"),
    ])
