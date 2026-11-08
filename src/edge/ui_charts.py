"""Altair/Vega-Lite builders for the terminal theme; presentation only.

Inputs are recorded values supplied by callers. Builders convert numbers to
floats solely for plotting geometry; exact strings stay in tooltips. A builder
returns ``None`` when there is nothing truthful to draw, so callers show an
explicit empty state rather than an invented chart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import altair as alt
import streamlit as st

VOID = "#050505"
PANEL = "#10100d"
RULE = "#6e6045"
GRID = "#2a2418"
INK = "#ffc15a"
MUTED = "#c6ad83"
AMBER = "#ffb000"
CYAN = "#65dce8"
GREEN = "#70e49a"
RED = "#ff817a"
VIOLET = "#b48cff"
FONT = "Cascadia Mono, Cascadia Code, Consolas, Courier New, monospace"
SERIES = [AMBER, CYAN, GREEN, VIOLET, RED, MUTED, "#e8d27a", "#4fa3ad"]
TONES = {"ink": INK, "amber": AMBER, "cyan": CYAN, "green": GREEN, "red": RED,
         "violet": VIOLET, "muted": MUTED}


def num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _style(chart: alt.TopLevelMixin, height: int | None = None) -> alt.TopLevelMixin:
    if height is not None and not isinstance(chart, (alt.VConcatChart, alt.HConcatChart)):
        chart = chart.properties(height=height)
    return (
        chart.configure(background="transparent", font=FONT,
                        padding={"top": 14, "left": 8, "right": 10, "bottom": 8})
        .configure_view(stroke=RULE, strokeWidth=1, fill=VOID)
        .configure_axis(
            labelColor=MUTED, titleColor=CYAN, gridColor=GRID, domainColor=RULE,
            tickColor=RULE, labelFont=FONT, titleFont=FONT, labelFontSize=11,
            titleFontSize=11, titleFontWeight="normal", gridDash=[2, 3],
        )
        .configure_legend(
            labelColor=INK, titleColor=CYAN, labelFont=FONT, titleFont=FONT,
            symbolType="square", orient="bottom", titleFontWeight="normal",
        )
        .configure_title(color=CYAN, font=FONT, fontSize=12, anchor="start",
                         fontWeight="normal")
        .configure_text(font=FONT, color=INK)
    )


def show(chart: alt.TopLevelMixin | None, *, alt_text: str, height: int | None = None,
         key: str | None = None) -> bool:
    """Render a themed chart; returns False (render nothing) for ``None``."""
    if chart is None:
        return False
    st.altair_chart(_style(chart, height), width="stretch", theme=None, alt=alt_text, key=key)
    return True


def _fit(rows: int, per_row: int, *, chrome: int = 72) -> int:
    """Total height: Streamlit autosizes with `fit`, so title and axes eat into it."""
    return chrome + per_row * max(1, rows)


def _plain(value: Any) -> Any:
    """JSON-safe copy for inline chart data; Decimals stay exact as strings."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _values(rows: Sequence[Mapping[str, Any]]) -> alt.Data:
    return alt.Data(values=[{k: _plain(v) for k, v in row.items()} for row in rows])


def donut(rows: Sequence[Mapping[str, Any]], *, label: str = "label", value: str = "value",
          title: str = "", center: str = "", detail: str = "") -> alt.Chart | None:
    data = [dict(row, _v=num(row[value])) for row in rows if (num(row[value]) or 0) > 0]
    if not data:
        return None
    base = alt.Chart(_values(data)).encode(
        theta=alt.Theta("_v:Q", stack=True),
        color=alt.Color(f"{label}:N", scale=alt.Scale(range=SERIES),
                        legend=alt.Legend(title=None, columns=2, labelLimit=160, orient="bottom")),
        tooltip=[alt.Tooltip(f"{label}:N", title="Item"), alt.Tooltip(f"{detail or value}:N", title="Recorded")],
    )
    # Radii follow the plot size so the ring never overflows narrow columns.
    outer = alt.expr("max(30, min(width, height) / 2 - 4)")
    inner = alt.expr("max(18, (min(width, height) / 2 - 4) * 0.62)")
    arc = base.mark_arc(innerRadius=inner, outerRadius=outer, stroke=VOID, strokeWidth=2)
    layers = [arc]
    if center:
        layers.append(alt.Chart(_values([{"t": center}])).mark_text(
            fontSize=15, color=INK, fontWeight="bold").encode(text="t:N"))
    return alt.layer(*layers).properties(title=title)


