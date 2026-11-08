"""Narrow, process-local desktop storage injection; no browser/environment override."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_desktop_data_root: Path | None = None


def local_app_root() -> Path:
    """Return the Windows per-user application root, never the distribution folder."""
    location = os.environ.get("LOCALAPPDATA")
    if not location or not Path(location).is_absolute():
        raise RuntimeError(
            "Windows LOCALAPPDATA is unavailable; EDGE cannot select safe storage."
        )
    return Path(location) / "EDGE"


def activate_packaged_data_root() -> Path:
    """Called only by the authenticated frozen server before running app.py."""
    global _desktop_data_root
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Desktop storage injection requires the bundled executable.")
    _desktop_data_root = local_app_root() / "data"
    return _desktop_data_root


def activate_diagnostic_data_root(root: Path) -> Path:
    """Authenticated diagnostic child only; never a production path override."""
    import tempfile

    global _desktop_data_root
    if (
        not getattr(sys, "frozen", False)
        or not root.is_absolute()
        or root.parent.resolve() != Path(tempfile.gettempdir()).resolve()
        or not root.name.startswith("EDGE-research-check-")
        or not root.is_dir()
        or root.is_symlink()
        or Path.cwd().parent != root / "runtime"
    ):
        raise RuntimeError("Invalid internally owned diagnostic profile.")
    _desktop_data_root = root / "application" / "data"
    return _desktop_data_root


def app_data_root(repository_default: Path) -> Path:
    """Source dashboard retains its existing default; no directories are created here."""
    return _desktop_data_root if _desktop_data_root is not None else repository_default
