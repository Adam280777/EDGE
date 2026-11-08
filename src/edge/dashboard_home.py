"""Command center: read-only overview of recorded local state; never writes.

Every number shown is either a recorded value from local stores or a
deterministic snapshot from ``edge.portfolio`` using the same session-only
manual quotes/FX as the Portfolio valuation workspace. Nothing is fetched,
estimated or persisted here, and missing values stay ``Unavailable``.
"""

# ruff: noqa: BLE001 -- each pane degrades independently with a fixed message.
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import streamlit as st

from edge import ui_charts as charts
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    esc,
    fmt_money,
    fmt_time,
    html_block,
    kv_grid,
    status_board,
    status_light,
    ticker_tape,
    tiles,
    tone_for,
)

JUMPS = (
    ("Discovery", "DSC", "Screen candidates"),
    ("Research / decisions", "DEC", "Plan a trade"),
    ("Manual portfolio", "PRT", "Record a trade"),
    ("Monitoring", "MON", "Check rules"),
    ("Research journal", "JRN", "Write a note"),
    ("SEC filing research", "SEC", "Look up filings"),
)


def _go(target: str) -> None:
    st.session_state["edge_workspace"] = target


def _snapshot(root: Path):
    from edge.portfolio_store import PortfolioStore

    prefix = f"edge_portfolio_observations_{root.resolve()}"
    age = st.session_state.get("edge_portfolio_max_age", 86400)
    store = PortfolioStore(root)
    return store.snapshot(
        quotes=st.session_state.get(prefix, {}),
        fx_rates=st.session_state.get(f"{prefix}_fx", ()),
        as_of=datetime.now(UTC),
        max_age=timedelta(seconds=int(age)),
    )


def _load_portfolio(root: Path):
    try:
        return _snapshot(root)
    except Exception:
        return None


def _book_items(snap) -> list[Tile]:
    holdings = snap.holdings
    return [
        Tile("Equity · GBP", fmt_money(snap.equity_total_gbp, "GBP"),
             "Cash + valued holdings", "amber"),
        Tile("Holdings · GBP", fmt_money(snap.holdings_total_gbp, "GBP"),
             f"{len(holdings)} open position(s)", "ink"),
        Tile("Cash · GBP (native)", fmt_money(snap.cash.get("GBP"), "GBP")
             if "GBP" in snap.cash else "Not declared", "Declared ledger cash", "cyan"),
        Tile("Cash · USD (native)", fmt_money(snap.cash.get("USD"), "USD")
             if "USD" in snap.cash else "Not declared", "Not converted", "cyan"),
        Tile("Unrealised · GBP", fmt_money(snap.unrealized_total_gbp, "GBP", signed=True),
             "Needs fresh manual quotes", tone_for(snap.unrealized_total_gbp)),
        Tile("Realised · GBP", fmt_money(snap.realized_total_gbp, "GBP", signed=True),
             "FIFO, net of fees", tone_for(snap.realized_total_gbp)),
    ]


def _book(snap, ctx: PanelContext) -> None:
    if snap is None:
        st.error("Portfolio ledger unreadable. Nothing was reset; open Portfolio for details.")
        reasons = ["Ledger unreadable"]
    else:
        items = _book_items(snap)
        if ctx.view == "list":
            kv_grid([(t.label, f"{t.value} — {t.note}") for t in items],
                    columns=2 if ctx.w >= 8 else 1)
        else:
            tiles(items, columns=6 if ctx.w >= 10 else 3 if ctx.w >= 5 else 2)
        reasons = list(snap.readiness.reasons)
    if reasons:
        st.caption("Valuation incomplete — " + " ".join(reasons))


