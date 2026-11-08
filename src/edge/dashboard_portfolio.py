"""Manual ledger and explicitly dated valuation; never simulated positions."""

# ruff: noqa: BLE001 -- fixed UI errors must not leak local records or SQL.
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import streamlit as st

from edge import portfolio as p
from edge.dashboard_manual import (
    boundary,
    decimal_input,
    failure,
    readout,
    timestamp_input,
)
from edge.decision_contracts import FxRate, ManualProvenance, PriceQuote
from edge.portfolio_store import PortfolioStore


def _event(store: PortfolioStore) -> None:
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
        effective = st.text_input("Effective at / ISO timestamp with UTC offset")
        source = st.text_input("Record source / user declaration", max_chars=200)
        currency = st.selectbox("Cash / settlement currency", ["GBP", "USD"])
        instrument_action = action in ("Opening holding", "Executed buy", "Executed sell")
        if instrument_action:
            symbol = st.text_input("Ticker / identity unverified", max_chars=32)
            market = st.selectbox("Declared market", ["UK", "US"])
            quantity = st.number_input("Quantity / whole shares", min_value=1, value=1, step=1)
            if action == "Opening holding":
                acquired = st.text_input("Acquired on / YYYY-MM-DD")
                cost = st.text_input("Total opening cost / major settlement currency")
            else:
                price = st.text_input("Execution price / exact decimal")
                unit = st.selectbox("Execution price unit / per share", ["GBP", "GBp", "USD"])
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
                rate = st.text_input("Explicit source → target FX rate")
                rate_time = st.text_input("FX observed at / ISO timestamp with UTC offset")
                fee = st.text_input("Exchange fee / enter 0 if none")
                fee_currency = st.selectbox("Exchange fee currency", ["GBP", "USD"])
        submitted = st.form_submit_button("Append manual ledger record", type="primary")
    if not submitted:
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
        entry = store.append(event)
        st.success("Manual record appended. No order was placed.")
        readout(entry)
    except Exception:
        failure("Ledger append")


def _valuation(store: PortfolioStore, root: Path) -> None:
    state = store.state()
    instruments = tuple(dict.fromkeys(lot.instrument for lot in state.lots))
    prefix = f"edge_portfolio_observations_{root.resolve()}"
    quotes = st.session_state.get(prefix, {})
    fx_rates = st.session_state.get(f"{prefix}_fx", ())
    st.caption(
        "Valuation observations stay in this session. Manual quote/FX source labels "
        "do not verify identity or freshness. Missing values stay unavailable, never zero."
    )
    if instruments:
        with st.form("edge_portfolio_quote", enter_to_submit=False):
            instrument = st.selectbox(
                "Holding to value", instruments,
                format_func=lambda item: f"{item.market}:{item.symbol} / {item.currency}",
            )
            amount = st.text_input("Manual valuation price")
            unit = st.selectbox("Price unit / per share", ["GBP", "GBp", "USD"])
            observed = st.text_input("Price observed at / ISO timestamp with UTC offset")
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
            except Exception:
                failure("Manual quote")
    with st.expander("Explicit USD → GBP valuation rate / optional"):
        with st.form("edge_portfolio_fx", enter_to_submit=False):
            rate = st.text_input("GBP per USD / exact decimal")
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
            except Exception:
                failure("Manual FX")
    age = st.number_input("Maximum observation age (seconds)", 1, 2592000, 86400,
                          key="edge_portfolio_max_age")
    result = store.snapshot(
        quotes=quotes, fx_rates=fx_rates, as_of=datetime.now(UTC),
        max_age=timedelta(seconds=age),
    )
    st.subheader("GBP reporting / manual snapshot")
    if result.readiness.reasons:
        st.warning("Incomplete valuation — " + " ".join(result.readiness.reasons))
    for name in ("cash_total_gbp", "holdings_total_gbp", "equity_total_gbp",
                 "unrealized_total_gbp", "realized_total_gbp"):
        value = getattr(result, name)
        st.text(f"{name}: {'Unavailable' if value is None else str(value) + ' GBP'}")
    st.subheader("Separate native cash balances")
    for currency in ("GBP", "USD"):
        st.text(f"{currency}: {result.cash.get(currency, 'Not declared')}")
    st.subheader("Holding evidence / manual identity")
    for holding in result.holdings:
        readout(holding)
    with st.expander("Full snapshot and limitations"):
        readout(result)
        st.caption("Snapshot FX is not historical tax basis or FX return attribution.")


def render(data_root: Path) -> None:
    boundary("Manual portfolio / GBP reporting")
    store = PortfolioStore(data_root)
    task = st.radio("Portfolio task", ["Ledger", "Append record", "Valuation", "Import CSV"],
                    horizontal=True, key="edge_portfolio_task")
    try:
        if task == "Append record":
            _event(store)
        elif task == "Import CSV":
            from edge.dashboard_manual_csv import render_import

            preview = render_import(f"edge_portfolio_csv_{data_root.resolve()}", ("Prices", "FX"))
            if preview is not None:
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
                    st.success("Selected manual observation applied to this session. Choose Valuation; no cash moved.")
        elif task == "Valuation":
            _valuation(store, data_root)
        else:
            entries = store.list_entries()
            if not entries:
                st.info("No manual ledger yet. Choose Append record to declare cash and opening holdings.")
            st.subheader("Native accounting / no market prices")
            readout(store.state())
            with st.expander("Immutable ledger records / newest last"):
                for entry in entries:
                    readout(entry)
    except Exception:
        failure("Portfolio read")
