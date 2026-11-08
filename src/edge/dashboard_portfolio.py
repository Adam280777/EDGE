"""Manual ledger and explicitly dated valuation; never simulated positions.

Presentation only: every figure comes from ``edge.portfolio`` replay/snapshot
results. Missing valuation inputs stay ``Unavailable``; GBP and USD are never
summed or plotted on one axis except where the snapshot itself converted them
with an explicit, fresh USD→GBP rate.
"""

# ruff: noqa: BLE001 -- fixed UI errors must not leak local records or SQL.
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import streamlit as st

from edge import portfolio as p
from edge import ui_charts as charts
from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    readout,
    timestamp_input,
)
from edge.decision_contracts import FxRate, ManualProvenance, PriceQuote
from edge.portfolio_store import PortfolioStore
from edge.ui_grid import Panel, PanelContext, grid
from edge.ui_kit import (
    UNAVAILABLE,
    Tile,
    empty,
    fmt_money,
    fmt_number,
    fmt_time,
    kv_grid,
    panel,
    tiles,
    tone_for,
)

EVENT_NAMES = {
    "CashOpening": "Cash opening", "OpeningHolding": "Opening holding", "Buy": "Executed buy",
    "Sell": "Executed sell", "CashDeposit": "Cash deposit", "CashWithdrawal": "Cash withdrawal",
    "Fee": "Fee", "CashExchange": "Cash exchange", "Correction": "Correction",
}
TASKS = ("Ledger", "Append record", "Valuation", "Import CSV")
# The task panel's pane header follows the chosen task: (code, title, meta).
TASK_HEADS = {
    "Ledger": ("LGR", "Ledger review", "Read-only ? recorded events"),
    "Append record": ("APP", "Append manual ledger record", "Immutable ? corrections add void records"),
    "Valuation": ("OBS", "Session valuation inputs", "Session-only ? never persisted"),
    "Import CSV": ("CSV", "Import user-authored CSV", "Session preview ? explicit apply"),
}


def _name(instrument: p.Instrument) -> str:
    return f"{instrument.market}:{instrument.symbol}"


def _cash(mapping, currency: str) -> str:
    return fmt_money(mapping[currency], currency) if currency in mapping else "Not declared"


def _session(root: Path):
    prefix = f"edge_portfolio_observations_{root.resolve()}"
    return prefix, st.session_state.get(prefix, {}), st.session_state.get(f"{prefix}_fx", ())


@dataclass(frozen=True)
class _Book:
    """Everything the panels display, read once before layout so any arrangement
    (or hidden panel) shows the same recorded values. ``None`` means unreadable."""

    entries: tuple | None
    state: object | None
    snap: object | None
    age: int


def _load(store: PortfolioStore, root: Path) -> _Book:
    _prefix, quotes, fx_rates = _session(root)
    age = int(st.session_state.get("edge_portfolio_max_age", 86400))
    try:
        snap = store.snapshot(quotes=quotes, fx_rates=fx_rates, as_of=datetime.now(UTC),
                              max_age=timedelta(seconds=age))
    except Exception:
        snap = None
    try:
        entries, state = tuple(store.list_entries()), store.state()
    except Exception:
        entries = state = None
    return _Book(entries, state, snap, age)


def _unreadable() -> None:
    empty(f"{UNAVAILABLE} — portfolio records could not be read.",
          hint="See the error above. Nothing was reset or recalculated.")


def _summary(snap) -> None:
    """KPI strip from the same deterministic snapshot the Valuation task shows."""
    if snap is None:
        failure("Portfolio summary")
        return
    tiles([
        Tile("Equity · GBP", fmt_money(snap.equity_total_gbp, "GBP"), "Cash + valued holdings", "amber"),
        Tile("Holdings · GBP", fmt_money(snap.holdings_total_gbp, "GBP"),
             f"{len(snap.holdings)} open position(s)", "ink"),
        Tile("Cash · GBP (native)", _cash(snap.cash, "GBP"), "Declared ledger cash", "cyan"),
        Tile("Cash · USD (native)", _cash(snap.cash, "USD"), "Not converted", "cyan"),
        Tile("Unrealised · GBP", fmt_money(snap.unrealized_total_gbp, "GBP", signed=True),
             "Needs fresh manual quotes", tone_for(snap.unrealized_total_gbp)),
        Tile("Realised · GBP", fmt_money(snap.realized_total_gbp, "GBP", signed=True),
             "FIFO sale P&L", tone_for(snap.realized_total_gbp)),
    ], columns=6)
    if snap.readiness.reasons:
        st.caption("GBP totals unavailable until valuation inputs are complete — see Valuation.")