def _allocation(snap, ctx: PanelContext) -> None:
    if snap is None:
        return
    holdings = snap.holdings
    if not holdings:
        empty("No open holdings in the manual ledger.",
              hint="Portfolio › Append record declares cash and opening holdings.")
        return
    valued = all(h.market_value_gbp is not None for h in holdings)
    if ctx.view == "table":
        st.dataframe(
            [{"Holding": f"{h.instrument.market}:{h.instrument.symbol}",
              "Cost (native)": fmt_money(h.cost, h.instrument.currency),
              "Market value · GBP": fmt_money(h.market_value_gbp, "GBP"),
              "Unrealised · GBP": fmt_money(h.unrealized_gbp, "GBP", signed=True)}
             for h in holdings],
            hide_index=True, width="stretch",
        )
        if not valued:
            st.caption("GBP columns stay Unavailable until every holding has a fresh manual quote "
                       "(and USD→GBP FX for USD lines) in Portfolio › Valuation this session.")
        return
    if valued:
        both = ctx.view == "split"
        if ctx.view in ("split", "donut"):
            rows = [{"label": f"{h.instrument.market}:{h.instrument.symbol}",
                     "value": h.market_value_gbp, "shown": fmt_money(h.market_value_gbp, "GBP")}
                    for h in holdings]
            charts.show(charts.donut(rows, detail="shown", title="Allocation by market value (GBP)",
                                     center=fmt_money(snap.holdings_total_gbp, "GBP", places=0)),
                        alt_text="Donut chart of holdings by GBP market value",
                        height=min(250, ctx.chart_height()) if both else ctx.chart_height())
        if ctx.view in ("split", "pnl"):
            pnl = [{"label": f"{h.instrument.market}:{h.instrument.symbol}", "value": h.unrealized_gbp,
                    "shown": fmt_money(h.unrealized_gbp, "GBP", signed=True)}
                   for h in holdings if h.unrealized_gbp is not None]
            charts.show(charts.dots(pnl, text="shown", x_title="GBP",
                                    title="Unrealised P&L per holding (GBP, deterministic snapshot)"),
                        alt_text="Dot plot of unrealised GBP profit or loss per holding")
    else:
        currencies = sorted({h.instrument.currency for h in holdings})
        for col, currency in zip(st.columns(len(currencies), gap="small"), currencies, strict=True):
            rows = [{"label": f"{h.instrument.market}:{h.instrument.symbol}", "value": h.cost,
                     "shown": fmt_money(h.cost, currency)}
                    for h in holdings if h.instrument.currency == currency]
            with col:
                charts.show(charts.donut(rows, detail="shown", title=f"{currency} cost basis split",
                                         center=currency),
                            alt_text=f"Donut of {currency} cost basis per holding",
                            height=min(230, ctx.chart_height()))
        st.caption("Market-value allocation needs a fresh manual quote for every holding "
                   "(and USD→GBP FX for USD lines) in Portfolio › Valuation this session.")


def _exposure(snap, ctx: PanelContext) -> None:
    if snap is None:
        return
    by_ccy = Counter(h.instrument.currency for h in snap.holdings)
    present = [c for c in ("GBP", "USD") if c in snap.cash or by_ccy.get(c)]
    if not present:
        empty("No cash or holdings declared.")
        return
    books = {}
    for currency in present:
        rows = []
        if currency in snap.cash:
            rows.append({"label": "Cash", "value": snap.cash[currency],
                         "shown": fmt_money(snap.cash[currency], currency)})
        if by_ccy.get(currency):
            cost = sum(h.cost for h in snap.holdings if h.instrument.currency == currency)
            rows.append({"label": "Holdings at cost", "value": cost,
                         "shown": f"{fmt_money(cost, currency)} · {by_ccy[currency]} line(s)"})
        books[currency] = rows
    if ctx.view == "table":
        # One row per native currency line; nothing is converted or summed across currencies.
        st.dataframe([{"Currency": c, "Line": r["label"], "Amount (native)": r["shown"]}
                      for c, rows in books.items() for r in rows],
                     hide_index=True, width="stretch")
        return
    stacked = ctx.w < 5
    columns = [st.container() for _ in present] if stacked else st.columns(len(present), gap="small")
    for col, currency in zip(columns, present, strict=True):
        with col:
            if not charts.show(
                charts.donut(books[currency], detail="shown", center=currency,
                             title=f"{currency} book · native"),
                alt_text=f"Donut of {currency} cash versus holding cost",
                height=min(230, ctx.chart_height()),
            ):
                st.caption(f"{currency}: no positive balances to draw.")


