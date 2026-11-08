"""Terminal design system: chart builders validate, refuse empty data, and display helpers escape."""

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from edge import ui_charts as c
from edge import ui_kit as k

T0 = "2026-01-02T10:00:00+00:00"
T1 = "2026-01-03T10:00:00+00:00"

BUILDERS = {
    "donut": lambda: c.donut([{"label": "A", "value": D(10)}, {"label": "B", "value": D(5)}],
                             center="GBP"),
    "hbars": lambda: c.hbars([{"label": "A", "value": D(3)}], integer=True),
    "grouped": lambda: c.grouped_bars([{"n": "A", "s": "Cost", "value": 1}], category="n", series="s"),
    "ladder": lambda: c.price_ladder(
        [{"label": "STOP", "price": D(90), "kind": "stop"},
         {"label": "T1", "price": D(120), "kind": "target"}],
        zone=(D(99), D(101)), risk=(D(90), D(99)), unit="GBP"),
    "bullet": lambda: c.bullet(D(180), D(200), label="Loss", value_text="x", limit_text="y"),
    "waterfall": lambda: c.waterfall([{"label": "PV", "amount": 100},
                                      {"label": "Eq", "amount": 100, "total": True}]),
    "funnel": lambda: c.funnel([{"stage": "All", "count": 10}, {"stage": "Pass", "count": 4}]),
    "timeline": lambda: c.timeline([{"lane": "10-K", "when": "2024-01-01", "label": "x"}]),
    "candles": lambda: c.candles([{"date": "2024-01-01", "open": 1, "high": 2, "low": 0.5,
                                   "close": 1.5, "volume": 100}]),
    "lines": lambda: c.lines([{"x": 1, "y": D(2), "lo": 1, "hi": 3}], x="x", y="y", band=("lo", "hi")),
    "proximity": lambda: c.proximity([{"symbol": "A", "stop": 90, "target": 120, "quote": 100}]),
    "heat": lambda: c.heat([{"x": "a", "y": "b", "value": 1}], x="x", y="y", value="value"),
    "execution_tape": lambda: c.execution_tape(
        [{"t": T0, "bid": D(99), "ask": D(101)}, {"t": T1, "bid": D(100), "ask": D(102)}],
        [{"t": T1, "price": D(101), "side": "buy"}], unit="USD"),
    "sparkline": lambda: c.sparkline([{"t": T0, "y": D(1)}, {"t": T1, "y": D(2)}]),
    "step_area": lambda: c.step_area([{"when": T0, "series": "Notes"}, {"when": T1, "series": "Notes"}]),
    "dots": lambda: c.dots([{"label": "VOD", "value": D("-12.5")}, {"label": "BP", "value": D(40)}]),
    "price_track": lambda: c.price_track([{"t": T0, "price": D(70)}, {"t": T1, "price": D(72)}],
                                         stop=D(60), target=D(95), unit="GBp"),
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_every_builder_emits_a_valid_vega_lite_spec(name):
    chart = BUILDERS[name]()
    assert chart is not None
    spec = c._style(chart, 220).to_dict(validate=True)
    assert spec["config"]["background"]


def test_builders_render_nothing_rather_than_invent_data():
    assert c.donut([]) is None
    assert c.donut([{"label": "A", "value": None}]) is None
    assert c.bullet(None, 1, label="", value_text="", limit_text="") is None
    assert c.execution_tape([{"t": T0, "bid": None, "ask": D(1)}]) is None
    assert c.sparkline([{"t": T0, "y": D(1)}]) is None  # one point is not a trend
    assert c.step_area([{"when": None, "series": "x"}]) is None
    assert c.dots([{"label": "A", "value": None}]) is None
    assert c.price_track([], stop=D(1), target=D(2)) is None


def test_step_area_counts_records_cumulatively_per_series():
    chart = c.step_area([{"when": T1, "series": "A"}, {"when": T0, "series": "A"},
                         {"when": T0, "series": "B"}])
    rows = chart.to_dict()["data"]["values"]
    assert [(r["s"], r["n"]) for r in rows] == [("A", 1), ("B", 1), ("A", 2)]


def test_decimal_chart_data_is_serialised_exactly():
    assert c._plain(D("0.1")) == "0.1"
    assert c._plain(datetime(2026, 1, 1, tzinfo=UTC)) == "2026-01-01T00:00:00+00:00"


def test_formatting_never_fills_missing_values():
    assert k.fmt_money(None, "GBP") == k.UNAVAILABLE
    assert k.fmt_money("nan", "GBP") == k.UNAVAILABLE
    assert k.fmt_money(D("-1234.5"), "GBP") == "−£1,234.50"
    assert k.fmt_money(D(12), "USD", signed=True) == "+$12.00"
    assert k.fmt_pct(D("0.125")) == "12.5%"
    assert k.fmt_number(None) == k.UNAVAILABLE
    assert k.tone_for(None) == "muted" and k.tone_for(D(-1)) == "red"


def test_fmt_time_converts_to_utc_before_labelling():
    london_summer = datetime(2026, 7, 1, 12, 0, tzinfo=timezone(timedelta(hours=1)))
    assert k.fmt_time(london_summer) == "2026-07-01 11:00 UTC"
    assert k.fmt_time("2026-07-01T12:00:00+01:00") == "2026-07-01 11:00 UTC"
    assert k.fmt_time(None) == k.UNAVAILABLE
    assert k.fmt_time("not a time") == "not a time"


def test_chips_and_provenance_escape_dynamic_text():
    assert "<script>" not in k.chip("<script>x</script>")
    assert "SYNTHETIC" in k.provenance("synthetic")
    assert k.provenance("unknown-kind") == ""
    assert "&lt;b&gt;" in k.status_light("<b>", "ok", "<i>")


def test_escaped_text_cannot_end_the_html_block():
    hostile = "ok\r\n\r\n[click](https://example.invalid) **bold**"
    escaped = k.esc(hostile)
    assert "\n" not in escaped and "\r" not in escaped
    assert escaped.startswith("ok&#10;&#10;[click]")


def test_pence_unit_survives_uppercase_terminal_labels():
    # CSS uppercases tile/kv/light labels; GBp must stay pence, never read as GBP.
    assert k.esc("UK:VOD · GBp") == 'UK:VOD · <span class="edge-keepcase">GBp</span>'
    assert k.esc("GBP") == "GBP"
    assert "edge-keepcase" not in k.esc("GBpx")
    from edge.ui_theme import TERMINAL_CSS

    assert ".edge-keepcase { text-transform: none !important; }" in TERMINAL_CSS
