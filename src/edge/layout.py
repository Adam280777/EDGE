"""Workspace panel layouts: pure, deterministic grid geometry.

A layout places each workspace panel on a 12-column grid measured in columns
(``x``, ``w``) and rows (``y``, ``h``). Everything here is presentation state:
it never touches financial data, Streamlit or the filesystem. ``normalize``
is the single authority that turns any proposed layout (from a saved file, a
built-in template or a browser drag) into a valid, overlap-free arrangement.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from edge.errors import EdgeError

COLS = 12
ROW_PX = 32
GAP_PX = 8
MAX_H = 40
MAX_Y = 600
MAX_PANELS = 40
DEFAULT_PROFILE = "Default"
BUILTIN_PROFILES = (DEFAULT_PROFILE, "Swing trading", "Long-term")
_ID = re.compile(r"[a-z][a-z0-9]{0,23}\Z")
_SLOT_KEYS = frozenset({"id", "x", "y", "w", "h", "hidden", "view", "fit"})


class LayoutError(EdgeError):
    """A layout or layout name is invalid."""


def valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def _int(value: object, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise LayoutError("Layout values must be whole numbers inside the grid.")
    return value


@dataclass(frozen=True)
class PanelSpec:
    """Geometry contract a workspace declares for one panel."""

    id: str
    w: int = 6
    h: int = 8
    min_w: int = 3
    min_h: int = 3
    views: tuple[str, ...] = ()
    fit: bool = False

    def __post_init__(self) -> None:
        if not valid_id(self.id):
            raise LayoutError("Panel ids are short lowercase letters and digits.")
        _int(self.min_w, 1, COLS)
        _int(self.min_h, 1, MAX_H)
        _int(self.w, self.min_w, COLS)
        _int(self.h, self.min_h, MAX_H)
        if any(not valid_id(v) for v in self.views) or len(set(self.views)) != len(self.views):
            raise LayoutError("Panel views must be unique short ids.")


@dataclass(frozen=True)
class Slot:
    """One panel's placement and display choice."""

    id: str
    x: int
    y: int
    w: int
    h: int
    hidden: bool = False
    view: str = ""
    fit: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
                "hidden": self.hidden, "view": self.view, "fit": self.fit}

    @classmethod
    def from_dict(cls, raw: object) -> Slot:
        if not isinstance(raw, Mapping) or set(raw) != _SLOT_KEYS:
            raise LayoutError("Unrecognised panel placement.")
        if not valid_id(raw["id"]):
            raise LayoutError("Unrecognised panel id.")
        view = raw["view"]
        if not isinstance(view, str) or (view and not valid_id(view)):
            raise LayoutError("Unrecognised panel view.")
        if not isinstance(raw["hidden"], bool) or not isinstance(raw["fit"], bool):
            raise LayoutError("Panel flags must be true or false.")
        return cls(
            id=raw["id"],
            x=_int(raw["x"], 0, COLS - 1),
            y=_int(raw["y"], 0, MAX_Y),
            w=_int(raw["w"], 1, COLS),
            h=_int(raw["h"], 1, MAX_H),
            hidden=raw["hidden"],
            view=view,
            fit=raw["fit"],
        )


def slots_from_list(raw: object) -> tuple[Slot, ...]:
    if not isinstance(raw, list) or len(raw) > MAX_PANELS:
        raise LayoutError("A workspace layout is a bounded list of panel placements.")
    slots = tuple(Slot.from_dict(item) for item in raw)
    if len({s.id for s in slots}) != len(slots):
        raise LayoutError("A panel may appear only once per workspace.")
    return slots


def _overlap(a: Slot, b: Slot) -> bool:
    return a.x < b.x + b.w and b.x < a.x + a.w and a.y < b.y + b.h and b.y < a.y + a.h


def default_slots(specs: Sequence[PanelSpec], *, top: int = 0) -> tuple[Slot, ...]:
    """Row flow in declaration order; panels sharing a row share its height.

    Rows never leave holes, so reading order (y, then x) equals declaration order.
    """
    rows: list[list[PanelSpec]] = []
    used = COLS
    for spec in specs:
        if used + spec.w > COLS:
            rows.append([])
            used = 0
        rows[-1].append(spec)
        used += spec.w
    out: list[Slot] = []
    y = top
    for row in rows:
        height = max(s.h for s in row)
        x = 0
        for spec in row:
            out.append(Slot(spec.id, x, y, spec.w, height,
                            view=spec.views[0] if spec.views else "", fit=spec.fit))
            x += spec.w
        y += height
    return tuple(out)


