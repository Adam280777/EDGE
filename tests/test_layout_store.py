from __future__ import annotations

import json

import pytest

from edge.layout import LayoutError, Slot
from edge.layout_store import (
    Layouts,
    LayoutStoreError,
    layouts_path,
    load_layouts,
    save_layouts,
    with_active,
    with_profile,
    without_profile,
)

SLOTS = (Slot("book", 0, 0, 12, 4), Slot("fx", 0, 4, 6, 6, hidden=True))


def test_missing_file_gives_builtins_and_creates_nothing(tmp_path):
    root = tmp_path / "data"
    layouts = load_layouts(root)
    assert layouts == Layouts()
    assert layouts.names()[:3] == ["Default", "Swing trading", "Long-term"]
    assert not (tmp_path / "settings").exists()


def test_round_trip_is_atomic_and_exact(tmp_path):
    root = tmp_path / "data"
    layouts = with_profile(Layouts(), "My desk", {"home": SLOTS})
    save_layouts(root, layouts)
    assert load_layouts(root) == layouts
    assert load_layouts(root).active == "My desk"
    assert [p.name for p in layouts_path(root).parent.iterdir()] == ["layouts-v1.json"]
    switched = with_active(load_layouts(root), "Swing trading")
    save_layouts(root, switched)
    assert load_layouts(root).active == "Swing trading"
    removed = without_profile(with_active(switched, "My desk"), "My desk")
    assert removed.active == "Default" and removed.custom == {}


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"version": 2, "active": "Default", "custom": {}}',
        '{"version": 1, "active": "Nope", "custom": {}}',
        '{"version": 1, "active": "Default", "custom": {}, "x": 1}',
        '{"version": 1, "version": 1, "active": "Default", "custom": {}}',
        '{"version": 1, "active": "Default", "custom": {"Default": {}}}',
        '{"version": 1, "active": "Default", "custom": {"A": {"Bad-WS": []}}}',
        '{"version": 1, "active": "Default", "custom": {"A": {"home": [{"id": "x"}]}}}',
    ],
)
def test_corrupt_files_fail_closed_and_are_not_overwritten(tmp_path, text):
    root = tmp_path / "data"
    path = layouts_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(LayoutStoreError) as caught:
        load_layouts(root)
    assert text not in str(caught.value)
    assert path.read_text(encoding="utf-8") == text


def test_oversized_file_is_refused(tmp_path):
    root = tmp_path / "data"
    path = layouts_path(root)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"pad": "x" * 600_000}), encoding="utf-8")
    with pytest.raises(LayoutStoreError):
        load_layouts(root)


def test_profile_names_and_limits():
    with pytest.raises(LayoutError):
        with_profile(Layouts(), "default", {})
    with pytest.raises(LayoutError):
        without_profile(Layouts(), "Swing trading")
    with pytest.raises(LayoutError):
        with_active(Layouts(), "Missing")
    layouts = Layouts()
    for i in range(30):
        layouts = with_profile(layouts, f"L{i}", {})
    with pytest.raises(LayoutError):
        with_profile(layouts, "One more", {})
    assert with_profile(layouts, "L3", {"home": SLOTS}).custom["L3"]["home"] == SLOTS