def _load_monitoring(root: Path) -> dict | None:
    from edge.monitoring_store import MonitoringStore

    try:
        store = MonitoringStore(root)
        rules, quotes, alerts = store.rules(), store.quotes(), store.alerts()
        history = store.quote_history()
    except Exception:
        return None
    tracked = [(r, history.get((r.symbol, r.market), ())) for r in rules]
    tracked = [(r, [q for q in items if q.unit == r.unit]) for r, items in tracked]
    return {
        "rules": rules, "quotes": quotes,
        "open": [a for a in alerts if a.acknowledged_at is None],
        "tracked": [(r, items) for r, items in tracked if items],
    }


def _monitoring(mon: dict | None, ctx: PanelContext) -> None:
    if mon is None:
        st.error("Monitoring store unreadable. Nothing was reset; open Monitoring for details.")
        return
    rules, quotes, open_alerts = mon["rules"], mon["quotes"], mon["open"]
    if ctx.view in ("both", "map"):
        rows = []
        for r in rules:
            q = quotes.get((r.symbol, r.market))
            usable = q is not None and q.unit == r.unit
            rows.append({
                "symbol": f"{r.market}:{r.symbol}", "stop": r.stop, "target": r.target,
                "quote": q.amount if usable else None,
                "quote_label": f"{q.amount} {q.unit} at {q.as_of:%Y-%m-%d %H:%M} UTC" if usable else None,
            })
        if not rules:
            empty("No monitoring rules saved.", hint="Monitoring › add stop / target / thesis rules.")
        elif not charts.show(charts.proximity(rows, title="Latest recorded quote between stop and target"),
                             alt_text="Track chart placing each latest recorded quote between stop and target"):
            st.caption("Rules exist but none define both a stop and a target to draw.")
        else:
            st.caption("Quotes in a different unit from the rule are not plotted (same policy as "
                       "rule evaluation). Staleness is judged only by Monitoring › Check rules.")
    if ctx.view in ("both", "alerts"):
        if open_alerts:
            symbols = {r.rule_id: f"{r.market}:{r.symbol}" for r in rules}
            st.dataframe(
                [{"Symbol": symbols.get(a.rule_id, "Unknown rule"),
                  "Reason": a.reason.replace("_", " "),
                  "Observed": fmt_time(a.observation_at) if a.observation_at else UNAVAILABLE,
                  "Raised": fmt_time(a.created_at)} for a in open_alerts[:8]],
                hide_index=True, width="stretch",
            )
        elif ctx.view == "alerts":
            empty("No open alerts.", hint="Alerts appear after Monitoring › Check rules.")