def hbars(rows: Sequence[Mapping[str, Any]], *, label: str = "label", value: str = "value",
          color: str | None = None, title: str = "", x_title: str = "",
          text: str | None = None, domain_zero: bool = True,
          integer: bool = False) -> alt.Chart | None:
    data = [dict(row, _v=num(row[value])) for row in rows if num(row[value]) is not None]
    if not data:
        return None
    order = [row[label] for row in data]
    enc_color = (
        alt.Color(f"{color}:N", scale=alt.Scale(range=SERIES), legend=alt.Legend(title=None))
        if color else alt.value(AMBER)
    )
    values = [row["_v"] for row in data]
    low, high = min(min(values), 0.0), max(max(values), 0.0)
    span = (high - low) or 1.0
    # Head-room so end-of-bar value labels are not clipped by the plot edge.
    domain = [low - (0.3 * span if low < 0 else 0.0), high + (0.3 * span if high > 0 else 0.0)]
    axis = alt.Axis(tickMinStep=1, format="d") if integer else alt.Axis()
    base = alt.Chart(_values(data)).encode(
        y=alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("_v:Q", title=x_title, stack=None, axis=axis,
                scale=alt.Scale(zero=domain_zero, domain=domain, nice=False)),
        tooltip=[alt.Tooltip(f"{label}:N", title="Item"),
                 alt.Tooltip(f"{text or value}:N", title="Recorded")],
    )
    bars = base.mark_bar(color=AMBER, height={"band": 0.62}).encode(color=enc_color)
    labels = base.mark_text(align="left", dx=4, color=INK, fontSize=11).encode(
        text=f"{text or value}:N")
    return alt.layer(bars, labels).properties(title=title, height=_fit(len(data), 34))


def grouped_bars(rows: Sequence[Mapping[str, Any]], *, category: str, series: str,
                 value: str = "value", title: str = "", y_title: str = "",
                 text: str | None = None) -> alt.Chart | None:
    data = [dict(row, _v=num(row[value])) for row in rows if num(row[value]) is not None]
    if not data:
        return None
    return alt.Chart(_values(data)).mark_bar().encode(
        x=alt.X(f"{category}:N", title=None, axis=alt.Axis(labelAngle=0)),
        xOffset=f"{series}:N",
        y=alt.Y("_v:Q", title=y_title),
        color=alt.Color(f"{series}:N", scale=alt.Scale(range=[MUTED, AMBER, CYAN, GREEN]),
                        legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip(f"{category}:N"), alt.Tooltip(f"{series}:N"),
                 alt.Tooltip(f"{text or value}:N", title="Recorded")],
    ).properties(title=title)