def _detail(event) -> tuple[str, str, str, str]:
    """(instrument, quantity, amount, currency) display strings for a ledger event."""
    instrument = getattr(event, "instrument", None)
    quantity = getattr(event, "quantity", None)
    name = _name(instrument) if instrument else "—"
    qty = fmt_number(quantity, places=0) if quantity is not None else "—"
    if isinstance(event, p.OpeningHolding):
        return name, qty, fmt_money(event.total_cost, instrument.currency) + " cost", instrument.currency
    if isinstance(event, (p.Buy, p.Sell)):
        text = (f"{fmt_money(event.price, event.price_unit, places=4)}/sh · fee "
                f"{fmt_money(event.fee, event.fee_currency)}")
        return name, qty, text, event.fee_currency
    if isinstance(event, p.CashExchange):
        fx = event.fx_rate
        text = f"{fmt_money(event.amount, fx.from_currency)} @ {fx.rate} → {fx.to_currency}"
        return name, qty, text, fx.from_currency
    if isinstance(event, p.Correction):
        return name, qty, f"Voids {event.target_event_id[:8]}…", "—"
    amount = getattr(event, "amount", None)
    currency = getattr(event, "currency", "—")
    return name, qty, fmt_money(amount, currency), currency


def _native(book: _Book) -> None:
    state, entries = book.state, book.entries
    if state is None:
        _unreadable()
        return
    tiles([
        Tile("Ledger records", str(len(entries)), "Immutable · append-only", "amber"),
        Tile("Open lots", str(len(state.lots)), "FIFO remaining", "ink"),
        Tile("Cash · GBP", _cash(state.cash, "GBP"), "Native", "cyan"),
        Tile("Cash · USD", _cash(state.cash, "USD"), "Native · not converted", "cyan"),
        Tile("Realised · GBP lines", fmt_money(state.realized.get("GBP"), "GBP", signed=True),
             "FIFO sale P&L", tone_for(state.realized.get("GBP"))),
        Tile("Realised · USD lines", fmt_money(state.realized.get("USD"), "USD", signed=True),
             "FIFO sale P&L", tone_for(state.realized.get("USD"))),
    ], columns=6)
    kv_grid([
        ("Standalone fees · GBP", fmt_money(state.standalone_fees.get("GBP"), "GBP")),
        ("Standalone fees · USD", fmt_money(state.standalone_fees.get("USD"), "USD")),
        ("Completed sales", str(len(state.sales))),
        ("Last effective at", fmt_time(state.last_effective_at)),
    ], columns=2)
    if state.lots:
        st.dataframe([{
            "Instrument": _name(lot.instrument), "Currency": lot.instrument.currency,
            "Acquired": lot.acquired_on.isoformat(), "Quantity": lot.quantity,
            "Remaining cost": fmt_money(lot.remaining_cost, lot.instrument.currency),
            "Lot event": lot.event_id[:12],
        } for lot in state.lots], hide_index=True, width="stretch")
    else:
        empty("No open lots.", hint="Append an Opening holding or Executed buy record.")


def _no_records() -> None:
    empty("No manual ledger records yet.",
          hint="Choose Append record to declare cash and opening holdings.")


def _timeline(book: _Book, ctx: PanelContext) -> None:
    if book.entries is None:
        _unreadable()
        return
    if not book.entries:
        _no_records()
        return
    events = [{
        "lane": EVENT_NAMES.get(type(entry.event).__name__, type(entry.event).__name__),
        "when": p.utc_timestamp(entry.event.effective_at).isoformat(),
        "label": " · ".join(part for part in _detail(entry.event)[:3] if part != "—"),
    } for entry in book.entries]
    height = None if ctx.fit else ctx.chart_height(reserve=110)

    def timeline() -> None:
        charts.show(charts.timeline(events, title="Records by type · effective time"),
                    alt_text="Timeline of manual ledger records by event type", height=height,
                    key="edge_portfolio_timeline_chart")

    def steps() -> None:
        charts.show(charts.step_area(events, when="when", series="lane",
                                     title="Cumulative records · by type"),
                    alt_text="Cumulative count of ledger records over effective time per type",
                    height=height, key="edge_portfolio_steps_chart")

    if ctx.view == "timeline":
        timeline()
    elif ctx.view == "steps":
        steps()
    else:
        left, right = st.columns(2)
        with left:
            timeline()
        with right:
            steps()