def _tracks(mon: dict | None, ctx: PanelContext) -> None:
    tracked = mon["tracked"] if mon else []
    if not tracked:
        empty("No recorded quote history for monitored instruments.",
              hint="Monitoring › record manual quotes; each observation becomes a point here.")
        return
    per_row = ctx.per_row(min_cols=4, most=4)
    rows = -(-len(tracked) // per_row)
    height = max(130, (ctx.height_px - 110) // rows - 40) if not ctx.fit else None
    for start in range(0, len(tracked), per_row):
        cols = st.columns(per_row, gap="small")
        for col, (r, items) in zip(cols, tracked[start:start + per_row], strict=False):
            with col:
                charts.show(
                    charts.price_track([{"t": q.as_of.isoformat(), "price": q.amount} for q in items],
                                       stop=r.stop, target=r.target, unit=r.unit,
                                       title=f"{r.market}:{r.symbol} · {len(items)} recorded quote(s)"),
                    alt_text=f"Line of recorded {r.unit} quotes for {r.symbol} with stop and target levels",
                    height=height)
    st.caption("Lines join recorded manual observations only (same unit as the rule); "
               "no prices are interpolated or fetched. Dashed lines are the saved stop and target.")


def _load_pipeline(root: Path, journal_root: Path) -> dict:
    from edge.decision_store import DecisionStore
    from edge.journal_store import JournalStore
    from edge.watchlist_store import WatchlistStore

    health: dict[str, str] = {}
    counts: list[dict] = []
    dated: list[dict] = []

    def count(label: str, key: str, loader) -> list:
        try:
            items = list(loader())
            health[key] = "ok" if items else "off"
        except Exception:
            items, health[key] = [], "bad"
        shown = f"{len(items)}{'+' if len(items) >= 100 else ''}"
        counts.append({"stage": label, "count": len(items), "shown": shown})
        dated.extend({"when": getattr(i, "created_at", None), "series": label} for i in items)
        return items

    count("Watchlist", "watchlist", lambda: WatchlistStore(root).list_entries(limit=100))
    notes = count("Journal notes", "journal", lambda: JournalStore(journal_root).list_entries(limit=100))
    decisions = DecisionStore(root)
    count("Research revisions", "research", lambda: decisions.list_records("research", limit=100))
    count("Trade plans", "plans", lambda: decisions.list_records("plan", limit=100))
    count("Risk policy revisions", "risk", lambda: decisions.list_records("risk", limit=100))
    return {"health": health, "counts": counts, "dated": dated, "notes": notes}


def _pipeline(pipe: dict, ctx: PanelContext) -> None:
    counts = pipe["counts"]
    if all(row["count"] == 0 for row in counts):
        empty("No research records yet.",
              hint="Start in Watchlist or Discovery, then record research and plans.")
    else:
        if ctx.view in ("both", "counts"):
            html_block('<div class="edge-pipe-counts">' + "".join(
                f'<span><b>{esc(row["shown"])}</b>{esc(row["stage"])}</span>' for row in counts) + "</div>")
        if ctx.view in ("both", "timeline"):
            charts.show(charts.step_area(pipe["dated"],
                                         title="Records created over time (cumulative, latest 100 each)"),
                        alt_text="Stepped cumulative line of saved research records per stage over time",
                        height=ctx.chart_height(reserve=150 if ctx.view == "both" else 100))
    if pipe["notes"]:
        horizons = Counter(n.horizon for n in pipe["notes"])
        st.caption(" · ".join(f"{k}: {v} note(s)" for k, v in sorted(horizons.items())))


def _tape(snap, root: Path) -> None:
    items = []
    if snap is not None:
        for h in snap.holdings:
            value = (f"{h.quantity} sh · {h.quote.amount} {h.quote.unit}" if h.quote
                     else f"{h.quantity} sh · no quote")
            pnl = h.unrealized_gbp
            items.append((f"{h.instrument.market}:{h.instrument.symbol}",
                          value + ("" if pnl is None else f" · {fmt_money(pnl, 'GBP', signed=True)}"),
                          tone_for(pnl) if pnl is not None else "muted"))
    try:
        from edge.watchlist_store import WatchlistStore

        for entry in WatchlistStore(root).list_entries(limit=20):
            items.append((entry.symbol, "WATCH", "cyan"))
    except Exception:
        # The pipeline pane already flags an unreadable watchlist; say so here too.
        items.append(("WATCHLIST", "unreadable", "red"))
    ticker_tape(items)


def _status(snap, mon: dict | None, pipe: dict, ctx: PanelContext) -> None:
    reasons = ["Ledger unreadable"] if snap is None else list(snap.readiness.reasons)
    rule_count = len(mon["rules"]) if mon else 0
    alert_count = len(mon["open"]) if mon else 0
    health = pipe["health"]
    ledger_state = "bad" if snap is None else "ok" if snap.holdings or snap.cash else "off"
    status_board([
        status_light("Manual ledger", ledger_state,
                     "No records yet" if ledger_state == "off" else "FIFO replay verified on read"),
        status_light("Valuation", "ok" if snap is not None and not reasons else "warn",
                     "Complete" if snap is not None and not reasons else "Missing or stale manual inputs"),
        status_light("Monitoring", "bad" if mon is None else "warn" if alert_count else
                     "ok" if rule_count else "off",
                     f"{rule_count} rule(s) · {alert_count} open alert(s)"),
        status_light("Research journal", health.get("journal", "off"), "Append-only notes"),
        status_light("Decisions store", health.get("plans", "off"), "Plans, research, risk revisions"),
        status_light("Live market feed", "off", "None — all prices are user-declared"),
        status_light("Broker connection", "off", "Not implemented by design"),
    ])


def _jumps(ctx: PanelContext) -> None:
    st.caption("Quick jump")
    per_row = len(JUMPS) if ctx.w >= 9 else 3 if ctx.w >= 5 else 2
    for start in range(0, len(JUMPS), per_row):
        cols = st.columns(per_row, gap="small")
        for col, (target, code, label) in zip(cols, JUMPS[start:start + per_row], strict=False):
            col.button(f"{code} · {label}", key=f"edge_home_jump_{code}", on_click=_go,
                       args=(target,), width="stretch")


def panels(data_root: Path, journal_root: Path) -> list[Panel]:
    """Command Center panels. All stores are read once, before layout, so any
    arrangement (or hidden panel) shows the same recorded values."""
    snap = _load_portfolio(data_root)
    mon = _load_monitoring(data_root)
    pipe = _load_pipeline(data_root, journal_root)
    return [
        Panel("book", "PRT", "Book overview", lambda c: _book(snap, c), w=12, h=5, min_w=4,
              min_h=3, fit=True, meta="GBP reporting · session manual quotes", badges=("manual",),
              views=(("tiles", "Tiles"), ("list", "List"))),
        Panel("tape", "TAPE", "Ticker tape", lambda c: _tape(snap, data_root), w=12, h=2,
              min_w=4, min_h=1, fit=True, framed=False),
        Panel("alloc", "ALC", "Allocation", lambda c: _allocation(snap, c), w=7, h=12, min_w=3,
              badges=("manual",),
              views=(("split", "Value + P&L"), ("donut", "Allocation donut"),
                     ("pnl", "P&L dot plot"), ("table", "Table"))),
        Panel("fx", "FX", "Currency exposure", lambda c: _exposure(snap, c), w=5, h=12, min_w=3,
              meta="GBP / USD kept separate", views=(("donut", "Donuts"), ("table", "Table"))),
        Panel("monitor", "MON", "Risk monitor", lambda c: _monitoring(mon, c), w=7, h=11, min_w=3,
              meta="Stored manual quotes only", badges=("manual",),
              views=(("both", "Map + alerts"), ("map", "Stop / target map"), ("alerts", "Open alerts"))),
        Panel("pipeline", "PIPE", "Research pipeline", lambda c: _pipeline(pipe, c), w=5, h=11,
              min_w=3, badges=("local",),
              views=(("both", "Counts + timeline"), ("timeline", "Timeline"), ("counts", "Counts"))),
        Panel("tracks", "TRK", "Price tracks", lambda c: _tracks(mon, c), w=12, h=9, min_w=3,
              meta="Recorded manual quotes vs saved stop / target", badges=("manual",)),
        Panel("status", "SYS", "System status", lambda c: _status(snap, mon, pipe, c), w=12, h=5,
              min_w=3, fit=True, meta="States describe local records, not market freshness"),
        Panel("jump", "JMP", "Quick jump", _jumps, w=12, h=2, min_w=3, min_h=2, fit=True,
              framed=False),
    ]


def render(data_root: Path, journal_root: Path | None = None) -> None:
    journal_root = data_root if journal_root is None else journal_root
    grid("home", panels(data_root, journal_root))