def compact(slots: Iterable[Slot], moved: str | None = None) -> tuple[Slot, ...]:
    """Resolve overlaps and float visible panels upward, deterministically.

    Panels are settled in reading order; on a tie the moved panel goes first so
    dropping onto another panel's top edge takes its place.
    """
    items = list(slots)
    hidden = [s for s in items if s.hidden]
    visible = sorted((s for s in items if not s.hidden),
                     key=lambda s: (s.y, s.id != moved, s.x, s.id))
    placed: list[Slot] = []
    for slot in visible:
        y = slot.y
        while y > 0 and not any(_overlap(replace(slot, y=y - 1), p) for p in placed):
            y -= 1
        while True:
            blockers = [p for p in placed if _overlap(replace(slot, y=y), p)]
            if not blockers:
                break
            y = max(p.y + p.h for p in blockers)
        placed.append(replace(slot, y=y))
    return tuple(sorted(placed, key=lambda s: (s.y, s.x, s.id))) + tuple(hidden)


def normalize(slots: Iterable[Slot], specs: Sequence[PanelSpec],
              moved: str | None = None) -> tuple[Slot, ...]:
    """Clamp to each panel's limits, drop unknown panels, add new ones, compact."""
    known = {spec.id: spec for spec in specs}
    seen: set[str] = set()
    kept: list[Slot] = []
    for slot in slots:
        spec = known.get(slot.id)
        if spec is None or slot.id in seen:
            continue
        seen.add(slot.id)
        w = min(max(slot.w, spec.min_w), COLS)
        view = slot.view if slot.view in spec.views else (spec.views[0] if spec.views else "")
        kept.append(Slot(slot.id, min(max(slot.x, 0), COLS - w), min(max(slot.y, 0), MAX_Y), w,
                         min(max(slot.h, spec.min_h), MAX_H), slot.hidden, view, slot.fit))
    bottom = max((s.y + s.h for s in kept if not s.hidden), default=0)
    kept.extend(default_slots([s for s in specs if s.id not in seen], top=bottom))
    return compact(kept, moved)


def update(slots: Sequence[Slot], specs: Sequence[PanelSpec], panel: str, **changes: object) -> tuple[Slot, ...]:
    """Apply a move/resize/visibility/view change to one panel and re-normalize."""
    allowed = {"x", "y", "w", "h", "hidden", "view", "fit"}
    if not set(changes) <= allowed:
        raise LayoutError("Unsupported layout change.")
    out = [replace(s, **changes) if s.id == panel else s for s in slots]  # type: ignore[arg-type]
    if changes.get("hidden") is False:
        # A re-shown panel joins at the bottom rather than overlapping silently.
        bottom = max((s.y + s.h for s in out if not s.hidden and s.id != panel), default=0)
        out = [replace(s, y=bottom) if s.id == panel else s for s in out]
    return normalize(out, specs, moved=panel)


def reading_order(slots: Iterable[Slot]) -> list[Slot]:
    return sorted((s for s in slots if not s.hidden), key=lambda s: (s.y, s.x, s.id))


def clean_name(name: object) -> str:
    """Validate a user layout name; built-in names are reserved."""
    if not isinstance(name, str):
        raise LayoutError("Layout name is required.")
    text = " ".join(name.split())
    if not 1 <= len(text) <= 40:
        raise LayoutError("Layout names are 1 to 40 characters.")
    if any(unicodedata.category(ch).startswith("C") for ch in text):
        raise LayoutError("Layout names cannot contain control characters.")
    if text.casefold() in {b.casefold() for b in BUILTIN_PROFILES}:
        raise LayoutError("That name is reserved for a built-in layout.")
    return text


def _t(id_: str, x: int, y: int, w: int, h: int, *, hidden: bool = False, view: str = "",
       fit: bool = True) -> Slot:
    return Slot(id_, x, y, w, h, hidden=hidden, view=view, fit=fit)