def _realised(book: _Book, ctx: PanelContext) -> None:
    state, entries = book.state, book.entries
    if state is None:
        _unreadable()
        return
    if not state.sales:
        empty("No completed sales.",
              hint="Realised P&L appears per FIFO sale once an Executed sell is recorded.")
        return
    when = {entry.event_id: entry.event.effective_at for entry in entries}
    if ctx.view == "table":
        # Each row keeps its own currency; GBP and USD lines are never summed here.
        st.dataframe([{
            "Currency": sale.instrument.currency, "Instrument": _name(sale.instrument),
            "Effective": fmt_time(when.get(sale.event_id)), "Sale event": sale.event_id[:12],
            "Realised": fmt_money(sale.realized, sale.instrument.currency, signed=True),
        } for sale in state.sales], hide_index=True, width="stretch")
        return
    for currency in ("GBP", "USD"):
        rows = [{
            "label": f"{_name(sale.instrument)} · {fmt_time(when.get(sale.event_id))} · "
                     f"{sale.event_id[:6]}",
            "value": sale.realized, "shown": fmt_money(sale.realized, currency, signed=True),
        } for sale in state.sales if sale.instrument.currency == currency]
        charts.show(charts.dots(rows, text="shown", x_title=currency,
                                title=f"Realised P&L per FIFO sale · {currency} lines"),
                    alt_text=f"Dot plot of realised profit or loss per {currency} sale",
                    key=f"edge_portfolio_realised_{currency}")


def _records(book: _Book) -> None:
    entries = book.entries
    if entries is None:
        _unreadable()
        return
    if not entries:
        _no_records()
        return
    st.dataframe([{
        "Effective": fmt_time(entry.event.effective_at),
        "Type": EVENT_NAMES.get(type(entry.event).__name__, type(entry.event).__name__),
        "Instrument": detail[0], "Qty": detail[1], "Amount / price": detail[2],
        "Currency": detail[3], "Source": entry.event.source,
        "Recorded": fmt_time(entry.recorded_at), "Event ID": entry.event_id,
    } for entry in entries for detail in (_detail(entry.event),)],
        hide_index=True, width="stretch")
    with st.expander("Immutable ledger records / newest last"):
        for entry in entries:
            readout(entry)


