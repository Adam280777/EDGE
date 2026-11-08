"""Terminal presentation primitives; display formatting only, never calculations.

Every helper that emits HTML escapes all dynamic text with ``html.escape``.
Formatting helpers convert recorded values to strings for display; they never
feed a value back into accounting, sizing or risk logic.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

import streamlit as st

Tone = Literal["ink", "amber", "cyan", "green", "red", "violet", "muted"]
SYMBOLS = {"GBP": "£", "USD": "$"}
UNAVAILABLE = "Unavailable"
_CASE_SENSITIVE = re.compile(r"\bGBp\b")


def esc(value: object) -> str:
    # Newlines become character references so a blank line in user text can never
    # end the surrounding HTML block and let the remainder render as Markdown.
    text = html.escape("" if value is None else str(value), quote=True)
    text = text.replace("\r", "").replace("\n", "&#10;")
    # Terminal labels are uppercased by CSS; pence (GBp) must never read as pounds (GBP).
    return _CASE_SENSITIVE.sub(r'<span class="edge-keepcase">\g<0></span>', text)


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def fmt_money(value: object, currency: str | None = None, *, places: int = 2,
              signed: bool = False) -> str:
    """Display string for a recorded amount; ``None`` stays ``Unavailable``."""
    number = _decimal(value)
    if number is None:
        return UNAVAILABLE
    sign = "+" if signed and number > 0 else ""
    prefix = SYMBOLS.get(currency or "", "")
    body = f"{abs(number):,.{places}f}"
    if number < 0:
        sign = "−"
    suffix = "" if prefix or not currency else f" {currency}"
    return f"{sign}{prefix}{body}{suffix}"


def fmt_number(value: object, *, places: int = 2, signed: bool = False) -> str:
    number = _decimal(value)
    if number is None:
        return UNAVAILABLE
    text = f"{number:,.{places}f}"
    return f"+{text}" if signed and number > 0 else text.replace("-", "−")


def fmt_pct(value: object, *, places: int = 1, ratio: bool = True) -> str:
    number = _decimal(value)
    if number is None:
        return UNAVAILABLE
    return f"{number * (100 if ratio else 1):,.{places}f}%".replace("-", "−")


def fmt_time(value: object) -> str:
    if value is None:
        return UNAVAILABLE
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            return value.isoformat()
        return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def tone_for(value: object) -> Tone:
    number = _decimal(value)
    if number is None:
        return "muted"
    return "green" if number > 0 else "red" if number < 0 else "ink"


def html_block(markup: str) -> None:
    """Render trusted static chrome; callers must pass dynamic text through esc()."""
    st.markdown(markup, unsafe_allow_html=True)


def chip(label: str, tone: Tone = "cyan") -> str:
    return f'<span class="edge-chip edge-chip--{tone}">{esc(label)}</span>'


PROVENANCE: dict[str, tuple[str, Tone]] = {
    "manual": ("MANUAL · USER-DECLARED", "cyan"),
    "synthetic": ("SYNTHETIC · NOT MARKET DATA", "violet"),
    "display": ("DISPLAY ONLY · NOT PERSISTED", "amber"),
    "stale": ("STALE / INCOMPLETE", "amber"),
    "unavailable": ("UNAVAILABLE", "muted"),
    "sec": ("SEC EDGAR METADATA", "cyan"),
    "engine": ("NAUTILUS ENGINE OUTPUT", "green"),
    "local": ("LOCAL STORAGE", "muted"),
}


def provenance(*kinds: str) -> str:
    return "".join(chip(*PROVENANCE[kind]) for kind in kinds if kind in PROVENANCE)


_GRIP_ID = re.compile(r"[a-z][a-z0-9]{0,23}\Z")


def panel(code: str, title: str, *, meta: str = "", badges: Sequence[str] = (),
          grip: str = "") -> None:
    """Terminal pane title bar: function code, title, provenance chips, meta.

    ``grip`` (a validated panel id) turns the bar into a layout drag handle.
    """
    tags = provenance(*badges)
    if grip and not _GRIP_ID.match(grip):
        raise ValueError("Invalid panel id for a drag handle.")
    head = (f'<div class="edge-pane-head edge-grip" data-edge-panel="{grip}" title="Drag to move">'
            '<span class="edge-grip-dots" aria-hidden="true">⠿</span>') if grip else (
        '<div class="edge-pane-head">')
    html_block(
        head
        + f'<span class="edge-pane-code">{esc(code)}</span>'
        f'<span class="edge-pane-title">{esc(title)}</span>'
        f'<span class="edge-pane-rule" aria-hidden="true"></span>'
        f'{tags}'
        + (f'<span class="edge-pane-meta">{esc(meta)}</span>' if meta else "")
        + "</div>"
    )


@dataclass(frozen=True)
class Tile:
    label: str
    value: str
    note: str = ""
    tone: Tone = "ink"
    unit: str = ""


def tiles(items: Iterable[Tile], *, columns: int | None = None) -> None:
    cells = []
    for item in items:
        unit = f'<span class="edge-tile-unit">{esc(item.unit)}</span>' if item.unit else ""
        note = f'<div class="edge-tile-note">{esc(item.note)}</div>' if item.note else ""
        muted = " edge-tile--na" if item.value == UNAVAILABLE else ""
        cells.append(
            f'<div class="edge-tile edge-tile--{item.tone}{muted}">'
            f'<div class="edge-tile-label">{esc(item.label)}</div>'
            f'<div class="edge-tile-value">{esc(item.value)}{unit}</div>{note}</div>'
        )
    if not cells:
        return
    style = f' style="--edge-cols:{int(columns)}"' if columns else ""
    html_block(f'<div class="edge-tiles"{style}>{"".join(cells)}</div>')


def kv_grid(rows: Iterable[tuple[str, object]], *, columns: int = 2) -> None:
    """Dense label/value readout grid (replacement for raw field dumps)."""
    cells = "".join(
        f'<div class="edge-kv-k">{esc(key)}</div><div class="edge-kv-v">{esc(value)}</div>'
        for key, value in rows
    )
    if cells:
        html_block(f'<div class="edge-kv" style="--edge-kv-cols:{int(columns)}">{cells}</div>')


def status_light(label: str, state: Literal["ok", "warn", "off", "bad"], detail: str = "") -> str:
    return (
        f'<div class="edge-light edge-light--{state}">'
        f'<span class="edge-light-dot" aria-hidden="true"></span>'
        f'<span class="edge-light-label">{esc(label)}</span>'
        f'<span class="edge-light-state">{esc({"ok": "READY", "warn": "PARTIAL", "off": "OFF", "bad": "FAULT"}[state])}</span>'
        + (f'<span class="edge-light-detail">{esc(detail)}</span>' if detail else "")
        + "</div>"
    )


def status_board(lights: Iterable[str]) -> None:
    html_block(f'<div class="edge-board">{"".join(lights)}</div>')


def ticker_tape(items: Sequence[tuple[str, str, Tone]]) -> None:
    """Scrolling tape of recorded labels; static when reduced motion is preferred."""
    if not items:
        return
    cells = "".join(
        f'<span class="edge-tape-item"><b>{esc(symbol)}</b>'
        f'<span class="edge-tape-v edge-tone--{tone}">{esc(value)}</span></span>'
        for symbol, value, tone in items
    )
    html_block(
        '<div class="edge-tape" role="marquee" aria-label="Recorded holdings and watchlist tape">'
        f'<div class="edge-tape-track">{cells}<span aria-hidden="true">{cells}</span></div></div>'
    )


def empty(message: str, *, hint: str = "") -> None:
    html_block(
        '<div class="edge-empty"><span class="edge-empty-glyph" aria-hidden="true">▒</span>'
        f'<div><div class="edge-empty-msg">{esc(message)}</div>'
        + (f'<div class="edge-empty-hint">{esc(hint)}</div>' if hint else "")
        + "</div></div>"
    )


def section_rule(label: str) -> None:
    html_block(f'<div class="edge-rule-label"><span>{esc(label)}</span></div>')


def legend(entries: Mapping[str, Tone]) -> None:
    html_block(
        '<div class="edge-legend">'
        + "".join(
            f'<span><i class="edge-swatch edge-swatch--{tone}"></i>{esc(name)}</span>'
            for name, tone in entries.items()
        )
        + "</div>"
    )