def _f(id_: str, x: int, y: int, w: int, h: int, **kw: object) -> Slot:
    """Fixed-height template slot (content scrolls inside the panel)."""
    return _t(id_, x, y, w, h, fit=False, **kw)  # type: ignore[arg-type]


# Templates are partial: panels they omit keep their default placement below.
# Templates never hide data-integrity panels (limitations, provenance, status).
TEMPLATES: dict[str, dict[str, tuple[Slot, ...]]] = {
    "Swing trading": {
        "home": (
            _f("monitor", 0, 0, 7, 11),
            _f("tracks", 7, 0, 5, 11),
            _t("book", 0, 11, 12, 5),
            _t("tape", 0, 16, 12, 2),
            _f("pipeline", 0, 18, 6, 9),
            _f("alloc", 6, 18, 6, 9, view="pnl"),
            _f("fx", 0, 27, 6, 7, hidden=True),
            _t("status", 0, 27, 12, 6),
            _t("jump", 0, 33, 12, 2),
        ),
        "decisions": (
            _t("summary", 0, 0, 12, 3, view="list"),
            _t("task", 0, 3, 12, 20),
        ),
        "discovery": (
            _t("status", 0, 0, 12, 5),
            _t("screen", 0, 5, 12, 10),
            _t("results", 0, 15, 12, 16),
            _t("evidence", 0, 31, 12, 10),
            _t("universe", 0, 41, 7, 20, view="timeline"),
            _t("declare", 7, 41, 5, 20),
        ),
        "journal": (
            _t("head", 0, 0, 12, 4),
            _t("entries", 0, 4, 12, 16),
            _t("overview", 0, 20, 12, 10, view="timeline"),
        ),
        "monitoring": (
            _t("kpis", 0, 0, 12, 3),
            _t("track", 0, 3, 7, 12, view="track"),
            _t("status", 7, 3, 5, 12),
            _t("alerts", 0, 15, 12, 12, view="timeline"),
            _t("task", 0, 27, 12, 8),
        ),
        "portfolio": (
            _t("kpis", 0, 0, 12, 3),
            _t("holdings", 0, 3, 12, 14),
            _t("realised", 0, 17, 6, 8, view="dots"),
            _t("snapshot", 6, 17, 6, 8),
            _t("task", 0, 25, 12, 10),
        ),
    },
    "Long-term": {
        "home": (
            _t("book", 0, 0, 12, 5),
            _f("alloc", 0, 5, 7, 10),
            _f("fx", 7, 5, 5, 10),
            _f("pipeline", 0, 15, 12, 9),
            _f("monitor", 0, 24, 6, 9),
            _f("tracks", 6, 24, 6, 9, hidden=True),
            _t("tape", 0, 33, 12, 2, hidden=True),
            _t("status", 0, 33, 12, 6),
            _t("jump", 0, 39, 12, 2),
        ),
        "discovery": (
            _t("status", 0, 0, 12, 5),
            _t("universe", 0, 5, 7, 20, view="coverage"),
            _t("companies", 7, 5, 5, 20),
            _t("declare", 0, 25, 12, 14),
        ),
        "sec": (
            _t("head", 0, 0, 12, 6),
            _t("lookup", 0, 6, 7, 9),
            _t("limits", 7, 6, 5, 9),
            _t("filings", 0, 15, 12, 14),
            _t("identity", 0, 29, 6, 8),
            _t("provenance", 6, 29, 6, 8),
        ),
        "journal": (
            _t("head", 0, 0, 12, 4),
            _t("overview", 0, 4, 12, 12, view="cumulative"),
            _t("entries", 0, 16, 12, 16),
        ),
        "portfolio": (
            _t("kpis", 0, 0, 12, 3),
            _t("snapshot", 0, 3, 12, 9),
            _f("timeline", 0, 12, 12, 12),
            _t("holdings", 0, 24, 12, 14, view="charts"),
            _t("native", 0, 38, 12, 10),
        ),
    },
}


def template(profile: str, workspace: str) -> tuple[Slot, ...]:
    return TEMPLATES.get(profile, {}).get(workspace, ())