def price_ladder(levels: Sequence[Mapping[str, Any]], *, zone: tuple[Any, Any] | None = None,
                 risk: tuple[Any, Any] | None = None, title: str = "",
                 unit: str = "") -> alt.Chart | None:
    """Horizontal level ladder: stop/entry/targets/quotes with shaded zones.

    ``levels`` rows: ``label``, ``price`` (number-like), ``kind`` in
    stop/entry/target/quote/info, optional ``note``.
    """
    rows = [
        {"label": row["label"], "price": num(row["price"]), "kind": row.get("kind", "info"),
         "shown": f"{row['price']} {unit}".strip(), "note": row.get("note", "")}
        for row in levels if num(row.get("price")) is not None
    ]
    if not rows:
        return None
    kind_colors = alt.Scale(domain=["stop", "entry", "target", "quote", "info"],
                            range=[RED, AMBER, GREEN, CYAN, MUTED])
    layers: list[alt.Chart] = []
    for band, color, opacity in ((risk, RED, 0.16), (zone, AMBER, 0.22)):
        if band and num(band[0]) is not None and num(band[1]) is not None:
            low, high = sorted((num(band[0]), num(band[1])))
            layers.append(alt.Chart(_values([{"lo": low, "hi": high}])).mark_rect(
                color=color, opacity=opacity).encode(y="lo:Q", y2="hi:Q"))
    targets = [row["price"] for row in rows if row["kind"] == "target"]
    entries = [row["price"] for row in rows if row["kind"] == "entry"]
    if targets and entries:
        low, high = sorted((max(entries), max(targets)))
        layers.append(alt.Chart(_values([{"lo": low, "hi": high}])).mark_rect(
            color=GREEN, opacity=0.08).encode(y="lo:Q", y2="hi:Q"))
    base = alt.Chart(_values(rows))
    layers.append(base.mark_rule(strokeWidth=2).encode(
        y=alt.Y("price:Q", title=f"Price {unit}".strip(), scale=alt.Scale(zero=False, nice=True)),
        color=alt.Color("kind:N", scale=kind_colors, legend=None),
        strokeDash=alt.condition(alt.datum.kind == "quote", alt.value([5, 3]), alt.value([1, 0])),
        tooltip=[alt.Tooltip("label:N"), alt.Tooltip("shown:N", title="Level"),
                 alt.Tooltip("note:N")],
    ))
    layers.append(base.mark_text(align="left", baseline="bottom", dy=-3, x=6, fontSize=11).encode(
        y="price:Q", text=alt.Text("label:N"),
        color=alt.Color("kind:N", scale=kind_colors, legend=None)))
    layers.append(base.mark_text(align="right", baseline="bottom", dy=-3, x="width", dx=-6,
                                 fontSize=11, color=INK).encode(y="price:Q", text="shown:N"))
    return alt.layer(*layers).properties(title=title, height=300)


def bullet(value: Any, limit: Any, *, label: str, value_text: str, limit_text: str,
           title: str = "") -> alt.Chart | None:
    """Usage bar against a hard limit (e.g. planned loss vs risk budget)."""
    used, cap = num(value), num(limit)
    if used is None or cap is None or cap <= 0:
        return None
    top = max(used, cap) * 1.08
    color = RED if used > cap else AMBER if used > cap * 0.8 else GREEN
    track = alt.Chart(_values([{"x": top}])).mark_bar(color=PANEL, stroke=RULE, height=26).encode(
        x=alt.X("x:Q", title=None, scale=alt.Scale(domain=[0, top])))
    bar = alt.Chart(_values([{"x": used, "t": value_text, "l": label}])).mark_bar(
        color=color, height=14).encode(
        x="x:Q", tooltip=[alt.Tooltip("l:N", title="Measure"), alt.Tooltip("t:N", title="Recorded")])
    rule = alt.Chart(_values([{"x": cap, "t": limit_text}])).mark_rule(
        color=INK, strokeWidth=2, strokeDash=[3, 2]).encode(
        x="x:Q", tooltip=[alt.Tooltip("t:N", title="Limit")])
    tag = alt.Chart(_values([{"x": cap, "t": f"LIMIT {limit_text}"}])).mark_text(
        dy=-18, color=INK, fontSize=10).encode(x="x:Q", text="t:N")
    return alt.layer(track, bar, rule, tag).properties(title=title, height=104)