def _event(store: PortfolioStore, root: Path, flash) -> None:
    pending_key = f"edge_portfolio_pending_{root.resolve()}"
    action = st.selectbox(
        "Manual ledger action",
        ["Cash opening", "Opening holding", "Executed buy", "Executed sell",
         "Cash deposit", "Cash withdrawal", "Fee", "Cash exchange", "Correction"],
        key="edge_portfolio_action",
    )
    st.caption(
        "Opening holdings declare existing acquisition cost, not a synthetic purchase. "
        "Subsequent sales use FIFO. Declare GBP and USD cash separately, including zero. "
        "No tax report, automatic FX conversion or broker execution."
    )
    with st.form("edge_portfolio_event", enter_to_submit=False):
        left, middle, right = st.columns([2, 2, 1])
        with left:
            effective = st.text_input("Effective at / ISO timestamp with UTC offset")
        with middle:
            source = st.text_input("Record source / user declaration", max_chars=200)
        with right:
            currency = st.selectbox("Cash / settlement currency", ["GBP", "USD"])
        instrument_action = action in ("Opening holding", "Executed buy", "Executed sell")
        if instrument_action:
            left, middle, right = st.columns(3)
            with left:
                symbol = st.text_input("Ticker / identity unverified", max_chars=32)
            with middle:
                market = st.selectbox("Declared market", ["UK", "US"])
            with right:
                quantity = st.number_input("Quantity / whole shares", min_value=1, value=1, step=1)
            if action == "Opening holding":
                left, right = st.columns(2)
                with left:
                    acquired = st.text_input("Acquired on / YYYY-MM-DD")
                with right:
                    cost = st.text_input("Total opening cost / major settlement currency")
            else:
                left, middle, right = st.columns(3)
                with left:
                    price = st.text_input("Execution price / exact decimal")
                with middle:
                    unit = st.selectbox("Execution price unit / per share", ["GBP", "GBp", "USD"])
                with right:
                    fee = st.text_input("Execution fee / major settlement currency; enter 0 if none")
        elif action == "Correction":
            target = st.text_input("Earlier event ID to void")
            reason = st.text_input("Correction reason", max_chars=200)
            st.caption("A correction adds a void record; it never deletes history. Unsafe replay is rejected.")
        else:
            amount = st.text_input("Amount / exact major cash currency")
            if action == "Fee":
                related = st.text_input("Related trade event ID / optional")
            if action == "Cash exchange":
                st.caption("Rate multiplies source cash to the other cash currency. Both cash openings required.")
                left, right = st.columns(2)
                with left:
                    rate = st.text_input("Explicit source → target FX rate")
                with right:
                    rate_time = st.text_input("FX observed at / ISO timestamp with UTC offset")
                left, right = st.columns(2)
                with left:
                    fee = st.text_input("Exchange fee / enter 0 if none")
                with right:
                    fee_currency = st.selectbox("Exchange fee currency", ["GBP", "USD"])
        submitted = st.form_submit_button("Append manual ledger record", type="primary")
    if not submitted:
        if flash is not None:
            repeat, entry = flash
            if repeat:
                st.info(
                    "Identical to the record just appended — not duplicated. To record a genuinely "
                    "separate identical event, change its source declaration (e.g. add a reference)."
                )
            else:
                st.success("Manual record appended. No order was placed.")
            readout(entry)
        return
    try:
        at = timestamp_input(effective)
        if instrument_action:
            instrument = p.Instrument(symbol.strip().upper(), market, currency)
            if action == "Opening holding":
                event = p.OpeningHolding(
                    at, instrument, quantity, decimal_input(cost), date.fromisoformat(acquired), source
                )
            else:
                cls = p.Buy if action == "Executed buy" else p.Sell
                event = cls(at, instrument, quantity, decimal_input(price), unit,
                            decimal_input(fee), currency, source)
        elif action == "Correction":
            event = p.Correction(at, target, reason, source)
        elif action == "Cash exchange":
            fx = FxRate(
                currency, "USD" if currency == "GBP" else "GBP", decimal_input(rate),
                timestamp_input(rate_time), ManualProvenance(source, datetime.now(UTC)),
            )
            event = p.CashExchange(at, decimal_input(amount), fx, decimal_input(fee),
                                   fee_currency, source)
        elif action == "Fee":
            event = p.Fee(at, currency, decimal_input(amount), source, related or None)
        else:
            cls = {"Cash opening": p.CashOpening, "Cash deposit": p.CashDeposit,
                   "Cash withdrawal": p.CashWithdrawal}[action]
            event = cls(at, currency, decimal_input(amount), source)
        # Streamlit forms keep their values after submit, so a second click (or a
        # retry after an unclear outcome) would previously append a distinct copy
        # under a fresh UUID. Reusing the UUID for an identical resubmission makes
        # the store return the existing record instead (its documented retry path).
        previous = st.session_state.get(pending_key)
        repeat = previous is not None and previous[1] == event
        event_id = previous[0] if repeat else uuid4().hex
        entry = store.append(event, event_id=event_id)
        st.session_state[pending_key] = (event_id, event)
        # Rerun so every panel (KPIs, native accounting, ledger) reads the new record;
        # the confirmation and readout are shown on that fresh run.
        st.session_state[_flash_key(root)] = (repeat, entry)
        st.rerun()
    except Exception:
        failure("Ledger append")


