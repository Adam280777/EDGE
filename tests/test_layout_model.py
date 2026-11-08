from __future__ import annotations

import pytest

from edge.layout import (
    COLS,
    LayoutError,
    PanelSpec,
    Slot,
    clean_name,
    compact,
    default_slots,
    normalize,
    reading_order,
    slots_from_list,
    template,
    update,
)

SPECS = (
    PanelSpec("book", w=12, h=4),
    PanelSpec("alloc", w=6, h=8, views=("donut", "table")),
    PanelSpec("fx", w=6, h=6),
    PanelSpec("status", w=12, h=5, fit=True),
)


def _overlaps(slots):
    vis = [s for s in slots if not s.hidden]
    for i, a in enumerate(vis):
        for b in vis[i + 1:]:
            if a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h:
                return True
    return False


def test_default_flow_keeps_declaration_order_without_gaps():
    slots = default_slots(SPECS)
    assert [s.id for s in reading_order(slots)] == ["book", "alloc", "fx", "status"]
    assert slots[1].h == slots[2].h == 8  # row shares the tallest height
    assert slots[1].view == "donut" and slots[3].fit is True
    assert not _overlaps(slots)
    assert normalize(slots, SPECS) == slots


def test_normalize_drops_unknown_adds_missing_and_clamps():
    raw = (Slot("ghost", 0, 0, 3, 3), Slot("alloc", 11, 0, 1, 99, view="nope"), Slot("alloc", 0, 0, 6, 6))
    out = normalize(raw, SPECS)
    ids = [s.id for s in out]
    assert sorted(ids) == ["alloc", "book", "fx", "status"]
    alloc = next(s for s in out if s.id == "alloc")
    assert alloc.w == 3 and alloc.x + alloc.w <= COLS and alloc.h == 40 and alloc.view == "donut"
    assert not _overlaps(out)


def test_compact_floats_up_and_resolves_overlap_deterministically():
    slots = (Slot("a", 0, 5, 6, 4), Slot("b", 0, 5, 6, 4), Slot("c", 6, 9, 6, 2))
    out = compact(slots)
    assert {s.id: s.y for s in out} == {"a": 0, "b": 4, "c": 0}
    assert compact(slots) == out
    moved = compact(slots, moved="b")
    assert {s.id: s.y for s in moved}["b"] == 0


def test_update_move_resize_hide_and_restore():
    slots = default_slots(SPECS)
    moved = update(slots, SPECS, "status", y=0)
    assert reading_order(moved)[0].id == "status" and not _overlaps(moved)
    resized = update(slots, SPECS, "fx", w=1)
    assert next(s for s in resized if s.id == "fx").w == 3
    hidden = update(slots, SPECS, "alloc", hidden=True)
    assert "alloc" not in [s.id for s in reading_order(hidden)]
    shown = update(hidden, SPECS, "alloc", hidden=False)
    assert reading_order(shown)[-1].id == "alloc" and not _overlaps(shown)
    with pytest.raises(LayoutError):
        update(slots, SPECS, "fx", colour="red")


def test_slot_json_validation_is_strict():
    good = [s.to_dict() for s in default_slots(SPECS)]
    assert slots_from_list(good) == default_slots(SPECS)
    bad_cases = [
        [{**good[0], "x": True}],
        [{**good[0], "extra": 1}],
        [{**good[0], "id": "Bad-Id"}],
        [{**good[0], "w": 0}],
        [good[0], good[0]],
        "not a list",
    ]
    for case in bad_cases:
        with pytest.raises(LayoutError):
            slots_from_list(case)


def test_names_and_specs_are_validated():
    assert clean_name("  My   swing ") == "My swing"
    for bad in ("", "x" * 41, "default", "Long-term", "a\x00b", None):
        with pytest.raises(LayoutError):
            clean_name(bad)
    with pytest.raises(LayoutError):
        PanelSpec("Bad id")
    with pytest.raises(LayoutError):
        PanelSpec("ok", w=2, min_w=3)


def test_builtin_templates_normalize_cleanly():
    home = (
        PanelSpec("book", w=12, h=5), PanelSpec("tape", w=12, h=2, min_h=2), PanelSpec("alloc", w=6, h=9, views=("donut", "pnl", "table")),
        PanelSpec("fx", w=6, h=9), PanelSpec("monitor", w=6, h=9), PanelSpec("pipeline", w=6, h=9),
        PanelSpec("tracks", w=12, h=9), PanelSpec("status", w=12, h=6), PanelSpec("jump", w=12, h=2, min_h=2),
    )
    for profile in ("Swing trading", "Long-term"):
        out = normalize(template(profile, "home"), home)
        assert sorted(s.id for s in out) == sorted(s.id for s in home)
        assert not _overlaps(out)
    assert reading_order(normalize(template("Swing trading", "home"), home))[0].id == "monitor"
    assert template("Nope", "home") == ()
