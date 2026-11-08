"""Saved workspace layouts: one versioned JSON file beside provider settings.

Loading never creates files. A corrupt file is reported, never overwritten:
callers fall back to the built-in layouts in memory and saving is refused.
"""

# ruff: noqa: BLE001
# Fail closed on unknown parse/OS failures; never echo the file's contents.

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from edge.application_storage_lock import storage_write_lock
from edge.layout import (
    BUILTIN_PROFILES,
    DEFAULT_PROFILE,
    LayoutError,
    Slot,
    clean_name,
    slots_from_list,
    valid_id,
)

VERSION = 1
MAX_PROFILES = 30
MAX_WORKSPACES = 20
MAX_BYTES = 512 * 1024


class LayoutStoreError(Exception):
    def __init__(self) -> None:
        super().__init__("Saved layouts are invalid or unavailable.")


Workspaces = dict[str, tuple[Slot, ...]]


@dataclass(frozen=True)
class Layouts:
    active: str = DEFAULT_PROFILE
    custom: Mapping[str, Workspaces] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [*BUILTIN_PROFILES, *sorted(self.custom, key=str.casefold)]

    def to_json(self) -> dict[str, object]:
        return {
            "version": VERSION,
            "active": self.active,
            "custom": {
                name: {ws: [s.to_dict() for s in slots] for ws, slots in sorted(spaces.items())}
                for name, spaces in sorted(self.custom.items())
            },
        }


def layouts_path(data_root: Path) -> Path:
    return data_root.parent / "settings" / "layouts-v1.json"


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise LayoutStoreError()
        out[key] = value
    return out


def parse(values: object) -> Layouts:
    if not isinstance(values, dict) or set(values) != {"version", "active", "custom"}:
        raise LayoutStoreError()
    if values["version"] != VERSION or isinstance(values["version"], bool):
        raise LayoutStoreError()
    custom_raw = values["custom"]
    if not isinstance(custom_raw, dict) or len(custom_raw) > MAX_PROFILES:
        raise LayoutStoreError()
    custom: dict[str, Workspaces] = {}
    try:
        for name, spaces in custom_raw.items():
            if clean_name(name) != name or not isinstance(spaces, dict) or len(spaces) > MAX_WORKSPACES:
                raise LayoutStoreError()
            if any(not valid_id(ws) for ws in spaces):
                raise LayoutStoreError()
            custom[name] = {ws: slots_from_list(raw) for ws, raw in spaces.items()}
    except LayoutError:
        raise LayoutStoreError() from None
    active = values["active"]
    if not isinstance(active, str) or (active not in BUILTIN_PROFILES and active not in custom):
        raise LayoutStoreError()
    return Layouts(active=active, custom=custom)


def load_layouts(data_root: Path) -> Layouts:
    """Missing file means built-in layouts only; corrupt files raise."""
    path = layouts_path(data_root)
    try:
        with path.open("rb") as stream:
            contents = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return Layouts()
    except OSError:
        raise LayoutStoreError() from None
    if len(contents) > MAX_BYTES:
        raise LayoutStoreError()
    try:
        return parse(json.loads(contents, object_pairs_hook=_unique))
    except LayoutStoreError:
        raise
    except Exception:
        raise LayoutStoreError() from None


def save_layouts(data_root: Path, layouts: Layouts) -> None:
    """Explicit user action only: validated, atomic replace under the storage lock."""
    if not isinstance(layouts, Layouts):
        raise LayoutStoreError()
    payload = layouts.to_json()
    parse(json.loads(json.dumps(payload)))  # same strictness as loading
    text = json.dumps(payload, allow_nan=False, indent=1)
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise LayoutStoreError()
    path = layouts_path(data_root)
    temporary = None
    try:
        with storage_write_lock(path.parent.parent):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", dir=path.parent, delete=False,
                    prefix=".layouts-", suffix=".tmp",
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(text)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    except Exception:
        raise LayoutStoreError() from None


def with_profile(layouts: Layouts, name: str, spaces: Workspaces) -> Layouts:
    name = clean_name(name)
    if name not in layouts.custom and len(layouts.custom) >= MAX_PROFILES:
        raise LayoutError(f"At most {MAX_PROFILES} saved layouts.")
    custom = dict(layouts.custom)
    custom[name] = dict(spaces)
    return Layouts(active=name, custom=custom)


def without_profile(layouts: Layouts, name: str) -> Layouts:
    if name not in layouts.custom:
        raise LayoutError("Only saved custom layouts can be deleted.")
    custom = {k: v for k, v in layouts.custom.items() if k != name}
    active = DEFAULT_PROFILE if layouts.active == name else layouts.active
    return Layouts(active=active, custom=custom)


def with_active(layouts: Layouts, name: str) -> Layouts:
    if name not in BUILTIN_PROFILES and name not in layouts.custom:
        raise LayoutError("Unknown layout.")
    return Layouts(active=name, custom=layouts.custom)