def waterfall(steps: Sequence[Mapping[str, Any]], *, title: str = "", y_title: str = "") -> alt.Chart | None:
    """Rows: ``label``, ``amount`` (signed delta) and optional ``total`` flag."""
    rows, running = [], 0.0
    for step in steps:
        amount = num(step.get("amount"))
        if amount is None:
            return None
        if step.get("total"):
            start, end, running = 0.0, amount, amount
            kind = "total"
        else:
            start, end = running, running + amount
            running = end
            kind = "up" if amount >= 0 else "down"
        rows.append({"label": step["label"], "start": start, "end": end, "kind": kind,
                     "shown": step.get("shown", f"{amount:,.2f}")})
    if not rows:
        return None
    order = [row["label"] for row in rows]
    scale = alt.Scale(domain=["up", "down", "total"], range=[GREEN, RED, AMBER])
    base = alt.Chart(_values(rows)).encode(x=alt.X("label:N", sort=order, title=None,
                                                    axis=alt.Axis(labelAngle=0, labelLimit=120)))
    bars = base.mark_bar(size=34).encode(
        y=alt.Y("start:Q", title=y_title), y2="end:Q",
        color=alt.Color("kind:N", scale=scale, legend=None),
        tooltip=[alt.Tooltip("label:N"), alt.Tooltip("shown:N", title="Recorded")])
    text = base.mark_text(dy=-8, color=INK, fontSize=10).encode(
        y=alt.Y("end:Q"), text="shown:N")
    return alt.layer(bars, text).properties(title=title, height=280)


def funnel(stages: Sequence[Mapping[str, Any]], *, title: str = "") -> alt.Chart | None:
    rows = [{"stage": s["stage"], "count": int(s["count"]), "order": i}
            for i, s in enumerate(stages) if s.get("count") is not None]
    if not rows or max(row["count"] for row in rows) <= 0:
        return None
    order = [row["stage"] for row in rows]
    base = alt.Chart(_values(rows)).transform_calculate(
        lo="-datum.count/2", hi="datum.count/2").encode(
        y=alt.Y("stage:N", sort=order, title=None, axis=alt.Axis(labelLimit=200)))
    bars = base.mark_bar(height={"band": 0.7}).encode(
        x=alt.X("lo:Q", axis=None), x2="hi:Q",
        color=alt.Color("order:O", scale=alt.Scale(range=[AMBER, "#e0a030", CYAN, GREEN]), legend=None),
        tooltip=[alt.Tooltip("stage:N"), alt.Tooltip("count:Q", title="Companies")])
    text = base.mark_text(color=VOID, fontWeight="bold", fontSize=12).encode(
        x=alt.X("zero:Q", axis=None), text="count:Q").transform_calculate(zero="0")
    return alt.layer(bars, text).properties(title=title, height=_fit(len(rows), 40))


def timeline(events: Sequence[Mapping[str, Any]], *, lane: str = "lane", when: str = "when",
             label: str = "label", tone: str | None = None, title: str = "") -> alt.Chart | None:
    rows = [dict(row) for row in events if row.get(when)]
    if not rows:
        return None
    color = (alt.Color(f"{tone}:N", scale=alt.Scale(range=SERIES), legend=alt.Legend(title=None))
             if tone else alt.value(AMBER))
    base = alt.Chart(_values(rows)).encode(
        x=alt.X(f"{when}:T", title=None, axis=alt.Axis(format="%Y-%m-%d", labelAngle=0)),
        y=alt.Y(f"{lane}:N", title=None, axis=alt.Axis(labelLimit=180)),
        tooltip=[alt.Tooltip(f"{when}:T", format="%Y-%m-%d %H:%M"), alt.Tooltip(f"{lane}:N"),
                 alt.Tooltip(f"{label}:N")])
    lanes = len({row[lane] for row in rows})
    stems = base.mark_tick(thickness=2, size=18, opacity=0.55).encode(color=color)
    dots = base.mark_point(filled=True, size=70, shape="diamond").encode(color=color)
    return alt.layer(stems, dots).properties(title=title, height=_fit(lanes, 36, chrome=78))