def _valuation_charts(result) -> None:
    holdings = result.holdings
    if not holdings:
        empty("No open holdings to value.", hint="Append an Opening holding or Executed buy record.")
        return
    left, right = st.columns(2)
    with left:
        valued = [h for h in holdings if h.unrealized_gbp is not None]
        rows = [{"label": _name(h.instrument), "value": h.unrealized_gbp,
                 "shown": f"{fmt_money(h.unrealized_gbp, 'GBP', signed=True)} "
                          f"(cost {fmt_money(h.cost_gbp, 'GBP')} → {fmt_money(h.market_value_gbp, 'GBP')})"}
                for h in valued]
        if not charts.show(charts.dots(rows, text="shown", x_title="GBP",
                                       title="Unrealised vs cost · GBP-valued holdings"),
                           alt_text="Dot plot of unrealised GBP profit or loss per valued holding",
                           key="edge_portfolio_unrealised_chart"):
            empty("No holding is valued in GBP yet.",
                  hint="Add a fresh manual quote per holding (and USD → GBP FX for USD lines).")
    with right:
        if all(h.market_value_gbp is not None for h in holdings):
            charts.show(charts.donut(
                [{"label": _name(h.instrument), "value": h.market_value_gbp,
                  "shown": fmt_money(h.market_value_gbp, "GBP")} for h in holdings],
                detail="shown", title="Allocation by market value · GBP",
                center=fmt_money(result.holdings_total_gbp, "GBP", places=0)),
                alt_text="Donut chart of holdings by GBP market value", height=250,
                key="edge_portfolio_allocation_chart")
        else:
            empty("Allocation needs every holding valued.",
                  hint="Missing or stale quotes keep allocation unavailable rather than partial.")


def _inputs(book: _Book, root: Path) -> None:
    instruments = tuple(dict.fromkeys(lot.instrument for lot in book.state.lots)) if book.state else ()
    prefix, quotes, _fx_rates = _session(root)
    st.caption(
        "Valuation observations stay in this session. Manual quote/FX source labels "
        "do not verify identity or freshness. Missing values stay unavailable, never zero."
    )
    if instruments:
        with st.form("edge_portfolio_quote", enter_to_submit=False):
            left, middle, right = st.columns([2, 1, 1])
            with left:
                instrument = st.selectbox(
                    "Holding to value", instruments,
                    format_func=lambda item: f"{item.market}:{item.symbol} / {item.currency}",
                )
            with middle:
                amount = st.text_input("Manual valuation price")
            with right:
                unit = st.selectbox("Price unit / per share", ["GBP", "GBp", "USD"])
            left, right = st.columns(2)
            with left:
                observed = st.text_input("Price observed at / ISO timestamp with UTC offset")
            with right:
                reference = st.text_input("Price source / user declaration", max_chars=200)
            submit = st.form_submit_button("Use manual quote for this session")
        if submit:
            try:
                quote = PriceQuote(
                    decimal_input(amount), unit, timestamp_input(observed),
                    ManualProvenance(reference, datetime.now(UTC)),
                )
                if quote.per_share_money().currency != instrument.currency:
                    raise ValueError
                quotes = dict(quotes)
                quotes[instrument] = quote
                st.session_state[prefix] = quotes
                st.rerun()  # the snapshot panels value with the new quote on a fresh run
            except Exception:
                failure("Manual quote")
    else:
        empty("No open holdings to quote.", hint="Append an Opening holding or Executed buy first.")
    with st.expander("Explicit USD → GBP valuation rate / optional"):
        with st.form("edge_portfolio_fx", enter_to_submit=False):
            left, right = st.columns(2)
            with left:
                rate = st.text_input("GBP per USD / exact decimal")
            with right:
                observed = st.text_input("FX observed at / ISO timestamp with UTC offset")
            reference = st.text_input("FX source / user declaration", max_chars=200)
            submit = st.form_submit_button("Use valuation FX for this session")
        if submit:
            try:
                fx_rates = (FxRate(
                    "USD", "GBP", decimal_input(rate), timestamp_input(observed),
                    ManualProvenance(reference, datetime.now(UTC)),
                ),)
                st.session_state[f"{prefix}_fx"] = fx_rates
                st.rerun()  # the snapshot panels value with the new rate on a fresh run
            except Exception:
                failure("Manual FX")
    st.number_input("Maximum observation age (seconds)", 1, 2592000, 86400,
                    key="edge_portfolio_max_age")


