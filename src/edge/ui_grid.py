"""Configurable workspace grid: panels users can move, resize, restyle and hide.

Presentation only. Panels receive the same data as before; the grid decides
where and how large they are, never what they compute. Layout edits live in a
session draft until the user explicitly saves; rendering never writes files.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from edge import layout as lay
from edge.layout_store import (
    Layouts,
    LayoutStoreError,
    load_layouts,
    save_layouts,
    with_active,
    with_profile,
    without_profile,
)
from edge.ui_kit import html_block, panel

ROW, GAP, COLS = lay.ROW_PX, lay.GAP_PX, lay.COLS
STATE_LAYOUTS = "edge_layouts"
STATE_ERROR = "edge_layouts_error"
STATE_DRAFT = "edge_layout_draft"
STATE_EDIT = "edge_layout_edit"
STATE_REGISTRY = "edge_layout_registry"
STATE_NOTICE = "edge_layout_notice"


@dataclass(frozen=True)
class PanelContext:
    """What a panel needs to adapt its presentation to its slot."""

    view: str
    w: int
    h: int
    fit: bool
    edit: bool

    @property
    def height_px(self) -> int:
        return self.h * ROW + (self.h - 1) * GAP

    def chart_height(self, reserve: int = 96, low: int = 120, high: int = 900) -> int:
        """Plot height that fills the panel after its header and captions."""
        return max(low, min(high, self.height_px - reserve))

    def per_row(self, min_cols: int = 4, most: int = 4) -> int:
        """How many small multiples fit side by side at this width."""
        return max(1, min(most, self.w // min_cols))


@dataclass(frozen=True)
class Panel:
    id: str
    code: str
    title: str
    render: Callable[[PanelContext], None]
    w: int = 6
    h: int = 9
    min_w: int = 3
    min_h: int = 3
    views: tuple[tuple[str, str], ...] = ()  # (view id, label); first is default
    fit: bool = False
    meta: str = ""
    badges: tuple[str, ...] = ()
    framed: bool = True  # False: bare strip (tape, quick-jump) with no pane chrome

    @property
    def spec(self) -> lay.PanelSpec:
        return lay.PanelSpec(self.id, w=self.w, h=self.h, min_w=self.min_w, min_h=self.min_h,
                             views=tuple(v for v, _ in self.views), fit=self.fit)

    def box_key(self, ws: str) -> str:
        return f"edge_{'box' if self.framed else 'cell'}_{ws}_{self.id}"


# ---------------------------------------------------------------- state helpers

def _layouts() -> Layouts:
    value = st.session_state.get(STATE_LAYOUTS)
    return value if isinstance(value, Layouts) else Layouts()


def _drafts() -> dict[str, tuple[lay.Slot, ...]]:
    value = st.session_state.get(STATE_DRAFT)
    if not isinstance(value, dict):
        value = {}
        st.session_state[STATE_DRAFT] = value
    return value


def editing() -> bool:
    return bool(st.session_state.get(STATE_EDIT, False))


def profile_slots(layouts: Layouts, ws: str) -> tuple[lay.Slot, ...]:
    """Raw saved/template slots for a workspace (normalised later against specs)."""
    if layouts.active in layouts.custom:
        return layouts.custom[layouts.active].get(ws, ())
    return lay.template(layouts.active, ws)


def current_slots(ws: str, specs: Sequence[lay.PanelSpec]) -> tuple[lay.Slot, ...]:
    draft = _drafts().get(ws)
    return lay.normalize(draft if draft is not None else profile_slots(_layouts(), ws), specs)


def _with_absent(slots: Sequence[lay.Slot], base: Sequence[lay.Slot]) -> tuple[lay.Slot, ...]:
    """Keep placements of panels not on screen now (e.g. another task's form) from `base`."""
    present = {s.id for s in slots}
    return tuple(slots) + tuple(s for s in base if s.id not in present)


def _set_draft(ws: str, slots: tuple[lay.Slot, ...], *, carry: bool = True) -> None:
    drafts = _drafts()
    if carry:
        base = drafts.get(ws)
        slots = _with_absent(slots, base if base is not None else profile_slots(_layouts(), ws))
    drafts[ws] = slots


def _specs(ws: str) -> tuple[lay.PanelSpec, ...]:
    entry = st.session_state.get(STATE_REGISTRY, {}).get(ws)
    return entry["specs"] if entry else ()


def _change(ws: str, panel_id: str, **changes: object) -> None:
    specs = _specs(ws)
    if specs:
        _set_draft(ws, lay.update(current_slots(ws, specs), specs, panel_id, **changes))


def _from_widget(ws: str, panel_id: str, field: str) -> None:
    value = st.session_state.get(f"edge_lay_{ws}_{panel_id}_{field}")
    if field == "fit":
        _change(ws, panel_id, fit=bool(value))
    elif field == "view":
        _change(ws, panel_id, view=str(value))
    elif isinstance(value, int):
        _change(ws, panel_id, **{field: value})


def _nudge(ws: str, panel_id: str, step: int) -> None:
    specs = _specs(ws)
    slots = current_slots(ws, specs)
    order = lay.reading_order(slots)
    ids = [s.id for s in order]
    if panel_id not in ids:
        return
    i = ids.index(panel_id)
    j = i + step
    if not 0 <= j < len(order):
        return
    other = order[j]
    y = other.y if step < 0 else other.y + other.h
    _set_draft(ws, lay.update(slots, specs, panel_id, y=y))


# ---------------------------------------------------------------- drag component

_DRAG_JS = r"""
const S = (window.__edgeGrid = window.__edgeGrid || {});

function boxSel(id) {
  return `[class*="st-key-edge_box_${S.data.ws}_${id}"], [class*="st-key-edge_cell_${S.data.ws}_${id}"]`;
}
function gridEl() { return document.querySelector(`.st-key-edge_grid_${S.data.ws}`); }
function wrapperOf(grid, id) {
  for (const child of grid.children) {
    const box = child.firstElementChild;
    if (box && box.matches(boxSel(id))) return [child, box];
  }
  return [null, null];
}
function overlap(a, b) { return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h; }
function compact(slots, moved) {
  const vis = slots.filter(s => !s.hidden).map(s => ({...s}));
  vis.sort((a, b) => (a.y - b.y) || ((a.id !== moved) - (b.id !== moved)) || (a.x - b.x) || (a.id < b.id ? -1 : 1));
  const placed = [];
  for (const s of vis) {
    while (s.y > 0 && !placed.some(p => overlap({...s, y: s.y - 1}, p))) s.y -= 1;
    for (;;) {
      const hit = placed.filter(p => overlap(s, p));
      if (!hit.length) break;
      s.y = Math.max(...hit.map(p => p.y + p.h));
    }
    placed.push(s);
  }
  return placed.concat(slots.filter(s => s.hidden));
}
function px(h) { return h * S.data.row + (h - 1) * S.data.gap; }
function paint(grid, slots, active) {
  for (const s of slots) {
    if (s.hidden) continue;
    const [wrap, box] = wrapperOf(grid, s.id);
    if (!wrap) continue;
    wrap.style.gridColumn = `${s.x + 1} / span ${s.w}`;
    wrap.style.gridRow = `${s.y + 1} / span ${s.h}`;
    if (box) box.style[s.fit ? "minHeight" : "height"] = `${px(s.h)}px`;
    wrap.classList.toggle("edge-grid-active", s.id === active);
  }
}
function clear() {
  const grid = S.data && gridEl();
  if (!grid) return;
  grid.classList.remove("edge-grid-dragging");
  for (const wrap of grid.children) {
    wrap.style.gridColumn = ""; wrap.style.gridRow = "";
    wrap.classList.remove("edge-grid-active");
    const box = wrap.firstElementChild;
    if (box) { box.style.height = ""; box.style.minHeight = ""; }
  }
}
function onDown(event) {
  if (event.button !== 0 || !S.data) return;
  const handle = event.target.closest(".edge-grip, .edge-resize");
  const grid = gridEl();
  if (!handle || !grid || !grid.contains(handle)) return;
  if (event.target.closest("button, input, a, [role='button']") && !handle.classList.contains("edge-resize")) return;
  const id = handle.dataset.edgePanel;
  const start = S.data.slots.find(s => s.id === id);
  if (!start) return;
  event.preventDefault();
  const resize = handle.classList.contains("edge-resize");
  const rect = grid.getBoundingClientRect();
  const colW = (rect.width - (S.data.cols - 1) * S.data.gap) / S.data.cols;
  const stepX = colW + S.data.gap, stepY = S.data.row + S.data.gap;
  const lim = S.data.limits[id];
  const x0 = event.clientX, y0 = event.clientY;
  let proposal = null;
  grid.classList.add("edge-grid-dragging");
  const move = (e) => {
    const dx = Math.round((e.clientX - x0) / stepX), dy = Math.round((e.clientY - y0) / stepY);
    const s = {...start};
    if (resize) {
      s.w = Math.max(lim.min_w, Math.min(S.data.cols - s.x, start.w + dx));
      s.h = Math.max(lim.min_h, Math.min(S.data.max_h, start.h + dy));
    } else {
      s.x = Math.max(0, Math.min(S.data.cols - s.w, start.x + dx));
      s.y = Math.max(0, start.y + dy);
    }
    proposal = compact(S.data.slots.map(o => o.id === id ? s : o), id);
    paint(grid, proposal, id);
  };
  const stop = (e, cancel) => {
    document.removeEventListener("pointermove", move);
    document.removeEventListener("pointerup", up);
    document.removeEventListener("keydown", key);
    const changed = proposal && JSON.stringify(proposal.find(p => p.id === id)) !== JSON.stringify(start)
      || proposal && proposal.some(p => { const o = S.data.slots.find(q => q.id === p.id); return o && (o.x !== p.x || o.y !== p.y); });
    if (cancel || !changed) { clear(); return; }
    S.send("layout", JSON.stringify({ws: S.data.ws, rev: S.data.rev, slots: proposal}));
  };
  const up = (e) => stop(e, false);
  const key = (e) => { if (e.key === "Escape") stop(e, true); };
  document.addEventListener("pointermove", move);
  document.addEventListener("pointerup", up);
  document.addEventListener("keydown", key);
}

export default function (component) {
  S.data = component.data;
  S.send = component.setTriggerValue;
  clear();
  if (!S.bound) {
    S.bound = true;
    document.addEventListener("pointerdown", onDown, true);
  }
  return () => {};
}
"""

_drag = st.components.v2.component(
    "edge_layout_drag", html='<span class="edge-layout-drag" hidden></span>', js=_DRAG_JS
)


def _accept(ws: str, raw: object) -> None:
    """Validate a drag payload and make it the draft (runs before the script body)."""
    if not raw:
        return
    entry = st.session_state.get(STATE_REGISTRY, {}).get(ws)
    if entry is None:
        return
    specs = entry["specs"]
    try:
        payload = json.loads(raw) if isinstance(raw, str) else None
        if not isinstance(payload, dict) or payload.get("ws") != ws:
            raise lay.LayoutError("Wrong workspace.")
        proposed = lay.slots_from_list(payload.get("slots"))
    except (ValueError, lay.LayoutError):
        st.session_state[f"edge_layout_rejected_{ws}"] = True
        return
    _set_draft(ws, lay.normalize(proposed, specs))


def _on_drag(ws: str) -> None:
    state = st.session_state.get(f"edge_layout_drag_{ws}")
    _accept(ws, state.get("layout") if hasattr(state, "get") else None)


def _drag_bridge(ws: str, slots: tuple[lay.Slot, ...], specs: Sequence[lay.PanelSpec]) -> None:
    # The change callback has already applied any new payload, so `slots` is current and the
    # browser receives the arrangement it should build the next gesture on.
    if st.session_state.pop(f"edge_layout_rejected_{ws}", False):
        st.warning("That layout change was rejected; the previous arrangement is kept.")
    rev = int(st.session_state.get(f"edge_layout_rev_{ws}", 0)) + 1
    st.session_state[f"edge_layout_rev_{ws}"] = rev
    _drag(
        key=f"edge_layout_drag_{ws}",
        data={
            "ws": ws, "rev": rev, "cols": COLS, "row": ROW, "gap": GAP, "max_h": lay.MAX_H,
            "slots": [s.to_dict() for s in slots],
            "limits": {s.id: {"min_w": s.min_w, "min_h": s.min_h} for s in specs},
        },
        on_layout_change=functools.partial(_on_drag, ws),
    )


# ---------------------------------------------------------------- CSS

def _css(ws: str, panels: Sequence[Panel], slots: Sequence[lay.Slot], edit: bool) -> str:
    by_id = {p.id: p for p in panels}
    grid = f".st-key-edge_grid_{ws}"
    rules = [
        (f"{grid}{{display:grid!important;grid-template-columns:repeat({COLS},minmax(0,1fr));"
         f"grid-auto-rows:minmax({ROW}px,auto);gap:{GAP}px;align-items:stretch}}"),
        f"{grid}>[data-testid=\"stLayoutWrapper\"]{{min-width:0;min-height:0;width:auto!important}}",
        f"{grid}>[data-testid=\"stLayoutWrapper\"]>div{{width:100%;box-sizing:border-box}}",
    ]
    for slot in slots:
        if slot.hidden or slot.id not in by_id:
            continue
        key = by_id[slot.id].box_key(ws)
        height = slot.h * ROW + (slot.h - 1) * GAP
        size = f"min-height:{height}px" if slot.fit else f"height:{height}px;overflow:auto"
        rules.append(
            f"{grid}>[data-testid=\"stLayoutWrapper\"]:has(>.st-key-{key})"
            f"{{grid-column:{slot.x + 1}/span {slot.w};grid-row:{slot.y + 1}/span {slot.h}}}"
            f".st-key-{key}{{{size}}}"
        )
    rules.append(
        "@media (max-width:1050px){"
        f"{grid}{{display:flex!important;flex-direction:column}}"
        f"{grid} [class*=\"st-key-edge_\"]{{height:auto!important;max-height:none}}"
        f"{grid} .edge-resize{{display:none}}}}"
    )
    if edit:
        wrap = f'{grid}>[data-testid="stLayoutWrapper"]'
        rules.append(
            f"{grid}{{outline:1px dashed rgba(101,220,232,.35);outline-offset:4px}}"
            # Wrapper is the positioning context so the handle stays put while the box scrolls.
            f"{wrap}{{position:relative}}{wrap}>div{{position:static!important}}"
            f'{grid} [data-testid="stElementContainer"]:has(.edge-resize)'
            "{position:absolute!important;right:0;bottom:0;width:18px!important;height:18px;"
            "margin:0;z-index:6}"
            ".edge-resize{width:18px;height:18px;cursor:nwse-resize;touch-action:none;"
            "background:linear-gradient(135deg,transparent 50%,rgba(101,220,232,.75) 50%,"
            "rgba(101,220,232,.75) 60%,transparent 60%,transparent 70%,rgba(101,220,232,.75) 70%,"
            "rgba(101,220,232,.75) 80%,transparent 80%)}"
            ".edge-resize:hover{background-color:rgba(101,220,232,.12)}"
            ".edge-grip{cursor:grab;user-select:none;touch-action:none;flex:1 1 auto;"
            "border-left:3px solid #65dce8}"
            ".edge-grip:hover{background:rgba(101,220,232,.08)}"
            ".edge-grip-dots{color:#65dce8;margin-right:.5rem;letter-spacing:-.1em}"
            f"{grid}.edge-grid-dragging,{grid}.edge-grid-dragging *{{cursor:grabbing!important;"
            "user-select:none!important}"
            f"{grid}.edge-grid-dragging>[data-testid=\"stLayoutWrapper\"]"
            "{transition:none;opacity:.72}"
            f"{grid}>.edge-grid-active{{opacity:1!important;outline:2px solid #ffb000;"
            "outline-offset:2px;z-index:4;box-shadow:0 0 18px rgba(255,176,0,.35)}"
            f'{grid} [class*="st-key-edge_lay_head_"]{{flex-wrap:nowrap}}'
            f'{grid} [class*="st-key-edge_lay_head_"]>[data-testid="stElementContainer"]'
            ":first-child{flex:1 1 auto;min-width:0}"
        )
    return "<style>" + "".join(rules) + "</style>"


# ---------------------------------------------------------------- panel chrome

def _controls(ws: str, item: Panel, slot: lay.Slot) -> None:
    prefix = f"edge_lay_{ws}_{item.id}"
    for field, value in (("w", slot.w), ("h", slot.h), ("x", slot.x), ("fit", slot.fit),
                         ("view", slot.view)):
        st.session_state[f"{prefix}_{field}"] = value
    with st.popover("Panel options", icon=":material/tune:", key=f"{prefix}_menu"):
        st.caption(f"{item.code} · {item.title} — keyboard alternative to dragging")
        if len(item.views) > 1:
            labels = dict(item.views)
            st.radio("Display as", list(labels), format_func=labels.__getitem__,
                     key=f"{prefix}_view", on_change=_from_widget, args=(ws, item.id, "view"),
                     horizontal=True)
        st.slider("Width (of 12 columns)", item.min_w, COLS, key=f"{prefix}_w",
                  on_change=_from_widget, args=(ws, item.id, "w"))
        st.slider("Height (rows)", item.min_h, lay.MAX_H, key=f"{prefix}_h",
                  on_change=_from_widget, args=(ws, item.id, "h"))
        if slot.w < COLS:
            st.slider("Column position", 0, COLS - slot.w, key=f"{prefix}_x",
                      on_change=_from_widget, args=(ws, item.id, "x"))
        st.toggle("Grow to fit content", key=f"{prefix}_fit", on_change=_from_widget,
                  args=(ws, item.id, "fit"),
                  help="Off: fixed height with scrolling. On: the panel grows to show everything.")
        up, down, hide = st.columns(3, gap="small")
        up.button("Move earlier", key=f"{prefix}_up", on_click=_nudge, args=(ws, item.id, -1),
                  icon=":material/arrow_upward:", width="stretch")
        down.button("Move later", key=f"{prefix}_down", on_click=_nudge, args=(ws, item.id, 1),
                    icon=":material/arrow_downward:", width="stretch")
        hide.button("Hide panel", key=f"{prefix}_hide", on_click=_change, args=(ws, item.id),
                    kwargs={"hidden": True}, icon=":material/visibility_off:", width="stretch")


def _chrome(ws: str, item: Panel, slot: lay.Slot, edit: bool) -> None:
    if edit:
        with st.container(horizontal=True, vertical_alignment="center", gap="small",
                          key=f"edge_lay_head_{ws}_{item.id}"):
            panel(item.code, item.title, meta=f"{slot.w}×{slot.h}", badges=item.badges,
                  grip=item.id)
            _controls(ws, item, slot)
        html_block(f'<div class="edge-resize" data-edge-panel="{item.id}" title="Drag to resize"'
                   ' aria-hidden="true"></div>')
    elif item.framed:
        panel(item.code, item.title, meta=item.meta, badges=item.badges)


def grid(ws: str, panels: Sequence[Panel]) -> dict[str, PanelContext]:
    """Render panels in the user's layout; returns the context each panel got."""
    if not lay.valid_id(ws):
        raise lay.LayoutError("Workspace ids are short lowercase letters and digits.")
    specs = tuple(p.spec for p in panels)
    if len({s.id for s in specs}) != len(specs):
        raise lay.LayoutError("Duplicate panel id.")
    registry = st.session_state.setdefault(STATE_REGISTRY, {})
    registry[ws] = {"specs": specs, "titles": {p.id: f"{p.code} · {p.title}" for p in panels}}
    st.session_state["edge_layout_current"] = ws
    edit = editing()
    slots = current_slots(ws, specs)
    if edit:
        _drag_bridge(ws, slots, specs)
    by_id = {p.id: p for p in panels}
    html_block(_css(ws, panels, slots, edit))
    contexts: dict[str, PanelContext] = {}
    with st.container(key=f"edge_grid_{ws}"):
        for slot in lay.reading_order(slots):
            item = by_id[slot.id]
            ctx = PanelContext(view=slot.view, w=slot.w, h=slot.h, fit=slot.fit, edit=edit)
            contexts[item.id] = ctx
            with st.container(key=item.box_key(ws)):
                _chrome(ws, item, slot, edit)
                item.render(ctx)
    return contexts


# ---------------------------------------------------------------- toolbar

def load_into_session(data_root: Path) -> None:
    """Read saved layouts once per session; never creates or repairs the file."""
    if STATE_LAYOUTS in st.session_state:
        return
    try:
        st.session_state[STATE_LAYOUTS] = load_layouts(data_root)
        st.session_state[STATE_ERROR] = False
    except LayoutStoreError:
        st.session_state[STATE_LAYOUTS] = Layouts()
        st.session_state[STATE_ERROR] = True


def _dirty() -> bool:
    return bool(_drafts())


def _persist(data_root: Path, layouts: Layouts, notice: str) -> bool:
    if st.session_state.get(STATE_ERROR):
        st.session_state[STATE_NOTICE] = ("error", "Saved layouts file is unreadable; nothing was written.")
        return False
    try:
        save_layouts(data_root, layouts)
    except (LayoutStoreError, lay.LayoutError):
        st.session_state[STATE_NOTICE] = ("error", "Layouts could not be saved. Nothing was changed on disk.")
        return False
    st.session_state[STATE_LAYOUTS] = layouts
    st.session_state[STATE_NOTICE] = ("ok", notice)
    return True


def _switch(data_root: Path) -> None:
    name = st.session_state.get("edge_layout_profile")
    layouts = _layouts()
    if not isinstance(name, str) or name == layouts.active:
        return
    switched = with_active(layouts, name)
    st.session_state[STATE_DRAFT] = {}
    if st.session_state.get(STATE_ERROR):
        st.session_state[STATE_LAYOUTS] = switched  # session only: the file is untouched
    else:
        _persist(data_root, switched, f"Layout “{name}” active.")


def _snapshot_profile(layouts: Layouts) -> dict[str, tuple[lay.Slot, ...]]:
    """Every workspace's arrangement as currently shown, for saving as a profile."""
    spaces: dict[str, tuple[lay.Slot, ...]] = {}
    base = layouts.custom.get(layouts.active)
    if base is not None:
        spaces.update(base)
    else:
        spaces.update(lay.TEMPLATES.get(layouts.active, {}))
    registry = st.session_state.get(STATE_REGISTRY, {})
    for ws, slots in _drafts().items():
        specs = registry.get(ws, {}).get("specs")
        spaces[ws] = _with_absent(lay.normalize(slots, specs), slots) if specs else slots
    return spaces


def _save(data_root: Path) -> None:
    layouts = _layouts()
    if layouts.active not in layouts.custom:
        st.session_state[STATE_NOTICE] = ("error", "Built-in layouts are read-only. Use Save as new.")
        return
    if _persist(data_root, with_profile(layouts, layouts.active, _snapshot_profile(layouts)),
                f"Saved “{layouts.active}”."):
        st.session_state[STATE_DRAFT] = {}


def _save_as(data_root: Path) -> None:
    layouts = _layouts()
    try:
        name = lay.clean_name(st.session_state.get("edge_layout_new_name", ""))
        if name in layouts.custom:
            raise lay.LayoutError("A saved layout already has that name.")
        updated = with_profile(layouts, name, _snapshot_profile(layouts))
    except lay.LayoutError as error:
        st.session_state[STATE_NOTICE] = ("error", str(error))
        return
    if _persist(data_root, updated, f"Saved new layout “{name}”."):
        st.session_state[STATE_DRAFT] = {}
        st.session_state["edge_layout_profile"] = name
        st.session_state["edge_layout_new_name"] = ""


def _delete(data_root: Path) -> None:
    layouts = _layouts()
    try:
        updated = without_profile(layouts, layouts.active)
    except lay.LayoutError as error:
        st.session_state[STATE_NOTICE] = ("error", str(error))
        return
    if _persist(data_root, updated, f"Deleted “{layouts.active}”."):
        st.session_state[STATE_DRAFT] = {}
        st.session_state["edge_layout_profile"] = updated.active


def _discard() -> None:
    st.session_state[STATE_DRAFT] = {}
    st.session_state[STATE_NOTICE] = ("ok", "Unsaved layout changes discarded.")


def _reset(ws: str) -> None:
    specs = _specs(ws)
    if specs:
        _set_draft(ws, lay.default_slots(specs), carry=False)


def _restore(ws: str) -> None:
    panel_id = st.session_state.get(f"edge_layout_restore_{ws}")
    if isinstance(panel_id, str):
        _change(ws, panel_id, hidden=False)


TOOLBAR_CSS = """<style>
[data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]>:has(.edge-mast),
[data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]>.st-key-edge_workspace,
[data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]>:has(.edge-crumb){order:-2}
[data-testid="stMainBlockContainer"]>[data-testid="stVerticalBlock"]>:has(>.st-key-edge_layout_bar){order:-1}
</style>"""


def toolbar(data_root: Path) -> None:
    """Layout controls. Rendered after the workspace (so it knows its panels) and
    moved visually under the breadcrumb with CSS flex order."""
    ws = st.session_state.get("edge_layout_current")
    layouts = _layouts()
    names = layouts.names()
    if st.session_state.get("edge_layout_profile") not in names:
        st.session_state["edge_layout_profile"] = layouts.active
    edit = editing()
    dirty = _dirty()
    html_block(TOOLBAR_CSS)
    with st.container(key="edge_layout_bar", horizontal=True, vertical_alignment="center",
                      gap="small"):
        st.selectbox("Layout", names, key="edge_layout_profile", on_change=_switch,
                     args=(data_root,), disabled=dirty, label_visibility="collapsed",
                     help="Save or discard layout changes before switching." if dirty else
                     "Choose a saved arrangement for every workspace.", width=220)
        st.toggle("Edit layout", key=STATE_EDIT,
                  help="Drag panel title bars to move, the corner grip to resize; "
                       "panel options change the display, size and visibility.")
        registry = st.session_state.get(STATE_REGISTRY, {}).get(ws) if ws else None
        if edit and registry is None:
            st.caption("This workspace has a fixed layout.")
        if edit:
            custom = layouts.active in layouts.custom
            blocked = bool(st.session_state.get(STATE_ERROR))
            st.button("Save", key="edge_layout_save", on_click=_save, args=(data_root,),
                      disabled=not dirty or not custom or blocked, icon=":material/save:",
                      help=None if custom else "Built-in layouts are read-only — use Save as new.")
            with st.popover("Save as new", icon=":material/library_add:", disabled=blocked):
                st.text_input("Layout name", key="edge_layout_new_name", max_chars=40,
                              placeholder="e.g. Morning swing scan")
                st.button("Save layout", key="edge_layout_save_as", on_click=_save_as,
                          args=(data_root,), type="primary")
            st.button("Discard changes", key="edge_layout_discard", on_click=_discard,
                      disabled=not dirty, icon=":material/undo:")
            if registry is not None:
                st.button("Reset workspace", key="edge_layout_reset", on_click=_reset, args=(ws,),
                          icon=":material/restart_alt:",
                          help="Return this workspace to the default arrangement (unsaved until you save).")
                specs = registry["specs"]
                hidden = [s.id for s in current_slots(ws, specs) if s.hidden]
                if hidden:
                    with st.popover(f"Hidden panels · {len(hidden)}", icon=":material/visibility:"):
                        titles = registry["titles"]
                        st.selectbox("Panel", hidden, format_func=titles.__getitem__,
                                     key=f"edge_layout_restore_{ws}")
                        st.button("Show panel", key=f"edge_layout_show_{ws}", on_click=_restore,
                                  args=(ws,), type="primary")
            if custom:
                with st.popover("Delete layout", icon=":material/delete:", disabled=blocked):
                    st.caption(f"Permanently delete “{layouts.active}”? Built-in layouts are unaffected.")
                    st.button("Delete", key="edge_layout_delete", on_click=_delete,
                              args=(data_root,), type="primary")
        if dirty:
            html_block('<span class="edge-chip edge-chip--amber">UNSAVED LAYOUT</span>')
    if st.session_state.get(STATE_ERROR):
        st.error("Saved layouts file is unreadable. Built-in layouts are in use and nothing "
                 "was overwritten; inspect settings/layouts-v1.json.")
    notice = st.session_state.pop(STATE_NOTICE, None)
    if notice:
        (st.error if notice[0] == "error" else st.toast)(notice[1])


def begin_page() -> None:
    """Call once per run before rendering so the toolbar only sees this page's grid."""
    st.session_state.pop("edge_layout_current", None)