def candles(bars: Sequence[Mapping[str, Any]], *, title: str = "", unit: str = "") -> alt.VConcatChart | None:
    rows = [
        {"d": str(b["date"]), "o": num(b["open"]), "h": num(b["high"]), "l": num(b["low"]),
         "c": num(b["close"]), "v": num(b.get("volume")),
         "dir": "up" if (num(b["close"]) or 0) >= (num(b["open"]) or 0) else "down"}
        for b in bars
    ]
    rows = [r for r in rows if None not in (r["o"], r["h"], r["l"], r["c"])]
    if not rows:
        return None
    scale = alt.Scale(domain=["up", "down"], range=[GREEN, RED])
    base = alt.Chart(_values(rows)).encode(
        x=alt.X("d:T", title=None, axis=alt.Axis(format="%Y-%m-%d", labelAngle=0)),
        color=alt.Color("dir:N", scale=scale, legend=None),
        tooltip=[alt.Tooltip("d:T", title="Date"), alt.Tooltip("o:Q", title="Open"),
                 alt.Tooltip("h:Q", title="High"), alt.Tooltip("l:Q", title="Low"),
                 alt.Tooltip("c:Q", title="Close"), alt.Tooltip("v:Q", title="Volume", format=",")])
    wick = base.mark_rule().encode(y=alt.Y("l:Q", title=f"Price {unit}".strip(),
                                           scale=alt.Scale(zero=False)), y2="h:Q")
    body = base.mark_bar(size=max(2, min(10, 600 // max(len(rows), 1)))).encode(y="o:Q", y2="c:Q")
    price = alt.layer(wick, body).properties(height=260, title=title)
    if all(r["v"] is None for r in rows):
        return alt.vconcat(price)
    volume = base.mark_bar(opacity=0.55).encode(
        y=alt.Y("v:Q", title="Volume", axis=alt.Axis(format="~s"))).properties(height=70)
    return alt.vconcat(price, volume, spacing=4)


def lines(rows: Sequence[Mapping[str, Any]], *, x: str, y: str, series: str | None = None,
          band: tuple[str, str] | None = None, title: str = "", y_title: str = "",
          x_type: str = "Q") -> alt.Chart | None:
    numeric = [y, *(band or ())] + ([x] if x_type == "Q" else [])
    data = [dict(row, **{f: num(row.get(f)) for f in numeric})
            for row in rows if num(row.get(y)) is not None]
    if not data:
        return None
    color = (alt.Color(f"{series}:N", scale=alt.Scale(range=SERIES), legend=alt.Legend(title=None))
             if series else alt.value(AMBER))
    base = alt.Chart(_values(data)).encode(x=alt.X(f"{x}:{x_type}", title=None))
    layers = []
    if band:
        layers.append(base.mark_area(opacity=0.18, color=CYAN).encode(
            y=alt.Y(f"{band[0]}:Q"), y2=f"{band[1]}:Q"))
    layers.append(base.mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=18)).encode(
        y=alt.Y(f"{y}:Q", title=y_title, scale=alt.Scale(zero=False)), color=color,
        tooltip=[alt.Tooltip(f"{x}:{x_type}"), alt.Tooltip(f"{y}:Q", format=",.4f")]
        + ([alt.Tooltip(f"{series}:N")] if series else [])))
    return alt.layer(*layers).properties(title=title)


def proximity(rows: Sequence[Mapping[str, Any]], *, title: str = "") -> alt.Chart | None:
    """Stop→target track per symbol with the latest recorded quote marked.

    Rows: ``symbol``, ``stop``, ``target``, ``quote`` (quote may be ``None``).
    Only rows with both stop and target are drawn; position is normalised 0→1.
    """
    data = []
    for row in rows:
        stop, target, quote = num(row.get("stop")), num(row.get("target")), num(row.get("quote"))
        if stop is None or target is None or stop == target:
            continue
        pos = None if quote is None else (quote - stop) / (target - stop)
        data.append({"symbol": row["symbol"], "pos": None if pos is None else max(-0.15, min(1.15, pos)),
                     "raw": "No recorded quote" if quote is None else str(row.get("quote_label") or row["quote"]),
                     "stop_t": f"{row['stop']}", "target_t": f"{row['target']}",
                     "state": "none" if pos is None else "breach" if pos <= 0 else "hit" if pos >= 1 else "inside"})
    if not data:
        return None
    base = alt.Chart(_values(data)).encode(y=alt.Y("symbol:N", title=None))
    track = base.mark_rule(color=RULE, strokeWidth=8, opacity=0.8).encode(
        x=alt.datum(0), x2=alt.datum(1))
    stop = base.mark_tick(color=RED, thickness=3, size=22).encode(x=alt.datum(0))
    target = base.mark_tick(color=GREEN, thickness=3, size=22).encode(x=alt.datum(1))
    dot = base.transform_filter("datum.pos != null").mark_point(
        filled=True, size=140, shape="triangle-down").encode(
        x=alt.X("pos:Q", scale=alt.Scale(domain=[-0.15, 1.15]),
                axis=alt.Axis(values=[0, 0.5, 1], labelExpr="datum.value == 0 ? 'STOP' : datum.value == 1 ? 'TARGET' : 'MID'"),
                title=None),
        color=alt.Color("state:N", scale=alt.Scale(domain=["inside", "breach", "hit", "none"],
                                                   range=[CYAN, RED, GREEN, MUTED]), legend=None),
        tooltip=[alt.Tooltip("symbol:N"), alt.Tooltip("raw:N", title="Latest recorded quote"),
                 alt.Tooltip("stop_t:N", title="Stop"), alt.Tooltip("target_t:N", title="Target")])
    return alt.layer(track, stop, target, dot).properties(title=title, height=_fit(len(data), 44))


def heat(rows: Sequence[Mapping[str, Any]], *, x: str, y: str, value: str, title: str = "",
         text: str | None = None) -> alt.Chart | None:
    data = [dict(row, _v=num(row[value])) for row in rows if num(row[value]) is not None]
    if not data:
        return None
    base = alt.Chart(_values(data)).encode(
        x=alt.X(f"{x}:N", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y(f"{y}:N", title=None))
    cells = base.mark_rect(stroke=VOID, strokeWidth=2).encode(
        color=alt.Color("_v:Q", scale=alt.Scale(range=[PANEL, "#6b4a10", AMBER]), legend=None),
        tooltip=[alt.Tooltip(f"{y}:N"), alt.Tooltip(f"{x}:N"),
                 alt.Tooltip(f"{text or value}:N", title="Recorded")])
    labels = base.mark_text(fontSize=10, color=INK).encode(text=f"{text or value}:N")
    return alt.layer(cells, labels).properties(title=title)


def execution_tape(quotes: Sequence[Mapping[str, Any]], fills: Sequence[Mapping[str, Any]] = (),
                   *, title: str = "", unit: str = "") -> alt.Chart | None:
    """Bid/ask spread band over event time with recorded fills marked.

    ``quotes`` rows: ``t`` (ISO time), ``bid``, ``ask``. ``fills`` rows: ``t``,
    ``price``, ``side``, ``shown``. Values are plotted as recorded; nothing derived.
    """
    q = [{"t": r["t"], "bid": num(r["bid"]), "ask": num(r["ask"])} for r in quotes]
    q = [r for r in q if r["bid"] is not None and r["ask"] is not None]
    if not q:
        return None
    x = alt.X("t:T", title=None, axis=alt.Axis(format="%H:%M:%S", labelAngle=0))
    base = alt.Chart(_values(q)).encode(x=x)
    band = base.mark_area(opacity=0.2, color=CYAN, interpolate="step-after").encode(
        y=alt.Y("bid:Q", title=f"Price {unit}".strip(), scale=alt.Scale(zero=False)), y2="ask:Q")
    bid = base.mark_line(color=GREEN, strokeWidth=1.5, interpolate="step-after").encode(
        y="bid:Q", tooltip=[alt.Tooltip("t:T", format="%H:%M:%S"), alt.Tooltip("bid:Q", title="Bid")])
    ask = base.mark_line(color=RED, strokeWidth=1.5, interpolate="step-after").encode(
        y="ask:Q", tooltip=[alt.Tooltip("t:T", format="%H:%M:%S"), alt.Tooltip("ask:Q", title="Ask")])
    layers = [band, bid, ask]
    f = [{"t": r["t"], "price": num(r["price"]), "side": str(r.get("side", "")),
          "shown": str(r.get("shown", r["price"]))} for r in fills]
    f = [r for r in f if r["price"] is not None]
    if f:
        layers.append(alt.Chart(_values(f)).mark_point(
            filled=True, size=160, shape="diamond", stroke=VOID, strokeWidth=1).encode(
            x=x, y="price:Q",
            color=alt.Color("side:N", scale=alt.Scale(domain=["buy", "sell"], range=[AMBER, VIOLET]),
                            legend=alt.Legend(title="Fill")),
            tooltip=[alt.Tooltip("side:N", title="Fill side"), alt.Tooltip("shown:N", title="Recorded"),
                     alt.Tooltip("t:T", format="%H:%M:%S")]))
    return alt.layer(*layers).properties(title=title, height=240)

def sparkline(rows: Sequence[Mapping[str, Any]], *, x: str = "t", y: str = "y",
              tone: str = "amber", height: int = 46) -> alt.Chart | None:
    """Axis-less trend of recorded points (needs at least two); last point marked."""
    data = [{"t": r[x], "y": num(r[y])} for r in rows if r.get(x) and num(r.get(y)) is not None]
    if len(data) < 2:
        return None
    color = TONES.get(tone, AMBER)
    base = alt.Chart(_values(data)).encode(
        x=alt.X("t:T", axis=None), y=alt.Y("y:Q", axis=None, scale=alt.Scale(zero=False)),
        tooltip=[alt.Tooltip("t:T", format="%Y-%m-%d %H:%M"), alt.Tooltip("y:Q", title="Recorded")])
    area = base.mark_area(color=color, opacity=0.12, interpolate="monotone")
    line = base.mark_line(color=color, strokeWidth=1.6, interpolate="monotone")
    last = alt.Chart(_values([data[-1]])).mark_point(filled=True, size=36, color=color).encode(
        x="t:T", y="y:Q")
    return alt.layer(area, line, last).properties(height=height)


def step_area(rows: Sequence[Mapping[str, Any]], *, when: str = "when", series: str = "series",
              title: str = "", y_title: str = "Records") -> alt.Chart | None:
    """Cumulative count of dated records per series as stepped lines (counting only)."""
    dated = sorted((str(r[when]), str(r[series])) for r in rows if r.get(when))
    if not dated:
        return None
    running: dict[str, int] = {}
    data = []
    for stamp, name in dated:
        running[name] = running.get(name, 0) + 1
        data.append({"t": stamp, "s": name, "n": running[name]})
    base = alt.Chart(_values(data)).encode(
        x=alt.X("t:T", title=None, axis=alt.Axis(format="%Y-%m-%d", labelAngle=0)),
        y=alt.Y("n:Q", title=y_title, axis=alt.Axis(tickMinStep=1, format="d")),
        color=alt.Color("s:N", scale=alt.Scale(range=SERIES), legend=alt.Legend(title=None)),
        tooltip=[alt.Tooltip("s:N", title="Series"), alt.Tooltip("n:Q", title="Cumulative"),
                 alt.Tooltip("t:T", format="%Y-%m-%d %H:%M")])
    line = base.mark_line(interpolate="step-after", strokeWidth=2)
    pts = base.mark_point(filled=True, size=28)
    return alt.layer(line, pts).properties(title=title, height=230)


def dots(rows: Sequence[Mapping[str, Any]], *, label: str = "label", value: str = "value",
         text: str | None = None, title: str = "", x_title: str = "",
         tone: str = "amber") -> alt.Chart | None:
    """Lollipop / dot plot: thin stem from zero to a recorded value, labelled."""
    data = [dict(row, _v=num(row[value])) for row in rows if num(row.get(value)) is not None]
    if not data:
        return None
    color = TONES.get(tone, AMBER)
    order = [row[label] for row in data]
    top = max(max(r["_v"] for r in data), 0.0)
    low = min(min(r["_v"] for r in data), 0.0)
    span = (top - low) or 1.0
    domain = [low - (0.3 * span if low < 0 else 0.0), top + (0.3 * span if top > 0 else 0.0)]
    base = alt.Chart(_values(data)).encode(
        y=alt.Y(f"{label}:N", sort=order, title=None, axis=alt.Axis(labelLimit=220)),
        tooltip=[alt.Tooltip(f"{label}:N", title="Item"),
                 alt.Tooltip(f"{text or value}:N", title="Recorded")])
    x = alt.X("_v:Q", title=x_title, scale=alt.Scale(domain=domain, nice=False))
    stem = base.mark_rule(color=RULE, strokeWidth=2).encode(x=alt.datum(0), x2="_v:Q")
    dot = base.mark_point(filled=True, size=120, color=color, shape="circle").encode(x=x)
    tag = base.mark_text(align="left", dx=9, color=INK, fontSize=11).encode(
        x="_v:Q", text=f"{text or value}:N")
    return alt.layer(stem, dot, tag).properties(title=title, height=_fit(len(data), 32))

def price_track(points: Sequence[Mapping[str, Any]], *, stop: Any = None, target: Any = None,
                unit: str = "", title: str = "", height: int = 170) -> alt.Chart | None:
    """Recorded price observations as a line with stop/target guide rules.

    ``points`` rows: ``t`` (ISO time), ``price``. Only recorded points are drawn and
    joined; nothing is interpolated beyond the straight segment between observations.
    """
    data = [{"t": p["t"], "price": num(p["price"])} for p in points
            if p.get("t") and num(p.get("price")) is not None]
    if not data:
        return None
    levels = [(lbl, num(v), c) for lbl, v, c in (("STOP", stop, RED), ("TARGET", target, GREEN))
              if num(v) is not None]
    values = [r["price"] for r in data] + [v for _, v, _ in levels]
    low, high = min(values), max(values)
    pad = (high - low) * 0.08 or abs(high) * 0.02 or 1.0
    y_scale = alt.Scale(domain=[low - pad, high + pad], nice=False, zero=False)
    base = alt.Chart(_values(data)).encode(
        x=alt.X("t:T", title=None, axis=alt.Axis(format="%d %b", labelAngle=0, tickCount=5)))
    area = base.mark_area(color=AMBER, opacity=0.1).encode(
        y=alt.Y("price:Q", scale=y_scale, title=unit or None), y2=alt.datum(low - pad))
    line = base.mark_line(color=AMBER, strokeWidth=2).encode(
        y=alt.Y("price:Q", scale=y_scale),
        tooltip=[alt.Tooltip("t:T", format="%Y-%m-%d %H:%M", title="Observed"),
                 alt.Tooltip("price:Q", title=f"Recorded {unit}".strip())])
    pts = base.mark_point(color=AMBER, filled=True, size=26).encode(y=alt.Y("price:Q", scale=y_scale))
    last = alt.Chart(_values([data[-1]])).mark_point(
        shape="triangle-left", size=120, filled=True, color=CYAN).encode(
        x="t:T", y=alt.Y("price:Q", scale=y_scale))
    layers = [area, line, pts, last]
    for lbl, value, color in levels:
        guide = alt.Chart(_values([{"y": value, "l": f"{lbl} {value:,.4g}"}]))
        layers.append(guide.mark_rule(color=color, strokeDash=[5, 3], strokeWidth=1.5).encode(
            y=alt.Y("y:Q", scale=y_scale), tooltip=[alt.Tooltip("l:N", title="Level")]))
        layers.append(guide.mark_text(align="left", x=4, dy=-6, color=color, fontSize=10).encode(
            y=alt.Y("y:Q", scale=y_scale), text="l:N"))
    return alt.layer(*layers).properties(title=title, height=height)