def _snapshot(book: _Book) -> None:
    result, age = book.snap, book.age
    if result is None:
        _unreadable()
        return
    if result.readiness.reasons:
        st.warning("Incomplete valuation — " + " ".join(result.readiness.reasons))
    tiles([
        Tile("Equity · GBP", fmt_money(result.equity_total_gbp, "GBP"), "Cash + holdings", "amber"),
        Tile("Holdings · GBP", fmt_money(result.holdings_total_gbp, "GBP"),
             f"Cost {fmt_money(result.cost_total_gbp, 'GBP')}", "ink"),
        Tile("Cash · GBP total", fmt_money(result.cash_total_gbp, "GBP"),
             "Both cash currencies in GBP", "cyan"),
        Tile("Unrealised · GBP", fmt_money(result.unrealized_total_gbp, "GBP", signed=True),
             "Snapshot FX", tone_for(result.unrealized_total_gbp)),
        Tile("Realised · GBP", fmt_money(result.realized_total_gbp, "GBP", signed=True),
             "FIFO sale P&L", tone_for(result.realized_total_gbp)),
        Tile("Standalone fees · GBP", fmt_money(result.standalone_fees_total_gbp, "GBP"),
             "Separate from sale P&L", "ink"),
    ], columns=6)
    fx = result.fx_rates[0] if result.fx_rates else None
    kv_grid([
        ("Native cash · GBP", _cash(result.cash, "GBP")),
        ("Native cash · USD", _cash(result.cash, "USD")),
        ("Realised · GBP lines", fmt_money(result.realized.get("GBP"), "GBP", signed=True)),
        ("Realised · USD lines", fmt_money(result.realized.get("USD"), "USD", signed=True)),
        ("USD → GBP rate", f"{fx.rate} · observed {fmt_time(fx.as_of)}" if fx else UNAVAILABLE),
        ("Maximum observation age", f"{int(age):,} s"),
    ], columns=2)
    with st.expander("Full snapshot and limitations"):
        st.caption("Exact recorded field values (audit text).")
        for name in ("cash_total_gbp", "holdings_total_gbp", "equity_total_gbp",
                     "unrealized_total_gbp", "realized_total_gbp"):
            value = getattr(result, name)
            st.text(f"{name}: {'Unavailable' if value is None else str(value) + ' GBP'}")
        for currency in ("GBP", "USD"):
            st.text(f"{currency}: {result.cash.get(currency, 'Not declared')}")
        readout(result)
        st.caption("Snapshot FX is not historical tax basis or FX return attribution.")


def _holdings(book: _Book, ctx: PanelContext) -> None:
    result = book.snap
    if result is None:
        _unreadable()
        return
    if result.holdings and ctx.view != "charts":
        st.dataframe([{
            "Instrument": _name(h.instrument), "Ccy": h.instrument.currency, "Qty": h.quantity,
            "Cost": fmt_money(h.cost, h.instrument.currency),
            "Market value": fmt_money(h.market_value, h.instrument.currency),
            "Unrealised": fmt_money(h.unrealized, h.instrument.currency, signed=True),
            "Cost · GBP": fmt_money(h.cost_gbp, "GBP"),
            "Value · GBP": fmt_money(h.market_value_gbp, "GBP"),
            "Unrealised · GBP": fmt_money(h.unrealized_gbp, "GBP", signed=True),
            "Quote": fmt_money(h.quote.amount, h.quote.unit, places=4) if h.quote else "No quote",
            "Quote observed": fmt_time(h.quote.as_of) if h.quote else UNAVAILABLE,
            "Quote source": h.quote.provenance.reference if h.quote else UNAVAILABLE,
        } for h in result.holdings], hide_index=True, width="stretch")
    if ctx.view != "table" or not result.holdings:
        _valuation_charts(result)


def _import(data_root: Path, flash) -> None:
    from edge.dashboard_manual_csv import render_import

    preview = render_import(f"edge_portfolio_csv_{data_root.resolve()}", ("Prices", "FX"))
    if preview is None:
        return
    panel("APL", "Apply one imported observation", meta="Explicit selection · session-only",
          badges=("manual",))
    index = st.number_input(
        "Imported observation row / explicit selection", 1, len(preview.observations), 1,
    )
    selected = preview.observations[index - 1]
    readout(selected)
    if st.button("Apply selected observation to session valuation", key="edge_portfolio_apply_csv"):
        prefix = f"edge_portfolio_observations_{data_root.resolve()}"
        if preview.kind == "Prices":
            quotes = dict(st.session_state.get(prefix, {}))
            quotes[preview.instrument] = selected.close
            st.session_state[prefix] = quotes
        elif (selected.rate.from_currency, selected.rate.to_currency) == ("USD", "GBP"):
            st.session_state[f"{prefix}_fx"] = (selected.rate,)
        else:
            st.error("Valuation requires an explicit USD → GBP rate. Reverse rates are not inverted.")
            return
        # Rerun so the snapshot panels value with the applied observation.
        st.session_state[_flash_key(data_root)] = "applied"
        st.rerun()
    if flash == "applied":
        st.success("Selected manual observation applied to this session. Choose Valuation; no cash moved.")


def _flash_key(root: Path) -> str:
    return f"edge_portfolio_flash_{root.resolve()}"


def _task(store: PortfolioStore, root: Path, book: _Book, task: str, flash) -> None:
    st.radio("Portfolio task", list(TASKS), horizontal=True, key="edge_portfolio_task")
    try:
        if task == "Append record":
            _event(store, root, flash if isinstance(flash, tuple) else None)
        elif task == "Import CSV":
            _import(root, flash)
        elif task == "Valuation":
            _inputs(book, root)
        elif book.entries == ():
            st.info("No manual ledger yet. Choose Append record to declare cash and opening holdings.")
        else:
            st.caption("Read-only review: the native accounting, timeline and ledger panels show "
                       "recorded events. Choose another task to append, value or import.")
    except Exception:
        failure("Portfolio read")


def panels(store: PortfolioStore, root: Path, task: str, flash, book: _Book) -> list[Panel]:
    """Portfolio panels; ``book`` is read before layout so every panel shows the
    same recorded values whatever the arrangement."""
    code, title, meta = TASK_HEADS[task]
    snap, state = book.snap, book.state
    stale = snap is not None and bool(snap.readiness.reasons)
    return [
        Panel("kpis", "SUM", "Portfolio summary", lambda c: _summary(snap), w=12, h=3, min_w=4,
              min_h=2, fit=True, framed=False),
        Panel("task", code, title, lambda c: _task(store, root, book, task, flash), w=12, h=10,
              min_w=4, fit=True, meta=meta, badges=("manual",)),
        Panel("native", "NAT", "Native accounting / no market prices", lambda c: _native(book),
              w=12, h=10, min_w=4, fit=True, badges=("manual",),
              meta=f"Last effective {fmt_time(state.last_effective_at)}" if state else UNAVAILABLE),
        Panel("snapshot", "VAL", "GBP reporting / manual snapshot", lambda c: _snapshot(book),
              w=12, h=9, min_w=4, fit=True,
              meta=f"As of {fmt_time(snap.as_of)}" if snap else UNAVAILABLE,
              badges=("manual", "stale") if stale else ("manual",)),
        Panel("holdings", "HLD", "Holding evidence / manual identity",
              lambda c: _holdings(book, c), w=12, h=14, min_w=4, fit=True, badges=("manual",),
              meta=f"{len(snap.holdings)} holding(s)" if snap else UNAVAILABLE,
              views=(("both", "Table + charts"), ("table", "Table"), ("charts", "Charts"))),
        Panel("timeline", "TML", "Ledger timeline", lambda c: _timeline(book, c), w=12, h=10,
              min_w=4, min_h=6, badges=("manual",),
              meta=f"{len(book.entries)} records by effective time" if book.entries else "",
              views=(("both", "Timeline + cumulative"), ("timeline", "Event timeline"),
                     ("steps", "Cumulative records"))),
        Panel("realised", "RLZ", "Realised P&L per FIFO sale", lambda c: _realised(book, c),
              w=12, h=8, min_w=4, fit=True, badges=("manual",), meta="GBP and USD lines kept separate",
              views=(("dots", "Dot plot"), ("table", "Table"))),
        Panel("records", "LDG", "Ledger records", lambda c: _records(book), w=12, h=10, min_w=4,
              fit=True, badges=("manual",), meta="Immutable · newest last"),
    ]


def render(data_root: Path) -> None:
    boundary("Manual portfolio / GBP reporting")
    store = PortfolioStore(data_root)
    task = st.session_state.get("edge_portfolio_task", TASKS[0])
    if task not in TASKS:
        task = TASKS[0]
    flash = st.session_state.pop(_flash_key(data_root), None)
    book = _load(store, data_root)
    if book.state is None or book.snap is None:
        failure("Portfolio read")
    grid("portfolio", panels(store, data_root, task, flash, book))
